*** Settings ***
Documentation     Analog-output correctness — apply a known line voltage, read the
...               milliamps the DUT puts on its current loop, convert back through the
...               loop's stated scaling and compare kilovolts with kilovolts.
...
...               Every plan point gets its own row and its own outcome. A single
...               averaged accuracy figure would let a channel that is wrong at one
...               end of its span hide behind being right at the other.
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
