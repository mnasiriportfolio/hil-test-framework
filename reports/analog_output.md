# HIL Test Framework — Test Report

_Generated 2026-08-08 15:41 UTC_

## Analog Output Correctness - ch1  ->  **PASS**

| # | Check | Expected | Measured | Outcome |
|---|-------|----------|----------|---------|
| 1 | Volts-to-kilovolts ratio (measured) | > 0 kV/V | 5.0000 kV/V | PASS |
| 2 | CH1 at 0.8 kV | within 1 % of the applied line voltage | 8.271 mA -> 0.801 kV vs 0.800 kV (0.102 % error) | PASS |
| 3 | CH1 at 1 kV | within 1 % of the applied line voltage | 9.338 mA -> 1.001 kV vs 1.000 kV (0.087 % error) | PASS |
| 4 | CH1 at 1.2 kV | within 1 % of the applied line voltage | 10.406 mA -> 1.201 kV vs 1.200 kV (0.094 % error) | PASS |
| 5 | CH1 at 1.5 kV | within 1 % of the applied line voltage | 12.007 mA -> 1.501 kV vs 1.500 kV (0.088 % error) | PASS |
| 6 | CH1 at 1.8 kV | within 1 % of the applied line voltage | 13.609 mA -> 1.802 kV vs 1.800 kV (0.094 % error) | PASS |

## Analog Output Correctness - ch2  ->  **PASS**

| # | Check | Expected | Measured | Outcome |
|---|-------|----------|----------|---------|
| 1 | Volts-to-kilovolts ratio (measured) | > 0 kV/V | 5.0000 kV/V | PASS |
| 2 | CH2 at 0.8 kV | within 1 % of the applied line voltage | 8.250 mA -> 0.797 kV vs 0.800 kV (0.391 % error) | PASS |
| 3 | CH2 at 1 kV | within 1 % of the applied line voltage | 9.312 mA -> 0.996 kV vs 1.000 kV (0.400 % error) | PASS |
| 4 | CH2 at 1.2 kV | within 1 % of the applied line voltage | 10.374 mA -> 1.195 kV vs 1.200 kV (0.406 % error) | PASS |
| 5 | CH2 at 1.5 kV | within 1 % of the applied line voltage | 11.968 mA -> 1.494 kV vs 1.500 kV (0.400 % error) | PASS |
| 6 | CH2 at 1.8 kV | within 1 % of the applied line voltage | 13.562 mA -> 1.793 kV vs 1.800 kV (0.396 % error) | PASS |

## Analog Output Correctness - ch3  ->  **PASS**

| # | Check | Expected | Measured | Outcome |
|---|-------|----------|----------|---------|
| 1 | Volts-to-kilovolts ratio (measured) | > 0 kV/V | 5.0000 kV/V | PASS |
| 2 | CH3 at 0.8 kV | within 1 % of the applied line voltage | 8.241 mA -> 0.795 kV vs 0.800 kV (0.602 % error) | PASS |
| 3 | CH3 at 1 kV | within 1 % of the applied line voltage | 9.301 mA -> 0.994 kV vs 1.000 kV (0.606 % error) | PASS |
| 4 | CH3 at 1.2 kV | within 1 % of the applied line voltage | 10.362 mA -> 1.193 kV vs 1.200 kV (0.594 % error) | PASS |
| 5 | CH3 at 1.5 kV | within 1 % of the applied line voltage | 11.952 mA -> 1.491 kV vs 1.500 kV (0.600 % error) | PASS |
| 6 | CH3 at 1.8 kV | within 1 % of the applied line voltage | 13.542 mA -> 1.789 kV vs 1.800 kV (0.604 % error) | PASS |
