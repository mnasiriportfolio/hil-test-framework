*** Settings ***
Documentation     Overcurrent detection — drive current past the trigger and verify
...               the DUT relay trips within spec, timed on the oscilloscope, and
...               holds for the required duration.
...
...               This suite says nothing about how the bench is reached. Run it
...               against the in-process simulation, a raw TCP/SCPI rack, PyVISA or
...               the containerised emulator by changing ${CONFIG} only.
Library           HiltfLibrary.py
Suite Setup       Open Bench          ${CONFIG}
Suite Teardown    Run Keywords        Write Report    reports/overcurrent.md    AND    Close Bench


*** Variables ***
${CONFIG}         config/bench_config.yaml


*** Test Cases ***
Bench Is Reachable
    [Documentation]    Every instrument answers its identity query before any
    ...                measurement is trusted. On a real bench this is the step
    ...                that catches a powered-off instrument in two seconds
    ...                instead of halfway through a scenario.
    [Tags]             smoke
    Log Bench Identities

Overcurrent Detection All Scenarios
    [Documentation]    Every enabled row in the overcurrent plan.
    Run All Cases      overcurrent    config/overcurrent_plan.csv
    All Cases Passed
