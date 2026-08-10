# Issue contract — Code Nav Intent Gate

## Problem
Code intelligence agents over-fetch without intent-bound query budgets.

## Desired outcome
A bounded, open, testable implementation of **Code Nav Intent Gate** that demonstrates Bind navigation queries to declared intent and hop budgets; refuse unbounded graph walks.

## Non-goals
- Sourcegraph affiliation or proprietary integration
- Portfolio-wide scale/performance claims
- UI marketing site

## Acceptance
1. Mechanism module implements allow + refuse with structured receipts
2. pytest behavioral suite green
3. operate.py cold-start produces JSON receipt
4. Non-affiliation disclaimer preserved
