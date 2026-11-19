"""Application service: binder domænelogik, database og notifikation sammen.

Hvert trin i flowet gemmes som en domain event i tabellen `events`, så man
kan følge forløbet i databasen bagefter.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from .domain import SensorValue, assess, response_due

log = logging.getLogger(__name__)


class AlarmNotFound(LookupError):
    pass


class AlarmAlreadyAcknowledged(RuntimeError):
    pass


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def register_sensor_value(reading: SensorValue, repo, notifier) -> dict:
    """Command: Register Sensor Value."""
    now = datetime.now(timezone.utc)
    base = {"turbine_id": reading.turbine_id, "sensor_type": reading.sensor_type,
            "value": reading.value, "measured_at": _iso(reading.timestamp)}
    events = ["SensorValueReceived"]
    repo.add_event("SensorValueReceived", reading.turbine_id, base, now)

    result = assess(reading)

    if result.outcome == "sensor_fault":
        repo.add_event("SensorFaultSuspected", reading.turbine_id,
                       {**base, "reason": result.reason}, now)
        events.append("SensorFaultSuspected")
        log.warning("Sensorfejl mistænkt: %s %s", reading.turbine_id, result.reason)
        return {"outcome": "sensor_fault", "reason": result.reason, "events": events, "alarm": None}

    if result.outcome == "normal":
        return {"outcome": "normal", "reason": result.reason, "events": events, "alarm": None}

    # Anomali: AnomalyDetected -> AlarmRaised (to events, samme kald)
    repo.add_event("AnomalyDetected", reading.turbine_id,
                   {**base, "threshold": result.threshold, "priority": result.priority,
                    "reason": result.reason}, now)
    events.append("AnomalyDetected")

    alarm = {
        "turbine_id": reading.turbine_id,
        "sensor_type": reading.sensor_type,
        "value": reading.value,
        "threshold": result.threshold,
        "priority": result.priority,
        "status": "raised",
        "reason": result.reason,
        "measured_at": reading.timestamp,
        "raised_at": now,
        "response_due_at": response_due(result.priority, now),
    }
    alarm["id"] = repo.add_alarm(alarm)
    repo.add_event("AlarmRaised", reading.turbine_id,
                   {"alarm_id": alarm["id"], "priority": result.priority,
                    "response_due_at": _iso(alarm["response_due_at"])}, now)
    events.append("AlarmRaised")
    log.info("Alarm %s rejst for %s (%s)", alarm["id"], reading.turbine_id, result.priority)

    # Handling: notifikation. En fejlet mail må ikke koste alarmen.
    try:
        notifier.send_alarm(alarm)
        repo.add_event("NotificationSent", reading.turbine_id, {"alarm_id": alarm["id"]}, now)
        events.append("NotificationSent")
    except Exception as exc:  # noqa: BLE001 - alle fejl fra SMTP skal fanges her
        log.error("Notifikation for alarm %s fejlede: %s", alarm["id"], type(exc).__name__)
        repo.add_event("NotificationFailed", reading.turbine_id,
                       {"alarm_id": alarm["id"], "error": type(exc).__name__}, now)
        events.append("NotificationFailed")

    return {"outcome": "anomaly", "reason": result.reason, "events": events,
            "alarm": serialize_alarm(alarm)}


def acknowledge_alarm(alarm_id: int, acknowledged_by: str, repo) -> dict:
    """Command: Acknowledge Alarm. En alarm kan kun kvitteres én gang."""
    alarm = repo.get_alarm(alarm_id)
    if alarm is None:
        raise AlarmNotFound(alarm_id)
    if alarm["status"] != "raised":
        raise AlarmAlreadyAcknowledged(alarm_id)

    now = datetime.now(timezone.utc)
    if not repo.acknowledge_alarm(alarm_id, acknowledged_by, now):
        raise AlarmAlreadyAcknowledged(alarm_id)  # en anden nåede først
    repo.add_event("AlarmAcknowledged", alarm["turbine_id"],
                   {"alarm_id": alarm_id, "acknowledged_by": acknowledged_by}, now)
    return serialize_alarm(repo.get_alarm(alarm_id))


def serialize_alarm(alarm: dict) -> dict:
    out = dict(alarm)
    for key in ("measured_at", "raised_at", "response_due_at", "acknowledged_at"):
        if isinstance(out.get(key), datetime):
            dt = out[key]
            if dt.tzinfo is None:  # MySQL DATETIME har ingen tidszone; vi gemmer UTC
                dt = dt.replace(tzinfo=timezone.utc)
            out[key] = _iso(dt)
    return out
