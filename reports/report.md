# HIL Test Framework — Test Report

_Generated 2026-08-07 22:28 UTC_

## Overcurrent Detection - slow_ac  ->  **PASS**

| # | Check | Expected | Measured | Outcome |
|---|-------|----------|----------|---------|
| 1 | Volts-to-amps ratio (measured) | > 0 A/Vrms | 10.0000 A/Vrms | PASS |
| 2 | Relay idle before stimulus | no trip | 0.00 A | PASS |
| 3 | Trigger level | 200 A (194.0-206.0) | 202.00 A | PASS |
| 4 | Detection time (scope) | <= 2000 ms | 1200.0 ms | PASS |
| 5 | Relay hold (scope) | >= 3 s | 3.08 s | PASS |

## Harmonic Detection - dc_1500v_50hz  ->  **PASS**

| # | Check | Expected | Measured | Outcome |
|---|-------|----------|----------|---------|
| 1 | Volts-to-amps ratio (measured) | > 0 A/Vrms | 10.0000 A/Vrms | PASS |
| 2 | Harmonic trigger (order 3) | 1.5 A (1.46-1.54) | 1.52 A | PASS |
| 3 | Detection time (scope) | <= 2000 ms | 1130.0 ms | PASS |
| 4 | Relay hold (scope) | >= 3 s | 3.20 s | PASS |

## Analog Output Correctness - ch1_50a  ->  **CHECK**

| # | Check | Expected | Measured | Outcome |
|---|-------|----------|----------|---------|
| 1 | Volts-to-amps ratio (measured) | > 0 A/Vrms | 10.0000 A/Vrms | PASS |
| 2 | CH1 output error (pre-cal) | <= 1 % | 3.00 % | CHECK |
| 3 | CH1 auto-calibration | apply correction, error <= tol | factor 0.9709 -> 0.00 % | PASS |

## Analog Output Correctness - ch2_100a  ->  **CHECK**

| # | Check | Expected | Measured | Outcome |
|---|-------|----------|----------|---------|
| 1 | Volts-to-amps ratio (measured) | > 0 A/Vrms | 10.0000 A/Vrms | PASS |
| 2 | CH2 output error (pre-cal) | <= 1 % | 3.00 % | CHECK |
| 3 | CH2 auto-calibration | apply correction, error <= tol | factor 0.9709 -> 0.00 % | PASS |
