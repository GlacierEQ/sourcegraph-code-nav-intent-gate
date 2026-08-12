# Code Navigation Intent Engine

Independent GlacierEQ portfolio implementation aligned to public Sourcegraph operating themes. This repository is not affiliated with or endorsed by Sourcegraph.

## Purpose

Navigate code-intelligence graphs without allowing an agent to turn a focused question into an unbounded repository crawl.

The engine binds every navigation request to explicit intent and resource limits, then performs deterministic graph traversal and emits a receipt showing exactly what was visited, what was skipped, and whether the requested target was reached.

## Capabilities

A navigation intent declares:

- one or more start nodes
- optional target nodes
- allowed edge kinds such as `calls` or `references`
- allowed repositories
- traversal direction: `outgoing`, `incoming`, or `both`
- maximum hop depth
- maximum node expansions
- whether reaching a target is mandatory

The request itself also carries a node-expansion budget. An intent cannot silently ask for more traversal than the caller authorized.

## Fail-closed behavior

The engine refuses:

- missing hop/node budgets
- missing repository or edge-kind scope
- unknown start or target nodes
- start nodes outside repository scope
- malformed graph edges
- duplicate node identities
- invalid traversal direction
- expired requests
- mutated intent digests
- requests whose declared node budget exceeds the caller budget
- walks that exhaust the node budget
- required targets that cannot be reached within the declared scope

## Deterministic traversal

The graph and intent are canonicalized before execution. Adjacency is sorted, traversal uses deterministic breadth-first search, and target paths are reconstructed from the same parent chain every time. Reordering the input nodes or edges does not change the resulting receipt.

The receipt includes:

- graph digest
- intent digest
- evaluation digest
- visited nodes
- traversed allowed edges
- target paths with hop counts
- maximum depth reached
- repository-boundary skips
- edge-kind skips
- caller node budget

## Run it

```bash
python scripts/operate.py
```

The built-in example navigates a small call graph from an entry function to a storage function under explicit repository, edge, hop, and node limits.

Use your own graph:

```bash
python scripts/operate.py --input navigation.json --output receipt.json
```

Example request:

```json
{
  "subject_id": "nav-42",
  "budget": 10,
  "graph": {
    "nodes": [
      {"id": "entry", "repository": "acme/app", "kind": "function", "path": "src/main.py"},
      {"id": "store", "repository": "acme/app", "kind": "function", "path": "src/store.py"}
    ],
    "edges": [
      {"from": "entry", "to": "store", "kind": "calls"}
    ]
  },
  "intent": {
    "start_nodes": ["entry"],
    "target_nodes": ["store"],
    "allowed_edge_kinds": ["calls"],
    "allowed_repositories": ["acme/app"],
    "direction": "outgoing",
    "max_hops": 3,
    "max_nodes": 10,
    "require_target": true
  }
}
```

## Intent drift

The normalized intent has a stable digest. A control plane can persist that digest with the original navigation request and later pass it as `expected_intent_digest`. If repository scope, edge kinds, targets, or traversal budgets mutate, execution refuses.

## Verify behavior

```bash
python -m pytest -q
```

Tests cover deterministic paths, hop limits, node-budget exhaustion, request-vs-intent budget enforcement, disallowed edge kinds, repository boundaries, malformed graphs, duplicate nodes, unknown targets, invalid directions, expiry, and silent intent mutation.

## Boundary

This is a vendor-neutral graph-navigation engine and CLI. It does not claim Sourcegraph API access, proprietary code intelligence, customer-scale benchmarks, or hosted deployment. A real Sourcegraph or SCIP/LSIF adapter can supply the graph without changing the bounded traversal mechanism.
