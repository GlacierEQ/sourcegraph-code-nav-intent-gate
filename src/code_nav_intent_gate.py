"""Deterministic intent-bounded code graph navigation.

A caller supplies a code-intelligence graph plus an explicit navigation intent.
The engine refuses unbounded walks, enforces repository/edge/hop/node budgets,
performs deterministic breadth-first traversal, and emits auditable paths and
coverage receipts.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


class Decision(str, Enum):
    ALLOW = "ALLOW"
    REFUSE = "REFUSE"


@dataclass(frozen=True)
class CodeNavIntentGateRequest:
    subject_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    budget: float = 1.0
    grant_id: str | None = None
    not_after: float | None = None


@dataclass(frozen=True)
class CodeNavIntentGateReceipt:
    decision: Decision
    reasons: tuple[str, ...]
    digest: str
    metrics: dict[str, Any] = field(default_factory=dict)
    graph_digest: str | None = None
    intent_digest: str | None = None
    visited_nodes: tuple[str, ...] = ()
    traversed_edges: tuple[dict[str, str], ...] = ()
    target_paths: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reasons": list(self.reasons),
            "digest": self.digest,
            "metrics": self.metrics,
            "graph_digest": self.graph_digest,
            "intent_digest": self.intent_digest,
            "visited_nodes": list(self.visited_nodes),
            "traversed_edges": list(self.traversed_edges),
            "target_paths": list(self.target_paths),
        }


class NavigationSchemaError(ValueError):
    pass


class CodeNavIntentGate:
    """Bound code graph navigation to declared intent and resource limits."""

    MIN_BUDGET = 0.0
    DIRECTIONS = {"outgoing", "incoming", "both"}

    @staticmethod
    def _names(value: Any, field_name: str, *, required: bool = False) -> list[str]:
        if value is None:
            if required:
                raise NavigationSchemaError(f"{field_name}_missing")
            return []
        if not isinstance(value, list):
            raise NavigationSchemaError(f"{field_name}_not_list")
        names: set[str] = set()
        for raw in value:
            name = str(raw).strip()
            if not name:
                raise NavigationSchemaError(f"{field_name}_contains_empty_name")
            names.add(name)
        if required and not names:
            raise NavigationSchemaError(f"{field_name}_empty")
        return sorted(names)

    @classmethod
    def _normalize_graph(cls, graph: Any) -> dict[str, Any]:
        if not isinstance(graph, dict):
            raise NavigationSchemaError("graph_not_object")
        raw_nodes = graph.get("nodes")
        raw_edges = graph.get("edges")
        if not isinstance(raw_nodes, list) or not raw_nodes:
            raise NavigationSchemaError("graph_nodes_missing")
        if not isinstance(raw_edges, list):
            raise NavigationSchemaError("graph_edges_not_list")

        nodes: list[dict[str, str]] = []
        node_ids: set[str] = set()
        for index, raw in enumerate(raw_nodes):
            if not isinstance(raw, dict):
                raise NavigationSchemaError(f"node_{index}_not_object")
            node_id = str(raw.get("id", "")).strip()
            repository = str(raw.get("repository", "")).strip()
            if not node_id:
                raise NavigationSchemaError(f"node_{index}_id_missing")
            if node_id in node_ids:
                raise NavigationSchemaError(f"node_duplicate:{node_id}")
            if not repository:
                raise NavigationSchemaError(f"node_{node_id}_repository_missing")
            node = {
                "id": node_id,
                "repository": repository,
                "kind": str(raw.get("kind", "symbol")).strip() or "symbol",
                "path": str(raw.get("path", "")).strip(),
            }
            nodes.append(node)
            node_ids.add(node_id)

        edges: list[dict[str, str]] = []
        seen_edges: set[tuple[str, str, str]] = set()
        for index, raw in enumerate(raw_edges):
            if not isinstance(raw, dict):
                raise NavigationSchemaError(f"edge_{index}_not_object")
            source = str(raw.get("from", "")).strip()
            target = str(raw.get("to", "")).strip()
            kind = str(raw.get("kind", "")).strip()
            if source not in node_ids or target not in node_ids:
                raise NavigationSchemaError(f"edge_{index}_references_unknown_node")
            if not kind:
                raise NavigationSchemaError(f"edge_{index}_kind_missing")
            identity = (source, target, kind)
            if identity in seen_edges:
                continue
            seen_edges.add(identity)
            edges.append({"from": source, "to": target, "kind": kind})

        return {
            "nodes": sorted(nodes, key=lambda item: item["id"]),
            "edges": sorted(
                edges,
                key=lambda item: (item["from"], item["to"], item["kind"]),
            ),
        }

    @classmethod
    def _normalize_intent(cls, intent: Any) -> dict[str, Any]:
        if not isinstance(intent, dict):
            raise NavigationSchemaError("intent_not_object")
        start_nodes = cls._names(intent.get("start_nodes"), "start_nodes", required=True)
        target_nodes = cls._names(intent.get("target_nodes"), "target_nodes")
        edge_kinds = cls._names(
            intent.get("allowed_edge_kinds"),
            "allowed_edge_kinds",
            required=True,
        )
        repositories = cls._names(
            intent.get("allowed_repositories"),
            "allowed_repositories",
            required=True,
        )
        direction = str(intent.get("direction", "outgoing")).strip().lower()
        if direction not in cls.DIRECTIONS:
            raise NavigationSchemaError("direction_invalid")
        try:
            max_hops = int(intent["max_hops"])
            max_nodes = int(intent["max_nodes"])
        except (KeyError, TypeError, ValueError) as exc:
            raise NavigationSchemaError("navigation_budget_missing_or_invalid") from exc
        if max_hops < 0:
            raise NavigationSchemaError("max_hops_negative")
        if max_nodes <= 0:
            raise NavigationSchemaError("max_nodes_non_positive")
        normalized = {
            "schema": "glaciereq.code-nav-intent.v1",
            "start_nodes": start_nodes,
            "target_nodes": target_nodes,
            "allowed_edge_kinds": edge_kinds,
            "allowed_repositories": repositories,
            "direction": direction,
            "max_hops": max_hops,
            "max_nodes": max_nodes,
            "require_target": bool(intent.get("require_target", bool(target_nodes))),
        }
        normalized["intent_digest"] = _digest(normalized)
        return normalized

    @staticmethod
    def _adjacency(
        graph: dict[str, Any],
        direction: str,
    ) -> dict[str, list[tuple[str, str, str, str]]]:
        adjacency: dict[str, list[tuple[str, str, str, str]]] = {
            node["id"]: [] for node in graph["nodes"]
        }
        for edge in graph["edges"]:
            source = edge["from"]
            target = edge["to"]
            kind = edge["kind"]
            if direction in {"outgoing", "both"}:
                adjacency[source].append((target, kind, source, target))
            if direction in {"incoming", "both"}:
                adjacency[target].append((source, kind, source, target))
        for neighbors in adjacency.values():
            neighbors.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
        return adjacency

    @staticmethod
    def _reconstruct_path(
        target: str,
        parent: dict[str, tuple[str, dict[str, str]] | None],
    ) -> dict[str, Any]:
        nodes = [target]
        edges: list[dict[str, str]] = []
        current = target
        while parent[current] is not None:
            previous, edge = parent[current]  # type: ignore[misc]
            edges.append(edge)
            nodes.append(previous)
            current = previous
        nodes.reverse()
        edges.reverse()
        return {"target": target, "nodes": nodes, "edges": edges, "hops": len(edges)}

    def evaluate(
        self,
        req: CodeNavIntentGateRequest,
        *,
        now: float | None = None,
    ) -> CodeNavIntentGateReceipt:
        reasons: list[str] = []
        if not str(req.subject_id or "").strip():
            reasons.append("subject_id_missing")
        if req.budget <= self.MIN_BUDGET:
            reasons.append("budget_non_positive")
        payload = req.payload if isinstance(req.payload, dict) else {}
        if not isinstance(req.payload, dict):
            reasons.append("payload_not_object")

        graph: dict[str, Any] | None = None
        intent: dict[str, Any] | None = None
        try:
            graph = self._normalize_graph(payload.get("graph"))
        except NavigationSchemaError as exc:
            reasons.append(str(exc))
        try:
            intent = self._normalize_intent(payload.get("intent"))
        except NavigationSchemaError as exc:
            reasons.append(str(exc))

        graph_digest = _digest(graph) if graph is not None else None
        intent_digest = intent["intent_digest"] if intent is not None else None
        visited: list[str] = []
        traversed: list[dict[str, str]] = []
        target_paths: list[dict[str, Any]] = []
        skipped_repo = 0
        skipped_edge_kind = 0
        max_depth_reached = 0
        at = time.time() if now is None else float(now)

        if req.not_after is not None and at > float(req.not_after):
            reasons.append("request_expired")

        if graph is not None and intent is not None:
            expected = str(payload.get("expected_intent_digest", "")).strip()
            if expected and expected != intent_digest:
                reasons.append("intent_digest_mismatch")

            if intent["max_nodes"] > math.floor(req.budget):
                reasons.append("intent_node_budget_exceeds_request")

            node_by_id = {node["id"]: node for node in graph["nodes"]}
            for node_id in intent["start_nodes"]:
                if node_id not in node_by_id:
                    reasons.append(f"start_node_unknown:{node_id}")
                elif node_by_id[node_id]["repository"] not in intent["allowed_repositories"]:
                    reasons.append(f"start_node_repository_not_allowed:{node_id}")
            for node_id in intent["target_nodes"]:
                if node_id not in node_by_id:
                    reasons.append(f"target_node_unknown:{node_id}")

            if not reasons:
                adjacency = self._adjacency(graph, intent["direction"])
                queue: deque[tuple[str, int]] = deque()
                parent: dict[str, tuple[str, dict[str, str]] | None] = {}
                depth_by_node: dict[str, int] = {}
                for start in intent["start_nodes"]:
                    queue.append((start, 0))
                    parent[start] = None
                    depth_by_node[start] = 0

                target_set = set(intent["target_nodes"])
                found_targets: set[str] = set()
                budget_exhausted = False
                while queue:
                    node_id, depth = queue.popleft()
                    if node_id in visited:
                        continue
                    if len(visited) >= intent["max_nodes"]:
                        budget_exhausted = True
                        break
                    visited.append(node_id)
                    max_depth_reached = max(max_depth_reached, depth)
                    if node_id in target_set:
                        found_targets.add(node_id)
                    if depth >= intent["max_hops"]:
                        continue

                    for neighbor, edge_kind, source, target in adjacency[node_id]:
                        if edge_kind not in intent["allowed_edge_kinds"]:
                            skipped_edge_kind += 1
                            continue
                        neighbor_node = node_by_id[neighbor]
                        if neighbor_node["repository"] not in intent["allowed_repositories"]:
                            skipped_repo += 1
                            continue
                        edge_receipt = {"from": source, "to": target, "kind": edge_kind}
                        traversed.append(edge_receipt)
                        if neighbor not in parent:
                            parent[neighbor] = (node_id, edge_receipt)
                            depth_by_node[neighbor] = depth + 1
                            queue.append((neighbor, depth + 1))

                for target in sorted(found_targets):
                    target_paths.append(self._reconstruct_path(target, parent))
                if budget_exhausted:
                    reasons.append("node_budget_exhausted")
                if intent["require_target"] and target_set and not found_targets:
                    reasons.append("required_target_not_reached")

        decision = Decision.REFUSE if reasons else Decision.ALLOW
        if not reasons:
            reasons = ["navigation_completed_within_intent"]
        traversed_unique = sorted(
            {tuple(sorted(edge.items())) for edge in traversed},
            key=str,
        )
        traversed_edges = [dict(items) for items in traversed_unique]
        body = {
            "schema": "glaciereq.code-nav-evaluation.v1",
            "subject_id": req.subject_id,
            "graph_digest": graph_digest,
            "intent_digest": intent_digest,
            "decision": decision.value,
            "reasons": reasons,
            "visited_nodes": visited,
            "traversed_edges": traversed_edges,
            "target_paths": target_paths,
        }
        return CodeNavIntentGateReceipt(
            decision=decision,
            reasons=tuple(reasons),
            digest=_digest(body),
            metrics={
                "visited_node_count": len(visited),
                "traversed_edge_count": len(traversed_edges),
                "target_count": len(target_paths),
                "max_depth_reached": max_depth_reached,
                "skipped_repository_edges": skipped_repo,
                "skipped_edge_kind_edges": skipped_edge_kind,
                "request_node_budget": math.floor(req.budget) if req.budget > 0 else 0,
            },
            graph_digest=graph_digest,
            intent_digest=intent_digest,
            visited_nodes=tuple(visited),
            traversed_edges=tuple(traversed_edges),
            target_paths=tuple(target_paths),
        )


Mechanism = CodeNavIntentGate
