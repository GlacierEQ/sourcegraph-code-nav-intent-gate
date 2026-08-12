from __future__ import annotations

from code_nav_intent_gate import (
    CodeNavIntentGate,
    CodeNavIntentGateRequest,
    Decision,
    NavigationIntent,
)

GRAPH = {
    "api.handle": ["service.resolve", "audit.record"],
    "service.resolve": ["repo.lookup", "cache.get"],
    "repo.lookup": ["db.query"],
    "cache.get": [],
    "db.query": [],
    "audit.record": [],
}


def req(intent: str, **extra):
    payload = {
        "graph": GRAPH,
        "intent": intent,
        "start": "api.handle",
        "max_hops": 4,
        "max_nodes": 20,
        **extra,
    }
    return CodeNavIntentGateRequest(subject_id="navigation-1", payload=payload, budget=50)


def test_path_finds_shortest_bounded_route():
    gate = CodeNavIntentGate(clock=lambda: 100)
    r = gate.evaluate(req("PATH", goal="db.query"))
    assert r.decision is Decision.ALLOW
    assert r.result["path"] == ["api.handle", "service.resolve", "repo.lookup", "db.query"]
    assert r.metrics["nodes_visited"] <= r.metrics["max_nodes"]
    assert r.metrics["work_units"] <= r.metrics["budget_units"]
    assert gate.verify_receipt(r)


def test_callees_returns_neighbors():
    gate = CodeNavIntentGate()
    r = gate.evaluate(req("CALLEES", max_hops=1))
    assert r.decision is Decision.ALLOW
    assert r.result["matches"] == ["audit.record", "service.resolve"]


def test_callers_uses_reverse_graph():
    gate = CodeNavIntentGate()
    r = gate.evaluate(
        CodeNavIntentGateRequest(
            subject_id="callers",
            payload={
                "graph": GRAPH,
                "intent": "CALLERS",
                "start": "repo.lookup",
                "max_hops": 1,
                "max_nodes": 20,
            },
            budget=20,
        )
    )
    assert r.decision is Decision.ALLOW
    assert r.result["matches"] == ["service.resolve"]


def test_reference_walk_is_deterministic():
    gate = CodeNavIntentGate()
    q = CodeNavIntentGateRequest(
        subject_id="refs",
        payload={
            "graph": GRAPH,
            "intent": NavigationIntent.REFERENCES.value,
            "start": "db.query",
            "max_hops": 3,
            "max_nodes": 20,
        },
        budget=20,
    )
    a = gate.evaluate(q)
    b = gate.evaluate(q)
    assert a == b
    assert a.digest == b.digest


def test_refuse_unbounded_missing_hop_budget():
    gate = CodeNavIntentGate()
    r = gate.evaluate(
        CodeNavIntentGateRequest(
            subject_id="bad",
            payload={"graph": GRAPH, "intent": "NEIGHBORHOOD", "start": "api.handle", "max_nodes": 20},
            budget=50,
        )
    )
    assert r.decision is Decision.REFUSE
    assert "max_hops_missing" in r.reasons


def test_refuse_work_budget_exhaustion():
    gate = CodeNavIntentGate()
    r = gate.evaluate(req("PATH", goal="db.query").__class__(
        subject_id="navigation-1",
        payload=req("PATH", goal="db.query").payload,
        budget=1.1,
    ))
    assert r.decision is Decision.REFUSE
    assert "work_budget_exhausted" in r.reasons


def test_refuse_hop_budget_when_goal_too_far():
    gate = CodeNavIntentGate()
    r = gate.evaluate(req("PATH", goal="db.query", max_hops=2))
    assert r.decision is Decision.REFUSE
    assert "goal_not_reached_within_hop_budget" in r.reasons


def test_refuse_expired_authority():
    gate = CodeNavIntentGate(clock=lambda: 101)
    q = req("CALLEES", max_hops=1)
    q = CodeNavIntentGateRequest(
        subject_id=q.subject_id,
        payload=q.payload,
        budget=q.budget,
        grant_id="grant-7",
        not_after=100,
    )
    r = gate.evaluate(q)
    assert r.decision is Decision.REFUSE
    assert "authority_expired" in r.reasons


def test_refuse_unknown_payload_keys_instead_of_silently_ignoring():
    gate = CodeNavIntentGate()
    q = req("CALLEES", max_hops=1)
    payload = dict(q.payload)
    payload["ignore_budget"] = True
    r = gate.evaluate(CodeNavIntentGateRequest(subject_id=q.subject_id, payload=payload, budget=20))
    assert r.decision is Decision.REFUSE
    assert any(reason.startswith("payload_keys_unknown:") for reason in r.reasons)


def test_graph_changes_change_receipt():
    gate = CodeNavIntentGate()
    a = gate.evaluate(req("CALLEES", max_hops=1))
    modified = dict(GRAPH)
    modified["api.handle"] = ["service.resolve"]
    b = gate.evaluate(
        CodeNavIntentGateRequest(
            subject_id="navigation-1",
            payload={
                "graph": modified,
                "intent": "CALLEES",
                "start": "api.handle",
                "max_hops": 1,
                "max_nodes": 20,
            },
            budget=50,
        )
    )
    assert a.digest != b.digest
    assert a.metrics["graph_fingerprint"] != b.metrics["graph_fingerprint"]


def test_cli_executes_real_request(tmp_path, capsys):
    import json
    from code_nav_intent_gate import cli
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps({
        "subject_id": "cli",
        "budget": 10,
        "payload": {
            "graph": {"A": ["B"], "B": []},
            "intent": "PATH",
            "start": "A",
            "goal": "B",
            "max_hops": 1,
            "max_nodes": 4
        }
    }))
    assert cli(["--input", str(request_path)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["decision"] == "ALLOW"
    assert output["result"]["path"] == ["A", "B"]
