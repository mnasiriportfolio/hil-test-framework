from hiltf.layer2_engine import EventBus, ReportRecorder
from hiltf.layer2_engine.report_recorder import RESULT_TOPIC


def test_publish_reaches_subscriber():
    bus = EventBus()
    seen = []
    bus.subscribe("x", lambda e: seen.append(e.payload["v"]))
    bus.publish("x", v=42)
    assert seen == [42]


def test_recorder_collects_and_grades():
    bus = EventBus()
    rec = ReportRecorder(bus)
    bus.publish(RESULT_TOPIC, suite="S", case="c", check="a",
                expected="1", measured="1", outcome="PASS")
    bus.publish(RESULT_TOPIC, suite="S", case="c", check="b",
                expected="2", measured="9", outcome="CHECK")
    assert len(rec.cases) == 1
    assert rec.cases[0].outcome == "CHECK"  # CHECK downgrades PASS but not to FAIL
    assert rec.all_passed is True


def test_recorder_marks_fail():
    bus = EventBus()
    rec = ReportRecorder(bus)
    bus.publish(RESULT_TOPIC, suite="S", case="c", check="a",
                expected="1", measured="9", outcome="FAIL")
    assert rec.cases[0].outcome == "FAIL"
    assert rec.all_passed is False
