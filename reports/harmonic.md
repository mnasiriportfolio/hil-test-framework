# HIL Test Framework — Test Report

_Generated 2026-07-24 10:00 UTC_

## Harmonic Detection - dc_1500v_50hz  ->  **PASS**

| # | Check | Expected | Measured | Outcome |
|---|-------|----------|----------|---------|
| 1 | Harmonic trigger (order 3) | 1.5 A (1.46-1.54) | 1.50 A | PASS |
| 2 | Detection time (scope) | <= 2000 ms | 1130.0 ms | PASS |
| 3 | Relay hold | >= 3 s | 3.20 s | PASS |
