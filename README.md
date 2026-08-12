# Code Nav Intent Gate

Independent GlacierEQ portfolio system aligned to **Sourcegraph-style code intelligence problems**.

> **Not affiliated.** This repository is not affiliated with, endorsed by, employed by, or deployed at Sourcegraph.

## Purpose

Bound code-navigation work to an explicit developer/agent intent so graph traversal cannot silently expand into an unbounded repository crawl.

## What it does

The runtime accepts a code graph plus a declared navigation intent and executes deterministic bounded traversal:

- shortest path discovery between symbols
- caller discovery through a reverse graph
- callee discovery
- reference discovery
- bounded neighborhood traversal
- maximum hop and visited-node ceilings
- explicit work-unit budget
- authority-expiry checks
- deterministic graph and decision fingerprints
- fail-closed rejection of unknown request fields

A successful receipt proves what was traversed, how much graph work occurred, and which limits constrained the operation. A refusal receipt explains exactly which invariant failed.

## Run

```bash
python -m pytest -q
python scripts/operate.py
```

`operate.py` executes a real five-hop code path lookup and exits non-zero unless the bounded path and receipt verify.

## Core API

```python
from code_nav_intent_gate import CodeNavIntentGate, CodeNavIntentGateRequest

receipt = CodeNavIntentGate().evaluate(
    CodeNavIntentGateRequest(
        subject_id="impact-check",
        payload={
            "graph": {"api": ["service"], "service": ["database"], "database": []},
            "intent": "PATH",
            "start": "api",
            "goal": "database",
            "max_hops": 3,
            "max_nodes": 20,
        },
        budget=20,
    )
)
```

## Boundary

This repository provides the intent-bound navigation kernel. It does not claim access to Sourcegraph infrastructure or proprietary APIs. A production integration can feed this kernel graphs assembled from Sourcegraph, SCIP, LSIF, language servers, or another code-intelligence index.
