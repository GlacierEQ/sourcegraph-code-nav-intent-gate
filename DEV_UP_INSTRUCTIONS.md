# DEV_UP_INSTRUCTIONS — COMPLETED

The original Wave C scaffold brief has been executed.

## Implemented

`src/code_nav_intent_gate.py` now provides a real intent-bound code graph navigation engine:

- deterministic bounded BFS
- PATH, CALLERS, CALLEES, REFERENCES, and NEIGHBORHOOD intents
- hop, node, and work-unit budgets
- expired-authority refusal
- fail-closed schema validation
- deterministic graph and receipt fingerprints
- structured success and refusal receipts

## Proof

Run:

```bash
python -m pytest -q
python scripts/operate.py
```

The behavioral suite covers successful navigation, reverse traversal, deterministic replay, graph mutation, budget exhaustion, hop exhaustion, malformed graphs, unknown fields, and authority expiry.

This is an independent GlacierEQ implementation and claims no Sourcegraph affiliation or proprietary access.
