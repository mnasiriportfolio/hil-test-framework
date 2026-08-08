# HIL Test Framework — Test Report

_Generated 2026-08-08 15:41 UTC_

## Harmonic Detection - dc_line_50hz  ->  **PASS**

| # | Check | Expected | Measured | Outcome |
|---|-------|----------|----------|---------|
| 1 | Volts-to-amps ratio (measured) | > 0 A/Vrms | 10.0000 A/Vrms | PASS |
| 2 | Line held at DC | a DC line, so that any AC content is contamination | 1.500 kV DC | PASS |
| 3 | No trip below the trigger window | idle at 1.38 A; window opens at 1.46 A | 1.38 A, relay idle | PASS |
| 4 | Trip above the trigger window (50 Hz) | trip at 1.62 A; window closes at 1.54 A | 1.62 A, relay driven | PASS |
| 5 | Detection time (stimulus -> digital pin) | < 2000 ms | 1175.0 ms | PASS |
| 6 | Relay set time (digital pin -> contact) | < 5 ms | 2.0 ms | PASS |
| 7 | Total time (stimulus -> contact) | detection + relay set | 1177.0 ms | PASS |
| 8 | Relay hold (latched past the stimulus) | >= 3 s | 3.20 s | PASS |
