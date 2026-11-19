"""Integrationstest mod en rigtig MySQL oprettet med db/init.sql.

Unit testene bruger InMemoryRepository og fangede derfor ikke, at MySQL gemte
ENUM-værdien 'høj' forkert (19/11). Denne test kører kun, når MYSQL_TEST_HOST er
sat. I CI starter pipelinen MySQL med Docker Compose, præcis som i drift.

Lokalt (mod databasen i docker compose, port 3307):
    MYSQL_TEST_HOST=127.0.0.1 MYSQL_TEST_PORT=3307 MYSQL_TEST_USER=alarm_app \
    MYSQL_TEST_PASSWORD=<fra .env> MYSQL_TEST_DATABASE=alarm_service pytest tests/test_mysql_integration.py
"""
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.repository import MySqlRepository

pytestmark = pytest.mark.skipif(not os.environ.get("MYSQL_TEST_HOST"), reason="MYSQL_TEST_HOST er ikke sat")


@pytest.fixture
def repo():
    return MySqlRepository(
        host=os.environ["MYSQL_TEST_HOST"], port=int(os.environ.get("MYSQL_TEST_PORT", "3306")),
        user=os.environ["MYSQL_TEST_USER"], password=os.environ["MYSQL_TEST_PASSWORD"],
        database=os.environ.get("MYSQL_TEST_DATABASE", "alarm_service"))


def alarm(turbine_id, priority):
    now = datetime.now(timezone.utc)
    return {"turbine_id": turbine_id, "sensor_type": "vibration", "value": 8.4, "threshold": 7.1,
            "priority": priority, "status": "raised", "reason": "8.4 mm/s er over advarselsgrænsen",
            "measured_at": now, "raised_at": now, "response_due_at": now + timedelta(hours=24)}


@pytest.mark.parametrize("priority", ["høj", "kritisk"])
def test_alarm_with_each_priority_can_be_stored_and_read(repo, priority):
    turbine = "IT-" + uuid.uuid4().hex[:8]
    alarm_id = repo.add_alarm(alarm(turbine, priority))
    stored = repo.get_alarm(alarm_id)
    assert stored["priority"] == priority
    assert [a["id"] for a in repo.list_alarms(turbine_id=turbine)] == [alarm_id]


def test_open_alarm_count_and_acknowledge(repo):
    turbine = "IT-" + uuid.uuid4().hex[:8]
    alarm_id = repo.add_alarm(alarm(turbine, "høj"))
    before = repo.count_open_alarms().get("høj", 0)
    assert repo.acknowledge_alarm(alarm_id, "CI", datetime.now(timezone.utc)) is True
    assert repo.acknowledge_alarm(alarm_id, "CI", datetime.now(timezone.utc)) is False
    assert repo.count_open_alarms().get("høj", 0) == before - 1
    assert repo.list_alarms(status="acknowledged", turbine_id=turbine)[0]["acknowledged_by"] == "CI"


def test_event_payload_keeps_danish_characters(repo):
    turbine = "IT-" + uuid.uuid4().hex[:8]
    repo.add_event("AnomalyDetected", turbine, {"reason": "over grænsen", "priority": "høj"},
                   datetime.now(timezone.utc))
    event = repo.list_events(turbine_id=turbine)[0]
    assert event["payload"] == {"reason": "over grænsen", "priority": "høj"}
