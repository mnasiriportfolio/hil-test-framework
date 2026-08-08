# Architecture

The short version: **the test specification, the test logic and the way an
instrument is reached are three separate things, and only the third one changes
when the bench changes.**

The long version is below, including the wire formats, because the interesting
part of a HIL framework is not the diagram — it is what happens at the boundary
where Python meets an instrument that answers slowly, partially, or not at all.

---

## Layers

```
┌───────────────────────────────────────────────────────────────────────┐
│ Layer 1   hiltf/layer1_suites/*.robot                                  │
│           WHAT is being verified.                                      │
│           Test intent and keyword names. No Python, no channels, no    │
│           addresses, no SCPI. Retargeted with --variable CONFIG:…      │
└───────────────────────────────────────────────────────────────────────┘
                              │ keywords
┌───────────────────────────────────────────────────────────────────────┐
│ Layer 2   hiltf/layer2_engine/                                         │
│           HOW a verdict is reached.                                    │
│           Plans, spec maths, edge finding, calibration, the event bus  │
│           and the report recorder. Speaks Protocols only.              │
└───────────────────────────────────────────────────────────────────────┘
                              │ Protocol interfaces
┌───────────────────────────────────────────────────────────────────────┐
│ Layer 3   hiltf/layer3_hal/                                            │
│           HOW the instrument is reached.                               │
│           interfaces.py + four transports + the driver registry.       │
│           The only layer that knows a socket, a VISA session or a byte │
│           offset exists.                                               │
└───────────────────────────────────────────────────────────────────────┘
                              │ the wire
┌───────────────────────────────────────────────────────────────────────┐
│           hiltf/emulators/                                             │
│           The other end. One SimulatedBench, served as SCPI over TCP   │
│           and as the DUT's binary protocol over UDP.                   │
└───────────────────────────────────────────────────────────────────────┘
```

### The rules, and where they are enforced

| Rule | Enforced by |
|---|---|
| Layer 2 never names a transport | `test_layer2_never_names_a_transport` (AST) |
| Layer 2 never imports `socket` / `pyvisa` / `struct` | `test_only_layer3_imports_transport_libraries` |
| Keywords never read the simulation | `test_keywords_never_reach_into_the_simulation` |
| No simulation object exists on a socket bench | `test_controller_exposes_no_simulation_object` |
| `.robot` files contain no address or SCPI | `test_robot_suites_contain_no_addresses_or_scpi` |
| Every suite is retargetable | `test_robot_suites_select_the_bench_by_variable` |
| All transports agree | `test_socket_run_matches_in_process_exactly` |

The AST matters: a regex over source cannot tell a real call from the same words
inside a docstring, and a test that fails on its own explanation gets deleted.

---

## One bench, four ways to reach it

`SimulatedBench` ([`simulation.py`](hiltf/layer3_hal/simulation.py)) is the only
source of bench behaviour in the project. It is closed-form and deterministic —
no randomness — so CI is stable and a socket run can be compared to an
in-process run sample for sample.

```
                        ┌──────────────────────┐
                        │   SimulatedBench     │
                        │  (the only physics)  │
                        └──────────┬───────────┘
              ┌────────────────────┼────────────────────┐
              │                    │                    │
      held by reference      served over TCP      served over UDP
              │                    │                    │
        ┌─────▼──────┐      ┌──────▼───────┐     ┌──────▼──────┐
        │  sim_*     │      │  scpi_server │     │  dut_server │
        │  drivers   │      └──────┬───────┘     └──────┬──────┘
        └────────────┘             │                    │
                            ┌──────┴──────┐             │
                            │             │             │
                       ┌────▼───┐   ┌─────▼────┐   ┌────▼─────┐
                       │ lan_*  │   │  visa_*  │   │ udp_dut  │
                       │ socket │   │  PyVISA  │   │  binary  │
                       └────────┘   └──────────┘   └──────────┘
```

Because the emulators hold the *same object*, a measurement fetched over a
socket is identical to one obtained in-process. That is what makes parity a
meaningful assertion rather than a coincidence.

### The registry

[`factory.py`](hiltf/layer3_hal/factory.py) maps a `driver:` name to a class.
Adding a transport is: write a class satisfying the Protocol, register it, name
it in the YAML. Unknown keys are **rejected**, not ignored — a misspelled
`timout_s` that silently keeps the default is the kind of thing discovered at
2am on a bench.

`${VAR}` / `${VAR:-default}` expansion in config values is what lets one file
serve both `docker compose` (where the emulator is the host `bench`) and a
laptop (where it is `127.0.0.1`).

---

## Wire formats

### SCPI over raw TCP

Defined once in [`scpi_commands.py`](hiltf/layer3_hal/scpi_commands.py) and
imported by both drivers *and* the emulator, so they cannot drift apart and
agree with each other's mistakes.

