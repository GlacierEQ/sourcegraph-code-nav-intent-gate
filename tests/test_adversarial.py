from __future__ import annotations

import pytest

from code_nav_intent_gate import (
    CodeNavIntentGate,
    CodeNavIntentGateRequest,
    Decision,
    NavigationSchemaError,
)


NOW = 1_800_000_000.0


def _graph():
    return {
        "nodes": [
            {"id": "a", "repository": "r/app"},
            {"id": "b", "repository": "r/app"},
            {"id": "c", "repository": "r/app"},
            {"id": "x", "repository": "other/lib"},
        ],
        "edges": [
            {"from": "a", "to": "b", "kind": "calls"},
            {"from": "b", "to": "c", "kind": "calls"},
            {"from": "b", "to": "x", "kind": "calls"},
        ],
    }


def _evaluate(intent, graph=None, *, budget=10):
    return CodeNavIntentGate().evaluate(
        CodeNavIntentGateRequest(
            subject_id="nav",
            payload={"graph": graph or _graph(), "intent": intent},
            budget=budget,
        ),
        now=NOW,
    )


def test_missing_hop_or_node_budget_cannot_run_unbounded() -> None:
    receipt = _evaluate(
        {
            "start_nodes": ["a"],
            "allowed_edge_kinds": ["calls"],
            "allowed_repositories": ["r/app"],
        }
    )
    assert receipt.decision is Decision.REFUSE
    assert "navigation_budget_missing_or_invalid" in receipt.reasons


def test_missing_explicit_edge_kind_scope_is_rejected() -> None:
    receipt = _evaluate(
        {
            "start_nodes": ["a"],
            "allowed_repositories": ["r/app"],
            "max_hops": 2,
            "max_nodes": 4,
        }
    )
    assert receipt.decision is Decision.REFUSE
    assert "allowed_edge_kinds_missing" in receipt.reasons


def test_start_node_cannot_begin_outside_repository_scope() -> None:
    receipt = _evaluate(
        {
            "start_nodes": ["x"],
            "allowed_edge_kinds": ["calls"],
            "allowed_repositories": ["r/app"],
            "max_hops": 2,
            "max_nodes": 4,
        }
    )
    assert receipt.decision is Decision.REFUSE
    assert "start_node_repository_not_allowed:x" in receipt.reasons


def test_unknown_target_refuses_instead_of_silently_returning_empty() -> None:
    receipt = _evaluate(
        {
            "start_nodes": ["a"],
            "target_nodes": ["missing"],
            "allowed_edge_kinds": ["calls"],
            "allowed_repositories": ["r/app"],
            "max_hops": 2,
            "max_nodes": 4,
        }
    )
    assert receipt.decision is Decision.REFUSE
    assert "target_node_unknown:missing" in receipt.reasons


def test_graph_edge_referencing_unknown_node_is_schema_error() -> None:
    graph = _graph()
    graph["edges"].append({"from": "a", "to": "ghost", "kind": "calls"})
    receipt = _evaluate(
        {
            "start_nodes": ["a"],
            "allowed_edge_kinds": ["calls"],
            "allowed_repositories": ["r/app"],
            "max_hops": 2,
            "max_nodes": 4,
        },
        graph,
    )
    assert receipt.decision is Decision.REFUSE
    assert any("references_unknown_node" in reason for reason in receipt.reasons)


def test_duplicate_node_identity_is_rejected() -> None:
    graph = _graph()
    graph["nodes"].append({"id": "a", "repository": "r/app"})
    receipt = _evaluate(
        {
            "start_nodes": ["a"],
            "allowed_edge_kinds": ["calls"],
            "allowed_repositories": ["r/app"],
            "max_hops": 2,
            "max_nodes": 4,
        },
        graph,
    )
    assert receipt.decision is Decision.REFUSE
    assert "node_duplicate:a" in receipt.reasons


def test_invalid_direction_is_rejected_at_compile_time() -> None:
    with pytest.raises(NavigationSchemaError, match="direction_invalid"):
        CodeNavIntentGate._normalize_intent(
            {
                "start_nodes": ["a"],
                "allowed_edge_kinds": ["calls"],
                "allowed_repositories": ["r/app"],
                "direction": "sideways",
                "max_hops": 1,
                "max_nodes": 2,
            }
        )


def test_expected_intent_digest_detects_query_scope_mutation() -> None:
    twin = CodeNavIntentGate()
    original = twin._normalize_intent(
        {
            "start_nodes": ["a"],
            "target_nodes": ["b"],
            "allowed_edge_kinds": ["calls"],
            "allowed_repositories": ["r/app"],
            "max_hops": 2,
            "max_nodes": 4,
        }
    )
    changed = {
        "start_nodes": ["a"],
        "target_nodes": ["b"],
        "allowed_edge_kinds": ["calls"],
        "allowed_repositories": ["r/app", "other/lib"],
        "max_hops": 2,
        "max_nodes": 4,
    }
    receipt = twin.evaluate(
        CodeNavIntentGateRequest(
            subject_id="nav",
            payload={
                "graph": _graph(),
                "intent": changed,
                "expected_intent_digest": original["intent_digest"],
            },
            budget=10,
        ),
        now=NOW,
    )
    assert receipt.decision is Decision.REFUSE
    assert "intent_digest_mismatch" in receipt.reasons
