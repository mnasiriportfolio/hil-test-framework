*** Settings ***
Documentation     Line detection — verify the device knows when it is connected to a
...               live line: where it picks up, that it drops out lower than it picks
...               up, that a short supply hole does not disturb it, and that both
...               transitions happen inside their time limits.
...
...               This suite says nothing about how the bench is reached. Run it
...               against the in-process simulation, a raw TCP/SCPI rack, PyVISA or
...               the containerised emulator by changing ${CONFIG} only.
Library           HiltfLibrary.py
Suite Setup       Open Bench          ${CONFIG}
Suite Teardown    Run Keywords        Write Report    reports/line_detection.md    AND    Close Bench


*** Variables ***
${CONFIG}         config/bench_config.yaml


*** Test Cases ***
Bench Is Reachable
    [Documentation]    Every instrument answers its identity query before any
    ...                measurement is trusted.
    [Tags]             smoke
    Log Bench Identities

Line Detection All Scenarios
    [Documentation]    Every enabled row in the line-detection plan.
    Run All Cases      line_detection    config/line_detection_plan.csv
    All Cases Passed
