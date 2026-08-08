# HIL Test Framework — Test Report

_Generated 2026-07-24 10:00 UTC_

## Analog Output Correctness - ch1_50a  ->  **CHECK**

| # | Check | Expected | Measured | Outcome |
|---|-------|----------|----------|---------|
| 1 | CH1 output error (pre-cal) | <= 1 % | 3.00 % | CHECK |
| 2 | CH1 auto-calibration | apply correction, error <= tol | factor 0.9709 -> 0.00 % | PASS |

## Analog Output Correctness - ch2_100a  ->  **CHECK**

| # | Check | Expected | Measured | Outcome |
|---|-------|----------|----------|---------|
| 1 | CH2 output error (pre-cal) | <= 1 % | 3.00 % | CHECK |
| 2 | CH2 auto-calibration | apply correction, error <= tol | factor 0.9709 -> 0.00 % | PASS |
