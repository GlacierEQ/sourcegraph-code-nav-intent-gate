#!/usr/bin/env python3
"""Executable bounded-navigation proof."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from code_nav_intent_gate import CodeNavIntentGate, CodeNavIntentGateRequest, Decision


def main() -> int:
    graph = {
        "web.route": ["controller.handle", "telemetry.emit"],
        "controller.handle": ["service.resolve"],
        "service.resolve": ["repo.fetch"],
        "repo.fetch": ["database.query"],
        "database.query": [],
        "telemetry.emit": [],
    }
    gate = CodeNavIntentGate(clock=lambda: 1_000.0)
    request = CodeNavIntentGateRequest(
        subject_id="operate-demo",
        payload={
            "graph": graph,
            "intent": "PATH",
            "start": "web.route",
            "goal": "database.query",
            "max_hops": 5,
            "max_nodes": 16,
        },
        budget=20.0,
        grant_id="demo-grant",
        not_after=2_000.0,
    )
    receipt = gate.evaluate(request)
    print(json.dumps(receipt.as_dict(), indent=2, sort_keys=True))
    if receipt.decision is not Decision.ALLOW:
        return 2
    if receipt.result["path"] != [
        "web.route",
        "controller.handle",
        "service.resolve",
        "repo.fetch",
        "database.query",
    ]:
        return 3
    if not gate.verify_receipt(receipt):
        return 4
    if receipt.metrics["work_units"] > receipt.metrics["budget_units"]:
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
