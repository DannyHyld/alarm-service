import pytest

from app import create_app
from app.notifier import RecordingNotifier
from app.repository import InMemoryRepository

DEVICE = {"X-API-Key": "device-test-key"}
OPERATOR = {"X-API-Key": "operator-test-key"}


def make(fail_mail=False):
    repo, notifier = InMemoryRepository(), RecordingNotifier(fail=fail_mail)
    app = create_app(repo=repo, notifier=notifier, config={
        "DEVICE_API_KEY": "device-test-key", "OPERATOR_API_KEY": "operator-test-key", "TESTING": True})
    return app.test_client(), repo, notifier


@pytest.fixture
def client():
    return make()


def send(c, value, sensor_type="gearbox_temperature", headers=DEVICE):
    return c.post("/sensor-values", headers=headers,
                  json={"turbine_id": "WT-017", "sensor_type": sensor_type, "value": value})


def test_full_flow_anomaly_raises_alarm_and_sends_mail(client):
    c, repo, notifier = client
    r = send(c, 97.5)
    assert r.status_code == 201
    body = r.get_json()
    assert body["outcome"] == "anomaly"
    assert body["events"] == ["SensorValueReceived", "AnomalyDetected", "AlarmRaised", "NotificationSent"]
    assert body["alarm"]["priority"] == "kritisk"
    assert [e["event_type"] for e in repo.events] == body["events"]
    assert len(notifier.sent) == 1


def test_normal_value_is_stored_but_raises_no_alarm(client):
    c, repo, notifier = client
    body = send(c, 60).get_json()
    assert body["outcome"] == "normal" and body["alarm"] is None
    assert [e["event_type"] for e in repo.events] == ["SensorValueReceived"]
    assert notifier.sent == []


def test_sensor_fault_gives_no_alarm(client):
    c, repo, notifier = client
    body = send(c, -60).get_json()
    assert body["outcome"] == "sensor_fault"
    assert "SensorFaultSuspected" in body["events"]
    assert repo.alarms == {} and notifier.sent == []


def test_alarm_is_kept_when_mail_fails():
    c, repo, _ = make(fail_mail=True)
    body = send(c, 97.5).get_json()
    assert body["events"][-1] == "NotificationFailed"
    assert len(repo.alarms) == 1


def test_missing_or_wrong_key_is_rejected(client):
    c, _, _ = client
    assert send(c, 60, headers={}).status_code == 401
    assert send(c, 60, headers={"X-API-Key": "forkert"}).status_code == 401
    # enhedens nøgle må ikke læse alarmer
    assert c.get("/alarms", headers=DEVICE).status_code == 401


def test_invalid_input_gives_422(client):
    c, _, _ = client
    r = c.post("/sensor-values", headers=DEVICE,
               json={"turbine_id": "<script>", "sensor_type": "vibration", "value": 3})
    assert r.status_code == 422
    assert c.post("/sensor-values", headers=DEVICE, data="ikke json").status_code == 422


def test_list_and_acknowledge_alarm(client):
    c, _, _ = client
    alarm_id = send(c, 85).get_json()["alarm"]["id"]

    alarms = c.get("/alarms?status=raised", headers=OPERATOR).get_json()
    assert [a["id"] for a in alarms] == [alarm_id]

    r = c.patch(f"/alarms/{alarm_id}/acknowledge", headers=OPERATOR, json={"acknowledged_by": "Danny"})
    assert r.status_code == 200 and r.get_json()["status"] == "acknowledged"

    again = c.patch(f"/alarms/{alarm_id}/acknowledge", headers=OPERATOR, json={"acknowledged_by": "Danny"})
    assert again.status_code == 409

    assert c.get("/alarms?status=raised", headers=OPERATOR).get_json() == []
    events = [e["event_type"] for e in c.get("/events", headers=OPERATOR).get_json()]
    assert events[0] == "AlarmAcknowledged"


def test_unknown_alarm_gives_404(client):
    c, _, _ = client
    assert c.get("/alarms/999", headers=OPERATOR).status_code == 404
    assert c.patch("/alarms/999/acknowledge", headers=OPERATOR,
                   json={"acknowledged_by": "Danny"}).status_code == 404


def test_bad_query_params_give_422_not_500(client):
    c, _, _ = client
    assert c.get("/alarms?limit=abc", headers=OPERATOR).status_code == 422
    assert c.get("/alarms?status=slettet", headers=OPERATOR).status_code == 422


def test_health_docs_and_metrics_are_open(client):
    c, _, _ = client
    assert c.get("/health").get_json()["status"] == "ok"
    assert c.get("/docs").status_code == 200
    assert c.get("/openapi.yaml").status_code == 200
    send(c, 97.5)
    assert b"alarms_raised_total" in c.get("/metrics").data


def test_errors_do_not_leak_stack_traces(client):
    c, repo, _ = client

    def boom(*a, **k):
        raise RuntimeError("hemmelig intern detalje")
    repo.list_alarms = boom
    r = c.get("/alarms", headers=OPERATOR)
    assert r.status_code == 500
    assert b"hemmelig" not in r.data and "request_id" in r.get_json()


def test_database_down_gives_503_not_500(client):
    import pymysql
    c, repo, _ = client

    def down(*a, **k):
        raise pymysql.err.OperationalError(2003, "Can't connect to MySQL server")
    repo.add_event = down
    r = send(c, 60)
    assert r.status_code == 503
    assert r.get_json()["error"] == "service_unavailable"


def test_json_log_includes_traceback():
    import logging
    from app import JsonFormatter
    try:
        raise ValueError("testfejl")
    except ValueError:
        import sys
        rec = logging.LogRecord("x", logging.ERROR, "f", 1, "Uventet fejl", None, sys.exc_info())
    out = JsonFormatter().format(rec)
    assert "ValueError: testfejl" in out
