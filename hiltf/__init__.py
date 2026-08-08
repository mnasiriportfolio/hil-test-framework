"""HIL Test Framework — a layered, config-driven Hardware-in-the-Loop test
framework with a hardware abstraction layer and a fully simulated bench.

Layer 1  ``layer1_suites``  Robot Framework specs (no code, no addresses).
Layer 2  ``layer2_engine``  test engine: plans, keywords, event bus, reporting.
Layer 3  ``layer3_hal``     hardware abstraction layer + simulated drivers.
"""

__version__ = "0.1.0"