```
*IDN?                     -> HILTF,BENCH-EMULATOR,0,1.0.0
*OPC?                     -> 1                       (synchronisation point)
SYST:ERR?                 -> +0,"No error" | -113,"Undefined header"
SOUR<n>:FUNC SIN|DC
SOUR<n>:VOLT <vpp>        SOUR<n>:VOLT?
SOUR<n>:VOLT:OFFS <v>     SOUR<n>:VOLT:OFFS?
SOUR<n>:FREQ <hz>         SOUR<n>:FREQ?
SOUR<n>:HARM<k>:VOLT <vpp>
SOUR<n>:HARM:CLE
OUTP<n> ON|OFF            OUTP<n>?
MEAS:VOLT:DC?             MEAS:CURR:AC?      MEAS:CURR:AC:HARM?
DIG:REL "<relay>",<gate_ms>
WAV:SRAT?                 WAV:POIN?          WAV:DATA?
```

`WAV:DATA?` answers with an IEEE 488.2 definite-length block:

```
#  8  00001100000  <1,100,000 bytes of little-endian float32>  \n
│  │  └── byte count
│  └───── how many digits the byte count has
└──────── block marker
```

A 5.5 s capture at 50 kS/s is 275,000 samples — about 1.1 MB. ASCII would be
roughly eight times larger and far slower to parse, which is why every benchtop
scope does it this way.

**An unrecognised header gets no reply at all.** It lands in the error queue and
the client waits, exactly as a firmware revision without that command behaves.
That is why `ScpiSocket.safe_query()` exists, and there is a test that a session
survives one.

### The DUT's binary protocol over UDP

```
+--------+--------+-------------------------+
| 0xA5   | msg_id | payload                 |
+--------+--------+-------------------------+
  1 byte   1 byte   0..n bytes, little-endian
```

| id | message | payload |
|----|---------|---------|
| `0x01` | request identity | — |
| `0x02` | identity | `u8` major, `u8` minor, `u32` serial, `u8` len, name |
| `0x03` | request relays | — |
| `0x04` | relay states | `u8` count, then per relay: `u8` len, name, `u8` state |
| `0x05` | request analog out | `u8` channel |
| `0x06` | analog out | `u8` channel, `f64` value |
| `0x07` | write correction | `f64` factor |
| `0x08` | ACK | `u8` echoed message id |
| `0xFF` | NACK | `u8` echoed message id, `u8` reason |

Reason codes: `0x01` unknown message, `0x02` bad length, `0x03` bad channel,
`0x04` value out of range.

The codec ([`dut_protocol.py`](hiltf/layer3_hal/dut_protocol.py)) is pure, so
every framing decision is testable without a socket. Length is validated before
unpacking: a short datagram raises rather than returning whatever `struct`
finds, because a silent decode of a truncated frame is how a measurement error
becomes a passing test.

---

## Measurement decisions

**The volts-to-amps ratio is measured, per case, not read from config.** It
would be easy to read — it is right there in the YAML. But on a physical bench
that number is a property of the probe, the shunt and the cabling on the day. A
stale one invalidates a whole campaign *and looks fine*, because every level is
off by the same factor. So each case drives a small probe, reads the meter, and
derives everything from that. The measured ratio is published as a report step,
so the evidence for every later level is in the document.

Wiring facts — which generator channel is the current input — stay in config.
They describe how the bench is cabled, not what it measured.

**One acquisition, two measurements.** The capture window is
`detect_max + hold_min + margin`, so a single trace carries the rising edge
(detection latency) and the width to the falling edge (latch duration). Asking
the device how long it holds would only read back its own intention.

**The edge threshold comes from the trace.** Half-way between its low and high
level, so a 3.3 V logic relay, a 5 V one and a 24 V industrial one all work with
no reconfiguration. A flat trace legitimately has no threshold — that is a relay
that never moved, and the code returns `inf` to say so. `inf` fails a
`<= limit` check; `0.0` would silently pass it.

**The test verifies the device.** The plans hold the *spec* values; the
simulation holds the device's *actual* behaviour. The framework measures reality
and compares. An out-of-tolerance-but-expected result surfaces as `CHECK` — not
silently passed, not hard-failed.

---

## Reporting

Keywords never hold a results list and never call a reporter:

```
keyword ──publish──> EventBus ──subscribe──> ReportRecorder ──> Markdown
```

One run can therefore feed many outputs without touching test logic. It is also
why the parity test can compare two entire runs: the recorder is the single
place a result exists.

---

## What this is a distillation of

The architecture, the layering rules and the measurement patterns come from a
Hardware-in-the-Loop framework I built professionally for certified embedded
measurement hardware — multi-instrument benches, SCPI and proprietary binary
protocols, scope-based sub-5 ms relay timing, and reports that have to stand up
as compliance evidence.

This repository contains none of that code, none of its device details and none
of its data. It is the *shape* of the solution, rebuilt against a simulation, so
it can be read, run and argued with by anyone.
