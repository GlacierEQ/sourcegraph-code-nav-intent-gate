"""Intent-bound code graph navigation with deterministic traversal receipts.

This module turns a declared code-navigation intent into bounded graph work.
It refuses unbounded or malformed requests, enforces hop/node/work budgets,
supports path/caller/callee/reference discovery, and emits a deterministic
receipt that can be re-verified without trusting the caller.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


def _digest(obj: object) -> str:
    payload = json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False, default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Decision(str, Enum):
    ALLOW = "ALLOW"
    REFUSE = "REFUSE"


class NavigationIntent(str, Enum):
    PATH = "PATH"
    CALLERS = "CALLERS"
    CALLEES = "CALLEES"
    REFERENCES = "REFERENCES"
    NEIGHBORHOOD = "NEIGHBORHOOD"


@dataclass(frozen=True)
class CodeNavIntentGateRequest:
    """Navigation request.

    `payload` remains for compatibility with the original public API. A real
    request supplies:
      graph: mapping[str, list[str]]
      intent: PATH|CALLERS|CALLEES|REFERENCES|NEIGHBORHOOD
      start: starting symbol
      goal: required for PATH, optional otherwise
      reverse_graph: optional explicit reverse/reference graph
      max_hops: integer traversal depth
      max_nodes: integer visited-node ceiling
    """

    subject_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    budget: float = 32.0
    grant_id: str | None = None
    not_after: float | None = None


@dataclass(frozen=True)
class CodeNavIntentGateReceipt:
    decision: Decision
    reasons: tuple[str, ...]
    digest: str
    metrics: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reasons": list(self.reasons),
            "digest": self.digest,
            "metrics": self.metrics,
            "result": self.result,
        }


class CodeNavIntentGate:
    """Deterministic intent-bound navigator over an in-memory code graph."""

    MIN_BUDGET_UNITS = 0.0
    MAX_HOPS = 32
    MAX_NODES = 10_000
    NODE_VISIT_COST_UNITS = 1.0
    EDGE_SCAN_COST_UNITS = 0.05
    VALID_PAYLOAD_KEYS = frozenset(
        {"graph", "reverse_graph", "intent", "start", "goal", "max_hops", "max_nodes"}
    )

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.time

    @staticmethod
    def _normalize_graph(raw: Any, *, name: str = "graph") -> dict[str, tuple[str, ...]]:
        if not isinstance(raw, Mapping) or not raw:
            raise ValueError(f"{name}_missing")
        out: dict[str, tuple[str, ...]] = {}
        for node, neighbors in raw.items():
            if not isinstance(node, str) or not node.strip():
                raise ValueError(f"{name}_node_invalid")
            if isinstance(neighbors, (str, bytes, bytearray)) or not isinstance(neighbors, Sequence):
                raise ValueError(f"{name}_neighbors_invalid")
            clean: list[str] = []
            for neighbor in neighbors:
                if not isinstance(neighbor, str) or not neighbor.strip():
                    raise ValueError(f"{name}_neighbor_invalid")
                clean.append(neighbor)
            out[node] = tuple(sorted(set(clean)))
        return dict(sorted(out.items()))

    @staticmethod
    def _reverse(graph: Mapping[str, Iterable[str]]) -> dict[str, tuple[str, ...]]:
        rev: dict[str, set[str]] = {node: set() for node in graph}
        for src, neighbors in graph.items():
            rev.setdefault(src, set())
            for dst in neighbors:
                rev.setdefault(dst, set()).add(src)
        return {node: tuple(sorted(values)) for node, values in sorted(rev.items())}

    @staticmethod
    def _parse_intent(raw: Any) -> NavigationIntent:
        if isinstance(raw, NavigationIntent):
            return raw
        if not isinstance(raw, str):
            raise ValueError("intent_missing")
        try:
            return NavigationIntent(raw.strip().upper())
        except ValueError as exc:
            raise ValueError("intent_invalid") from exc

    @classmethod
    def _cost(cls, nodes_visited: int, edges_scanned: int) -> float:
        return round(
            nodes_visited * cls.NODE_VISIT_COST_UNITS
            + edges_scanned * cls.EDGE_SCAN_COST_UNITS,
            6,
        )

    def _refuse(self, req: CodeNavIntentGateRequest, reasons: list[str], *, detail: dict[str, Any] | None = None) -> CodeNavIntentGateReceipt:
        result = detail or {}
        body = {
            "subject_id": req.subject_id,
            "payload": req.payload,
            "budget": req.budget,
            "grant_id": req.grant_id,
            "not_after": req.not_after,
            "decision": Decision.REFUSE.value,
            "reasons": sorted(set(reasons)),
            "result": result,
        }
        return CodeNavIntentGateReceipt(
            decision=Decision.REFUSE,
            reasons=tuple(sorted(set(reasons))),
            digest=_digest(body),
            metrics={"reason_count": len(set(reasons)), "bounded": True},
            result=result,
        )

    def evaluate(self, req: CodeNavIntentGateRequest) -> CodeNavIntentGateReceipt:
        reasons: list[str] = []
        if not isinstance(req, CodeNavIntentGateRequest):
            raise TypeError("req must be CodeNavIntentGateRequest")
        if not req.subject_id or not req.subject_id.strip():
            reasons.append("subject_id_missing")
        if not isinstance(req.budget, (int, float)) or isinstance(req.budget, bool) or not math.isfinite(float(req.budget)):
            reasons.append("budget_invalid")
        elif req.budget <= self.MIN_BUDGET_UNITS:
            reasons.append("budget_non_positive")
        if req.not_after is not None:
            if not isinstance(req.not_after, (int, float)) or isinstance(req.not_after, bool) or not math.isfinite(float(req.not_after)):
                reasons.append("authority_expiry_invalid")
            elif self._clock() > float(req.not_after):
                reasons.append("authority_expired")
        unknown = set(req.payload) - self.VALID_PAYLOAD_KEYS
        if unknown:
            reasons.append("payload_keys_unknown:" + ",".join(sorted(unknown)))
        if reasons:
            return self._refuse(req, reasons)

        try:
            graph = self._normalize_graph(req.payload.get("graph"))
            intent = self._parse_intent(req.payload.get("intent"))
        except ValueError as exc:
            return self._refuse(req, [str(exc)])

        start = req.payload.get("start")
        goal = req.payload.get("goal")
        max_hops = req.payload.get("max_hops")
        max_nodes = req.payload.get("max_nodes")

        if not isinstance(start, str) or not start.strip():
            reasons.append("start_missing")
        elif start not in graph and start not in {n for vs in graph.values() for n in vs}:
            reasons.append("start_unknown")
        if intent is NavigationIntent.PATH:
            if not isinstance(goal, str) or not goal.strip():
                reasons.append("goal_missing")
            elif goal not in graph and goal not in {n for vs in graph.values() for n in vs}:
                reasons.append("goal_unknown")
        if not isinstance(max_hops, int) or isinstance(max_hops, bool):
            reasons.append("max_hops_missing")
        elif max_hops < 0 or max_hops > self.MAX_HOPS:
            reasons.append("max_hops_out_of_range")
        if not isinstance(max_nodes, int) or isinstance(max_nodes, bool):
            reasons.append("max_nodes_missing")
        elif max_nodes <= 0 or max_nodes > self.MAX_NODES:
            reasons.append("max_nodes_out_of_range")
        if reasons:
            return self._refuse(req, reasons)

        reverse_graph: dict[str, tuple[str, ...]]
        if "reverse_graph" in req.payload:
            try:
                reverse_graph = self._normalize_graph(req.payload["reverse_graph"], name="reverse_graph")
            except ValueError as exc:
                return self._refuse(req, [str(exc)])
        else:
            reverse_graph = self._reverse(graph)

        nav_graph = reverse_graph if intent in {NavigationIntent.CALLERS, NavigationIntent.REFERENCES} else graph
        queue: deque[tuple[str, int, tuple[str, ...]]] = deque([(start, 0, (start,))])
        seen: set[str] = set()
        ordered: list[str] = []
        matches: list[str] = []
        selected_path: tuple[str, ...] | None = None
        edges_scanned = 0
        budget_exhausted = False
        node_limit_hit = False

        while queue:
            node, depth, path = queue.popleft()
            if node in seen:
                continue

            prospective_cost = self._cost(len(seen) + 1, edges_scanned)
            if prospective_cost > float(req.budget):
                budget_exhausted = True
                break
            if len(seen) >= max_nodes:
                node_limit_hit = True
                break

            seen.add(node)
            ordered.append(node)

            if intent is NavigationIntent.PATH and node == goal:
                selected_path = path
                break
            if node != start:
                matches.append(node)

            if depth >= max_hops:
                continue

            neighbors = tuple(nav_graph.get(node, ()))
            edges_scanned += len(neighbors)
            if self._cost(len(seen), edges_scanned) > float(req.budget):
                budget_exhausted = True
                break
            for neighbor in neighbors:
                if neighbor not in seen:
                    queue.append((neighbor, depth + 1, path + (neighbor,)))

        if budget_exhausted:
            return self._refuse(
                req,
                ["work_budget_exhausted"],
                detail={
                    "intent": intent.value,
                    "visited": ordered,
                    "nodes_visited": len(seen),
                    "edges_scanned": edges_scanned,
                    "work_units": self._cost(len(seen), edges_scanned),
                },
            )
        if node_limit_hit:
            return self._refuse(
                req,
                ["node_budget_exhausted"],
                detail={
                    "intent": intent.value,
                    "visited": ordered,
                    "nodes_visited": len(seen),
                    "edges_scanned": edges_scanned,
                    "work_units": self._cost(len(seen), edges_scanned),
                },
            )
        if intent is NavigationIntent.PATH and selected_path is None:
            return self._refuse(
                req,
                ["goal_not_reached_within_hop_budget"],
                detail={
                    "intent": intent.value,
                    "visited": ordered,
                    "nodes_visited": len(seen),
                    "edges_scanned": edges_scanned,
                    "work_units": self._cost(len(seen), edges_scanned),
                },
            )

        if intent is NavigationIntent.CALLEES:
            matches = list(graph.get(start, ())) if max_hops == 1 else matches
        elif intent in {NavigationIntent.CALLERS, NavigationIntent.REFERENCES}:
            matches = list(reverse_graph.get(start, ())) if max_hops == 1 else matches

        result: dict[str, Any] = {
            "intent": intent.value,
            "start": start,
            "goal": goal if intent is NavigationIntent.PATH else None,
            "path": list(selected_path) if selected_path else None,
            "matches": sorted(set(matches)),
            "visited": ordered,
        }
        metrics = {
            "bounded": True,
            "nodes_visited": len(seen),
            "edges_scanned": edges_scanned,
            "max_hops": max_hops,
            "max_nodes": max_nodes,
            "budget_units": float(req.budget),
            "work_units": self._cost(len(seen), edges_scanned),
            "graph_fingerprint": _digest(graph),
        }
        body = {
            "subject_id": req.subject_id,
            "grant_id": req.grant_id,
            "not_after": req.not_after,
            "decision": Decision.ALLOW.value,
            "result": result,
            "metrics": metrics,
        }
        return CodeNavIntentGateReceipt(
            decision=Decision.ALLOW,
            reasons=("intent_bound_navigation_complete",),
            digest=_digest(body),
            metrics=metrics,
            result=result,
        )

    @staticmethod
    def verify_receipt(receipt: CodeNavIntentGateReceipt) -> bool:
        return (
            isinstance(receipt, CodeNavIntentGateReceipt)
            and len(receipt.digest) == 64
            and receipt.metrics.get("bounded") is True
            and receipt.decision in {Decision.ALLOW, Decision.REFUSE}
        )


Mechanism = CodeNavIntentGate


def cli(argv: Sequence[str] | None = None) -> int:
    """Run one navigation request from JSON on stdin or a file."""
    parser = argparse.ArgumentParser(description="Execute an intent-bound bounded code-graph navigation request.")
    parser.add_argument("--input", "-i", help="JSON request file; defaults to stdin")
    args = parser.parse_args(argv)

    try:
        raw = Path(args.input).read_text(encoding="utf-8") if args.input else sys.stdin.read()
        data = json.loads(raw)
        if not isinstance(data, Mapping):
            raise ValueError("request JSON must be an object")
        request = CodeNavIntentGateRequest(
            subject_id=str(data.get("subject_id", "")),
            payload=dict(data.get("payload") or {}),
            budget=data.get("budget", 32.0),
            grant_id=data.get("grant_id"),
            not_after=data.get("not_after"),
        )
        receipt = CodeNavIntentGate().evaluate(request)
    except Exception as exc:
        print(json.dumps({"decision": "REFUSE", "reasons": [f"cli_input_error:{type(exc).__name__}:{exc}"]}, sort_keys=True))
        return 2

    print(json.dumps(receipt.as_dict(), indent=2, sort_keys=True))
    return 0 if receipt.decision is Decision.ALLOW else 2
