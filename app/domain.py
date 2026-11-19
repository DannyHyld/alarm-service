"""Domænelogik for Condition Monitoring.

Ren Python uden Flask og database, så reglerne kan testes alene.
Navnene følger Event Stormingen: SensorValueReceived -> threshold-check ->
AnomalyDetected -> AlarmRaised, eller SensorFaultSuspected ved en umulig værdi.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

TURBINE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{3,40}$")


@dataclass(frozen=True)
class SensorRule:
    unit: str
    physical_min: float  # under denne værdi er det en sensorfejl
    physical_max: float  # over denne værdi er det en sensorfejl
    warning: float       # fra denne værdi: anomali med prioritet "høj"
    critical: float      # fra denne værdi: anomali med prioritet "kritisk"


# Tærskler pr. sensortype. Værdierne er MVP-antagelser, ikke tal fra casen.
SENSOR_RULES: dict[str, SensorRule] = {
    "gearbox_temperature": SensorRule("°C", -40, 150, warning=80, critical=95),
    "generator_temperature": SensorRule("°C", -40, 180, warning=110, critical=130),
    "vibration": SensorRule("mm/s", 0, 50, warning=7.1, critical=11.0),
}

# Svarfrist fra serviceaftalen (Service Agreement). I MVP'en er den fast;
# i den fulde løsning slås den op i Salesforce via en ACL.
RESPONSE_TIME = {"kritisk": timedelta(hours=4), "høj": timedelta(hours=24)}


class ValidationError(ValueError):
    """Input, der ikke overholder kontrakten. Bliver til HTTP 422."""


@dataclass(frozen=True)
class SensorValue:
    turbine_id: str
    sensor_type: str
    value: float
    timestamp: datetime

    @staticmethod
    def from_dict(data: object) -> "SensorValue":
        if not isinstance(data, dict):
            raise ValidationError("Body skal være et JSON-objekt")

        turbine_id = data.get("turbine_id")
        if not isinstance(turbine_id, str) or not TURBINE_ID_PATTERN.match(turbine_id):
            raise ValidationError("turbine_id skal være 3-40 tegn: bogstaver, tal, - og _")

        sensor_type = data.get("sensor_type")
        if sensor_type not in SENSOR_RULES:
            raise ValidationError(f"sensor_type skal være en af {sorted(SENSOR_RULES)}")

        value = data.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValidationError("value skal være et tal")
        if not -1000 <= value <= 1000:
            raise ValidationError("value skal ligge mellem -1000 og 1000")

        raw_ts = data.get("timestamp")
        if raw_ts is None:
            timestamp = datetime.now(timezone.utc)
        else:
            try:
                timestamp = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValidationError("timestamp skal være ISO 8601, fx 2026-11-20T10:15:00Z") from exc
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)

        return SensorValue(turbine_id, sensor_type, float(value), timestamp)


@dataclass(frozen=True)
class Assessment:
    """Resultatet af threshold-checket."""
    outcome: str            # "normal", "anomaly" eller "sensor_fault"
    priority: str | None    # "høj" eller "kritisk" ved anomali
    threshold: float | None
    reason: str


def assess(reading: SensorValue) -> Assessment:
    """Threshold-check: beslutningen i Event Stormingen.

    Hot spottet fra Delopgave 1 ("er det en anomali eller en sensorfejl?")
    er løst som en regel: en fysisk umulig værdi er en sensorfejl og giver
    ingen alarm.
    """
    rule = SENSOR_RULES[reading.sensor_type]
    v = reading.value

    if v < rule.physical_min or v > rule.physical_max:
        return Assessment("sensor_fault", None, None,
                          f"{v} {rule.unit} er uden for det fysisk mulige "
                          f"({rule.physical_min} til {rule.physical_max} {rule.unit})")
    if v >= rule.critical:
        return Assessment("anomaly", "kritisk", rule.critical,
                          f"{v} {rule.unit} er over den kritiske grænse på {rule.critical} {rule.unit}")
    if v >= rule.warning:
        return Assessment("anomaly", "høj", rule.warning,
                          f"{v} {rule.unit} er over advarselsgrænsen på {rule.warning} {rule.unit}")
    return Assessment("normal", None, None, "Inden for grænserne")


def response_due(priority: str, raised_at: datetime) -> datetime:
    return raised_at + RESPONSE_TIME[priority]
