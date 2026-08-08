*** Settings ***
Documentation     Harmonic detection — hold a DC voltage, inject a harmonic on the
...               current input and verify the DUT's harmonic relay trigger, scope
...               timing and hold.
...
...               Transport-agnostic: change ${CONFIG} to run the identical suite
...               over sockets, PyVISA or the containerised bench.
Library           HiltfLibrary.py
Suite Setup       Open Bench          ${CONFIG}
Suite Teardown    Run Keywords        Write Report    reports/harmonic.md    AND    Close Bench


*** Variables ***
${CONFIG}         config/bench_config.yaml


*** Test Cases ***
Harmonic Detection All Scenarios
    [Documentation]    Every enabled row in the harmonic plan.
    Run All Cases      harmonic    config/harmonic_plan.csv
    All Cases Passed
