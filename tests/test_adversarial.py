from __future__ import annotations

from code_nav_intent_gate import CodeNavIntentGate, CodeNavIntentGateRequest, Decision

GRAPH = {"A": ["B"], "B": ["C"], "C": []}


def base(**payload_changes):
    payload = {
        "graph": GRAPH,
        "intent": "PATH",
        "start": "A",
        "goal": "C",
        "max_hops": 2,
        "max_nodes": 10,
        **payload_changes,
    }
    return CodeNavIntentGateRequest(subject_id="adv", payload=payload, budget=10)


def test_cannot_request_absurd_hop_ceiling():
    gate = CodeNavIntentGate()
    r = gate.evaluate(base(max_hops=10_000))
    assert r.decision is Decision.REFUSE
    assert "max_hops_out_of_range" in r.reasons


def test_cannot_request_absurd_node_ceiling():
    gate = CodeNavIntentGate()
    r = gate.evaluate(base(max_nodes=10_000_000))
    assert r.decision is Decision.REFUSE
    assert "max_nodes_out_of_range" in r.reasons


def test_unknown_start_refuses():
    gate = CodeNavIntentGate()
    r = gate.evaluate(base(start="ROOT_PASSWORD"))
    assert r.decision is Decision.REFUSE
    assert "start_unknown" in r.reasons


def test_non_boolean_budget_is_rejected():
    gate = CodeNavIntentGate()
    q = base()
    r = gate.evaluate(CodeNavIntentGateRequest(subject_id=q.subject_id, payload=q.payload, budget=True))
    assert r.decision is Decision.REFUSE
    assert "budget_invalid" in r.reasons


def test_bad_graph_shape_refuses():
    gate = CodeNavIntentGate()
    q = base(graph={"A": "B"})
    r = gate.evaluate(q)
    assert r.decision is Decision.REFUSE
    assert "graph_neighbors_invalid" in r.reasons


def test_caller_cannot_smuggle_affiliation_or_execution_claim_fields():
    gate = CodeNavIntentGate()
    q = base()
    payload = dict(q.payload)
    payload["company_affiliation"] = "Sourcegraph"
    payload["production_verified"] = True
    r = gate.evaluate(CodeNavIntentGateRequest(subject_id=q.subject_id, payload=payload, budget=10))
    assert r.decision is Decision.REFUSE
    assert any(reason.startswith("payload_keys_unknown:") for reason in r.reasons)
