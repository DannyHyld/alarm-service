from datetime import datetime, timezone

import pytest

from app.domain import SensorValue, ValidationError, assess, response_due


def reading(value, sensor_type="gearbox_temperature"):
    return SensorValue("WT-017", sensor_type, value, datetime.now(timezone.utc))


def test_normal_value_gives_no_anomaly():
    assert assess(reading(60)).outcome == "normal"


def test_warning_threshold_gives_high_priority():
    a = assess(reading(80))
    assert (a.outcome, a.priority, a.threshold) == ("anomaly", "høj", 80)


def test_critical_threshold_gives_critical_priority():
    a = assess(reading(97.5))
    assert (a.outcome, a.priority) == ("anomaly", "kritisk")


def test_physically_impossible_value_is_sensor_fault_not_anomaly():
    # Hot spottet fra Delopgave 1: -60 °C i en gearkasse er en sensorfejl.
    assert assess(reading(-60)).outcome == "sensor_fault"
    assert assess(reading(400)).outcome == "sensor_fault"


def test_vibration_thresholds():
    assert assess(reading(5, "vibration")).outcome == "normal"
    assert assess(reading(8, "vibration")).priority == "høj"
    assert assess(reading(12, "vibration")).priority == "kritisk"


def test_response_due_follows_priority():
    t = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)
    assert response_due("kritisk", t) == datetime(2026, 11, 20, 14, 0, tzinfo=timezone.utc)
    assert response_due("høj", t) == datetime(2026, 11, 21, 10, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("body", [
    None,
    {"turbine_id": "<script>alert(1)</script>", "sensor_type": "vibration", "value": 3},
    {"turbine_id": "x' OR '1'='1", "sensor_type": "vibration", "value": 3},
    {"turbine_id": "WT-017", "sensor_type": "wind_speed", "value": 3},
    {"turbine_id": "WT-017", "sensor_type": "vibration", "value": "høj"},
    {"turbine_id": "WT-017", "sensor_type": "vibration", "value": True},
    {"turbine_id": "WT-017", "sensor_type": "vibration", "value": -9999},
    {"turbine_id": "WT-017", "sensor_type": "vibration", "value": 3, "timestamp": "i går"},
])
def test_invalid_input_is_rejected(body):
    with pytest.raises(ValidationError):
        SensorValue.from_dict(body)


def test_timestamp_without_zone_is_utc():
    r = SensorValue.from_dict({"turbine_id": "WT-017", "sensor_type": "vibration",
                               "value": 3, "timestamp": "2026-11-20T10:15:00"})
    assert r.timestamp.tzinfo is not None
