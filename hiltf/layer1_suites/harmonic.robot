*** Settings ***
Documentation     Harmonic detection — hold the line at DC, then inject alternating
...               current on the current input. On a DC line every ampere of AC is
...               contamination, which is what this detector watches for.
...
...               Verifies that it stays quiet below the trigger window, trips above
...               it, and that detection time, contact transit and hold are each
...               inside their own limit.
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
