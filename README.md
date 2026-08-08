# HIL Test Framework

A layered, config-driven **Hardware-in-the-Loop test framework** for
instrument-controlled hardware validation.

One set of Robot Framework suites. Four completely different ways of reaching
the bench. **The suites do not know which one they are running on, and the
reports come out identical** — that is the whole claim, and there is a test that
fails if it ever stops being true.

[![CI](https://github.com/mnasiriportfolio/hil-test-framework/actions/workflows/ci.yml/badge.svg)](https://github.com/mnasiriportfolio/hil-test-framework/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%20|%203.11%20|%203.12-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

> Built as a clean-room demonstration of the test-automation architecture I use
> professionally for power and signal-measurement hardware. It contains no
> proprietary code, data or device details — everything runs against a
> deterministic simulation you can start on your laptop.

---

## The four transports

| `driver:` in the YAML | How the bench is reached | Needs |
|---|---|---|
| `sim_*` | in-process simulation | nothing |
| `lan_*` | **raw TCP socket, SCPI** — buffered line reader, IEEE 488.2 binary blocks | standard library |
| `visa_*` | **PyVISA** — `pyvisa-sim`, `pyvisa-py`, or a system VISA library | `pip install -e ".[visa]"` |
| `udp_dut` | the DUT's own **little-endian binary protocol over UDP** | standard library |

Switching between them is an edit to `bench_config.yaml`. Not a code change, not
a flag, not an `if simulated:` — there is no such branch anywhere in Layer 1 or
Layer 2, and [a test enforces that](tests/unit/test_layering.py).

```bash
robot --variable CONFIG:config/bench_config.yaml hiltf/layer1_suites   # simulation
robot --variable CONFIG:config/bench_socket.yaml hiltf/layer1_suites   # sockets + UDP
robot --variable CONFIG:config/bench_visa.yaml   hiltf/layer1_suites   # PyVISA
```

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev,visa]"

python examples/run_all.py    # end-to-end run + Markdown report, no hardware
pytest -q                     # unit + integration; starts its own bench emulator
robot --outputdir reports/local hiltf/layer1_suites
```

To drive the suites over real sockets, start the bench emulator first:

```bash
python -m hiltf.emulators                                    # SCPI :5025, DUT :50000/udp
make robot-socket        # or: make robot-visa
```

## The containerised bench

The interesting one. `bench` is a container running the instrument and DUT
emulators as **network services**; the runners are separate containers that
reach them over the compose network.

```bash
docker compose run --rm socket-runner    # suites over raw TCP/SCPI + binary UDP
docker compose run --rm visa-runner      # the same suites over PyVISA
docker compose run --rm tests            # the test suite inside the image
```

This is what makes the transport claim more than a diagram. A suite that only
ever runs in one process proves the *logic*; it cannot prove the *driver*,
because there is no wire for a driver to get wrong. Here there is a wire, it is
the same wire in CI as on a desk, and `bench` has a healthcheck that probes both
protocols so a runner never starts against a half-open listener.

## Why it's interesting

- **Three-layer architecture** with the separation enforced by tests, not by
  convention: Layer 2 may not name a transport, Layer 1 `.robot` files may not
  contain an address or a SCPI string, and the keyword layer may not read the
  simulation.
- **A hardware abstraction layer that is actually exercised.** Four
  implementations of the same `Protocol`s, all four run in CI, and
  [`test_transport_parity.py`](tests/integration/test_transport_parity.py)
  asserts the socket run and the in-process run produce byte-identical reports.
- **Real protocol work, not mocks.** A buffered SCPI line reader, IEEE 488.2
  definite-length block transfer, `*OPC?` synchronisation, VISA termination
  handling, and a binary UDP protocol with retries, message-id matching and
  NACK reason codes.
- **Event-sourced reporting:** keywords publish results to a bus; a recorder
  subscribes and renders. Test logic never touches output format.
- **Measured, not assumed.** Every case calibrates the generator's
  volts-to-amps ratio against the meter before it computes a drive level, and
  measures the relay's latch duration off the captured trace rather than asking
  the device how long it intends to hold.
- **Three real T&M test patterns:** overcurrent detection, harmonic detection,
  and analog-output correctness with automated self-calibration.

## Architecture

```
Layer 1  hiltf/layer1_suites/   Robot specs — intent only. No code, no addresses.
                │  keywords
Layer 2  hiltf/layer2_engine/   plans, keyword algorithms, event bus, reporting
                │  Protocol interfaces only
Layer 3  hiltf/layer3_hal/      HAL: interfaces + four transport implementations
                │
         hiltf/emulators/       the other end of the wire: SCPI/TCP + binary/UDP
```

The dependency arrow only ever points **down**, and each layer knows strictly
less than the one below it:

| Layer | Knows about | Never knows about |
|-------|-------------|-------------------|
| 1 Suites | test intent, keyword names | Python, channels, addresses |
| 2 Engine | HAL Protocols, spec maths | which transport, IPs, SCPI, VISA |
| 3 HAL | sockets, SCPI, VISA, byte offsets | which test is running |

Full write-up, including the wire formats: **[ARCHITECTURE.md](ARCHITECTURE.md)**.

## The three tests

**Overcurrent detection** — calibrate the current path, confirm the relay is
idle, drive just past the trigger, confirm the metered value against spec, then
take **one** acquisition and get both numbers from it: the rising edge is the
detection latency, the width to the falling edge is the latch duration.

**Harmonic detection** — hold a DC voltage, inject a harmonic on the current
input, confirm the harmonic-band RMS trigger, then the same scope timing and
hold.

**Analog-output correctness** — drive a known input, read the DUT's reproduced
analog output; if the error exceeds tolerance, **compute and apply a calibration
correction, then re-verify** (pre-cal reports as `CHECK`, post-cal as `PASS`).

## Design notes

Six decisions that are not obvious, each one a bug this code exists to prevent.
They are documented at the code, not just here.

**A buffered line reader, never a bare `recv()`.**
[`scpi_socket.py`](hiltf/layer3_hal/scpi_socket.py) — `recv()` is not
line-oriented. One call can return half a response or three at once. Reading a
"line" with a bare `recv()` works until the first instrument that answers
quickly, then silently desynchronises the session: every later query returns the
*previous* query's answer.

**Binary blocks are read by count, never by delimiter.** A float32 sample can be
`0x0A 0x0A 0x0A 0x0A` — an ordinary value that is also four terminators. The
same trap appears through PyVISA, where a `::SOCKET` resource must have
`read_termination` set for text queries to work at all; the VISA driver switches
it off for the transfer and reads the declared byte count explicitly.

**`*OPC?` after every configuration step.** Writes are fire-and-forget and two
instruments are two independent sessions with no ordering between them, so "set
the generator, then read the meter" is a race. Skip the synchronisation point
and the test still passes on a fast day — which is the worst failure mode,
because it comes back as a flake months later.
[There is a test](tests/integration/test_socket_transport.py) that loops the
sequence so an unsynchronised implementation fails reliably.

**Drive *past* the threshold, and say by how much.** This one the project
learned the hard way. In-process, the drive level is a Python float and the
arithmetic happened to land a hair above 200.000 A, so the relay tripped. Over a
socket the same level goes out as SCPI text with finite resolution, comes back a
hair below, and the relay correctly did not trip. The transport did not break
the test — it revealed that the test had been relying on the last bit of a
float. Real generators have command resolution and real detectors have
hysteresis, so a test that means "cross the threshold" states its margin.

**Little-endian, explicitly.** Network byte order is big-endian by convention;
embedded firmware emits its CPU's native order, which is little. Getting it
wrong does not raise — it returns plausible, wrong numbers, and a test suite
written against the same wrong assumption agrees with itself perfectly.

**A NACK is not a timeout.** The device is allowed to refuse. Retrying a refusal
just burns the timeout budget before failing, so
[`dut_driver.py`](hiltf/layer3_hal/dut_driver.py) raises immediately with the
reason code — and discards late replies to earlier, retried requests instead of
handing them to whoever is waiting now.

## Testing

```
tests/unit/          protocol codecs, SCPI interpreter, spec maths, config
                     validation, and the architecture rules themselves
tests/integration/   real sockets, real datagrams, real VISA sessions
```

The emulator can drop datagrams on demand (`drop_first=`) so the retry path runs
for real rather than being assumed to work because nothing ever went wrong.
Integration tests bind **ephemeral** ports, so they can run in parallel and
cannot collide with anything already on the machine.

## Docs site

A self-contained site lives in [`docs/`](docs/) and publishes to GitHub Pages:

- **▶ Watch** — [`docs/presentation/`](docs/presentation/index.html): a
  self-playing animated walkthrough of the architecture.
- **🎛️ Try it live** — [`docs/demo/`](docs/demo/index.html): tweak a test and
  watch the oscilloscope trace and report update; a second tab boots **Pyodide**
  and runs the real engine code in your browser.
- **📘 Learn it** — [`docs/book/`](docs/book/index.html): a teaching book that
  explains the codebase from the real files.

To publish: **Settings → Pages → Deploy from branch → `main` / `/docs`**.

## License

MIT — see [LICENSE](LICENSE).
