#!/usr/bin/env python3
"""Run bounded code-graph navigation against declared intent."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from code_nav_intent_gate import CodeNavIntentGate, CodeNavIntentGateRequest, Decision


DEMO = {
    "subject_id": "code-nav-demo",
    "budget": 10,
    "graph": {
        "nodes": [
            {"id": "entry", "repository": "demo/app", "kind": "function", "path": "src/main.py"},
            {"id": "service", "repository": "demo/app", "kind": "function", "path": "src/service.py"},
            {"id": "store", "repository": "demo/app", "kind": "function", "path": "src/store.py"},
        ],
        "edges": [
            {"from": "entry", "to": "service", "kind": "calls"},
            {"from": "service", "to": "store", "kind": "calls"},
        ],
    },
    "intent": {
        "start_nodes": ["entry"],
        "target_nodes": ["store"],
        "allowed_edge_kinds": ["calls"],
        "allowed_repositories": ["demo/app"],
        "direction": "outgoing",
        "max_hops": 3,
        "max_nodes": 10,
        "require_target": True,
    },
}


def load_input(path: str | None) -> dict:
    if path is None:
        return DEMO
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("input JSON must be an object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Navigate a code graph within explicit intent and resource bounds"
    )
    parser.add_argument("--input", help="JSON request; built-in demo when omitted")
    parser.add_argument("--output", help="optional receipt output path")
    args = parser.parse_args()

    data = load_input(args.input)
    payload = {
        "graph": data.get("graph", {}),
        "intent": data.get("intent", {}),
    }
    if data.get("expected_intent_digest"):
        payload["expected_intent_digest"] = data["expected_intent_digest"]
    request = CodeNavIntentGateRequest(
        subject_id=str(data.get("subject_id", "")),
        payload=payload,
        budget=float(data.get("budget", 1.0)),
        grant_id=data.get("grant_id"),
        not_after=data.get("not_after"),
    )
    receipt = CodeNavIntentGate().evaluate(request)
    rendered = json.dumps(receipt.as_dict(), indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if receipt.decision is Decision.ALLOW else 2


if __name__ == "__main__":
    raise SystemExit(main())
