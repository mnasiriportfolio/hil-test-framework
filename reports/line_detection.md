# HIL Test Framework — Test Report

_Generated 2026-08-08 15:41 UTC_

## Line Detection - dc_1_5kv  ->  **PASS**

| # | Check | Expected | Measured | Outcome |
|---|-------|----------|----------|---------|
| 1 | Volts-to-kilovolts ratio (measured) | > 0 kV/V | 5.0000 kV/V | PASS |
| 2 | No line declared well below pick-up | idle at 0.500 kV | 0.500 kV, relay idle | PASS |
| 3 | Pick-up threshold | 1 kV +/- 3 % | 1.000 kV | PASS |
| 4 | Holds below pick-up (hysteresis) | still declared at 0.975 kV, between drop-out and pick-up | 0.975 kV, relay driven | PASS |
| 5 | Drop-out threshold | 0.95 kV +/- 3 %, below pick-up | 0.949 kV | PASS |
| 6 | Line-in time (stimulus -> contact) | < 2000 ms | 1072.0 ms | PASS |
| 7 | Relay set time (digital pin -> contact) | < 5 ms | 2.0 ms | PASS |
| 8 | Rides through a short supply hole | still declared after a 300 ms interruption | 1.500 kV line, relay driven | PASS |
| 9 | Line-out time (stimulus removed -> contact opens) | < 1000 ms | 526.0 ms | PASS |
