*** Settings ***
Documentation     Overcurrent detection, slow and fast — bracket the trigger window
...               from below and above, then time the trip on the oscilloscope.
...
...               The two detectors compare different quantities and are specified in
...               opposite directions: the slow one integrates an RMS current and must
...               NOT trip before its floor, because a protection that fires on inrush
...               is a broken protection; the fast one compares the instantaneous
...               current against a ceiling. Detection time, contact transit and hold
...               are measured separately, on two probes of one acquisition.
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
