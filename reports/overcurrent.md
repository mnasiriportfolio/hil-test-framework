# HIL Test Framework — Test Report

_Generated 2026-08-08 15:41 UTC_

## Overcurrent Detection - slow_ac  ->  **PASS**

| # | Check | Expected | Measured | Outcome |
|---|-------|----------|----------|---------|
| 1 | Volts-to-amps ratio (measured) | > 0 A/Vrms | 10.0000 A/Vrms | PASS |
| 2 | No trip below the trigger window | idle at 184.3 A (rms); window opens at 194.0 A | 184.3 A, relay idle | PASS |
| 3 | Trip above the trigger window | trip at 216.3 A (rms); window closes at 206.0 A | 216.3 A, relay driven | PASS |
| 4 | Stimulus for the timing measurement | 300 A (rms) | 300.0 A on a 1.500 kV line | PASS |
| 5 | Detection time (stimulus -> digital pin) | >= 1200 ms | 1215.0 ms | PASS |
| 6 | Relay set time (digital pin -> contact) | < 5 ms | 2.0 ms | PASS |
| 7 | Total time (stimulus -> contact) | detection + relay set | 1217.0 ms | PASS |
| 8 | Relay hold (latched past the stimulus) | >= 3 s | 3.08 s | PASS |

## Overcurrent Detection - fast_ac  ->  **PASS**

| # | Check | Expected | Measured | Outcome |
|---|-------|----------|----------|---------|
| 1 | Volts-to-amps ratio (measured) | > 0 A/Vrms | 10.0000 A/Vrms | PASS |
| 2 | No trip below the trigger window | idle at 460.8 A (peak); window opens at 485.0 A | 460.8 A, relay idle | PASS |
| 3 | Trip above the trigger window | trip at 540.8 A (peak); window closes at 515.0 A | 540.8 A, relay driven | PASS |
| 4 | Stimulus for the timing measurement | 780 A (peak) | 780.0 A on a 1.500 kV line | PASS |
| 5 | Detection time (stimulus -> digital pin) | < 5 ms | 3.1 ms | PASS |
| 6 | Relay set time (digital pin -> contact) | < 5 ms | 2.0 ms | PASS |
| 7 | Total time (stimulus -> contact) | detection + relay set | 5.1 ms | PASS |
| 8 | Relay hold (latched past the stimulus) | >= 3 s | 3.05 s | PASS |
