# HIL Test Framework — Test Report

_Generated 2026-07-24 10:00 UTC_

## Overcurrent Detection - slow_ac  ->  **PASS**

| # | Check | Expected | Measured | Outcome |
|---|-------|----------|----------|---------|
| 1 | Relay idle before stimulus | no trip | 0.00 A | PASS |
| 2 | Trigger level | 200 A (194.0-206.0) | 200.00 A | PASS |
| 3 | Detection time (scope) | <= 2000 ms | 1200.0 ms | PASS |
| 4 | Relay hold | >= 3 s | 3.08 s | PASS |
