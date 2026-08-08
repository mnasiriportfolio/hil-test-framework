*** Settings ***
Documentation     Analog-output correctness — drive a known input, read the DUT's
...               reproduced analog output, and if it is out of tolerance run an
...               automated self-calibration and re-verify.
...
...               Transport-agnostic: change ${CONFIG} to run the identical suite
...               over sockets, PyVISA or the containerised bench.
Library           HiltfLibrary.py
Suite Setup       Open Bench          ${CONFIG}
Suite Teardown    Run Keywords        Write Report    reports/analog_output.md    AND    Close Bench


*** Variables ***
${CONFIG}         config/bench_config.yaml


*** Test Cases ***
Analog Output Correctness All Scenarios
    [Documentation]    Every enabled row in the analog-output plan.
    Run All Cases      analog_output    config/analog_out_plan.csv
    All Cases Passed
