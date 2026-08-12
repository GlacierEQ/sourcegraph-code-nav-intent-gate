from __future__ import annotations

from code_nav_intent_gate import CodeNavIntentGate, CodeNavIntentGateRequest, Decision


NOW = 1_800_000_000.0


def _graph():
    return {
        "nodes": [
            {"id": "entry", "repository": "acme/app", "kind": "function", "path": "src/main.py"},
            {"id": "service", "repository": "acme/app", "kind": "function", "path": "src/service.py"},
            {"id": "store", "repository": "acme/app", "kind": "function", "path": "src/store.py"},
            {"id": "model", "repository": "acme/app", "kind": "type", "path": "src/model.py"},
            {"id": "external", "repository": "vendor/lib", "kind": "function", "path": "lib.py"},
        ],
        "edges": [
            {"from": "entry", "to": "service", "kind": "calls"},
            {"from": "service", "to": "store", "kind": "calls"},
            {"from": "store", "to": "model", "kind": "references"},
            {"from": "service", "to": "external", "kind": "calls"},
        ],
    }


def _intent(**overrides):
    value = {
        "start_nodes": ["entry"],
        "target_nodes": ["store"],
        "allowed_edge_kinds": ["calls"],
        "allowed_repositories": ["acme/app"],
        "direction": "outgoing",
        "max_hops": 3,
        "max_nodes": 10,
        "require_target": True,
    }
    value.update(overrides)
    return value


def _request(intent=None, graph=None, **kwargs):
    return CodeNavIntentGateRequest(
        subject_id="nav-42",
        payload={"graph": graph or _graph(), "intent": intent or _intent()},
        budget=10,
        **kwargs,
    )


def test_bounded_navigation_returns_deterministic_target_path() -> None:
    receipt = CodeNavIntentGate().evaluate(_request(), now=NOW)

    assert receipt.decision is Decision.ALLOW
    assert receipt.reasons == ("navigation_completed_within_intent",)
    assert receipt.visited_nodes == ("entry", "service", "store")
    assert receipt.target_paths[0]["nodes"] == ["entry", "service", "store"]
    assert receipt.target_paths[0]["hops"] == 2
    assert receipt.metrics["visited_node_count"] == 3
    assert receipt.metrics["skipped_repository_edges"] == 1
    assert len(receipt.digest) == 64


def test_hop_limit_blocks_unreached_required_target() -> None:
    receipt = CodeNavIntentGate().evaluate(
        _request(intent=_intent(max_hops=1)),
        now=NOW,
    )

    assert receipt.decision is Decision.REFUSE
    assert "required_target_not_reached" in receipt.reasons
    assert receipt.metrics["max_depth_reached"] == 1


def test_node_expansion_budget_is_hard_limit() -> None:
    receipt = CodeNavIntentGate().evaluate(
        CodeNavIntentGateRequest(
            subject_id="nav",
            payload={"graph": _graph(), "intent": _intent(max_nodes=2)},
            budget=2,
        ),
        now=NOW,
    )

    assert receipt.decision is Decision.REFUSE
    assert "node_budget_exhausted" in receipt.reasons
    assert len(receipt.visited_nodes) == 2


def test_intent_cannot_request_more_nodes_than_caller_budget() -> None:
    receipt = CodeNavIntentGate().evaluate(
        CodeNavIntentGateRequest(
            subject_id="nav",
            payload={"graph": _graph(), "intent": _intent(max_nodes=5)},
            budget=4,
        ),
        now=NOW,
    )

    assert receipt.decision is Decision.REFUSE
    assert "intent_node_budget_exceeds_request" in receipt.reasons
    assert receipt.visited_nodes == ()


def test_disallowed_edge_kind_is_not_followed() -> None:
    receipt = CodeNavIntentGate().evaluate(
        _request(
            intent=_intent(
                target_nodes=["model"],
                allowed_edge_kinds=["calls"],
                max_hops=4,
            )
        ),
        now=NOW,
    )

    assert receipt.decision is Decision.REFUSE
    assert "required_target_not_reached" in receipt.reasons
    assert receipt.metrics["skipped_edge_kind_edges"] == 1


def test_navigation_is_deterministic_when_graph_input_order_changes() -> None:
    graph = _graph()
    reversed_graph = {
        "nodes": list(reversed(graph["nodes"])),
        "edges": list(reversed(graph["edges"])),
    }
    twin = CodeNavIntentGate()
    first = twin.evaluate(_request(), now=NOW)
    second = twin.evaluate(_request(graph=reversed_graph), now=NOW)

    assert first.digest == second.digest
    assert first.target_paths == second.target_paths
    assert first.traversed_edges == second.traversed_edges


def test_expired_request_refuses_before_navigation() -> None:
    receipt = CodeNavIntentGate().evaluate(
        _request(not_after=NOW - 1),
        now=NOW,
    )
    assert receipt.decision is Decision.REFUSE
    assert "request_expired" in receipt.reasons
