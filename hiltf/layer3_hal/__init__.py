"""Layer 3 — Hardware Abstraction Layer (HAL).

Only this layer knows *how* an instrument is reached. Everything above talks to
the Protocols in :mod:`hiltf.layer3_hal.interfaces`.

Four transports implement those Protocols, and the same suites run over all of
them — selected by ``driver:`` in ``bench_config.yaml``, never by a code change:

===================  ==========================================================
``sim_*``            in-process simulation; no dependencies, the default bench
``lan_*``            raw TCP socket speaking SCPI; standard library only
``visa_*``           PyVISA — ``pyvisa-sim``, ``pyvisa-py``, or a system VISA
``udp_dut``          the DUT's own little-endian binary protocol over UDP
===================  ==========================================================
"""
from .base_driver import BaseDriver
from .dut_driver import BinaryUdpDut, DutTimeout
from .factory import (
    REGISTRY,
    DriverConfigError,
    DriverContext,
    build_context,
    build_driver,
    expand_env,
    needs_simulated_bench,
)
from .interfaces import (
    DeviceUnderTest,
    Instrument,
    Multimeter,
    Oscilloscope,
    SignalGenerator,
    Waveform,
)
from .lan_drivers import LanMultimeter, LanOscilloscope, LanSignalGenerator
from .scpi_socket import ScpiError, ScpiSocket
from .sim_drivers import (
    SimDeviceUnderTest,
    SimMultimeter,
    SimOscilloscope,
    SimSignalGenerator,
)
from .simulation import ChannelState, SimConfig, SimulatedBench
from .visa_drivers import (
    PYVISA_AVAILABLE,
    VisaMultimeter,
    VisaOscilloscope,
    VisaSession,
    VisaSignalGenerator,
)

__all__ = [
    # protocols + value types
    "Instrument",
    "SignalGenerator",
    "Multimeter",
    "Oscilloscope",
    "DeviceUnderTest",
    "Waveform",
    "BaseDriver",
    # simulation core
    "SimConfig",
    "SimulatedBench",
    "ChannelState",
    # transport 1 — in-process
    "SimSignalGenerator",
    "SimMultimeter",
    "SimOscilloscope",
    "SimDeviceUnderTest",
    # transport 2 — raw TCP / SCPI
    "ScpiSocket",
    "ScpiError",
    "LanSignalGenerator",
    "LanMultimeter",
    "LanOscilloscope",
    # transport 3 — PyVISA
    "PYVISA_AVAILABLE",
    "VisaSession",
    "VisaSignalGenerator",
    "VisaMultimeter",
    "VisaOscilloscope",
    # transport 4 — binary UDP
    "BinaryUdpDut",
    "DutTimeout",
    # registry
    "REGISTRY",
    "DriverContext",
    "DriverConfigError",
    "build_context",
    "build_driver",
    "expand_env",
    "needs_simulated_bench",
]
