"""Alarm-servicen: Flask-app for bounded contexten Condition Monitoring."""
from __future__ import annotations

import json
import logging
import os
import secrets
import sys
import time
import uuid
from functools import wraps

import pymysql
from flask import Flask, g, jsonify, request, send_from_directory
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Counter, Gauge, Histogram, generate_latest
from prometheus_client.core import GaugeMetricFamily
from werkzeug.exceptions import HTTPException

from . import service
from .domain import SENSOR_RULES, SensorValue, ValidationError

log = logging.getLogger("alarm_service")

SENSOR_VALUES = Counter("sensor_values_total", "Modtagne sensorværdier", ["outcome"])
ALARMS_RAISED = Counter("alarms_raised_total", "Rejste alarmer", ["priority"])
NOTIFICATIONS = Counter("notifications_total", "Notifikationer", ["result"])
REQUEST_SECONDS = Histogram("http_request_seconds", "Svartid", ["method", "endpoint", "status"])
SENSOR_VALUE = Gauge("sensor_value", "Seneste sensorværdi pr. mølle og sensortype", ["turbine_id", "sensor_type"])


class OpenAlarmsCollector:
    """alarms_open{priority}: antal ukvitterede alarmer, talt i databasen ved hver scrape.

    Tælleren alarms_raised_total nulstilles, når API'et genstarter. Antallet af
    åbne alarmer står i databasen og overlever genstarter, så det er det tal,
    vagten skal se på dashboardet.
    """

    repo = None

    def collect(self):
        family = GaugeMetricFamily("alarms_open", "Åbne (ukvitterede) alarmer", labels=["priority"])
        if self.repo is not None:
            try:
                counts = self.repo.count_open_alarms()
                for priority in ("høj", "kritisk"):
                    family.add_metric([priority], counts.get(priority, 0))
            except Exception as exc:  # noqa: BLE001 - en scrape må aldrig vælte API'et
                log.warning("alarms_open kunne ikke tælles: %s", type(exc).__name__)
        yield family


OPEN_ALARMS = OpenAlarmsCollector()
REGISTRY.register(OPEN_ALARMS)


class JsonFormatter(logging.Formatter):
    """Én JSON-linje pr. log, så Docker/Azure/Grafana kan søge i dem."""

    def format(self, record):
        entry = {"time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"), "level": record.levelname,
                 "logger": record.name, "msg": record.getMessage()}
        rid = getattr(record, "request_id", None)
        if rid:
            entry["request_id"] = rid
        if record.exc_info:
            entry["error"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def _setup_logging():
    root = logging.getLogger()
    if not any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        root.handlers = [handler]
    root.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


def _build_repo():
    from .repository import MySqlRepository
    return MySqlRepository(
        host=os.environ.get("MYSQL_HOST", "localhost"),
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ.get("MYSQL_DATABASE", "alarm_service"),
    )


def _build_notifier():
    from .notifier import EmailNotifier, NullNotifier
    host = os.environ.get("SMTP_HOST")
    if not host:
        return NullNotifier()
    return EmailNotifier(
        host=host,
        port=int(os.environ.get("SMTP_PORT", "1025")),
        sender=os.environ.get("ALARM_EMAIL_FROM", "alarm@intelligent-iot.example"),
        recipients=[r.strip() for r in os.environ.get("ALARM_EMAIL_TO", "vagt@intelligent-iot.example").split(",")],
        username=os.environ.get("SMTP_USER") or None,
        password=os.environ.get("SMTP_PASSWORD") or None,
        use_tls=os.environ.get("SMTP_TLS", "false").lower() == "true",
    )


def create_app(repo=None, notifier=None, config: dict | None = None) -> Flask:
    _setup_logging()
    app = Flask(__name__, static_folder=None)
    app.config.update(
        DEVICE_API_KEY=os.environ.get("DEVICE_API_KEY"),
        OPERATOR_API_KEY=os.environ.get("OPERATOR_API_KEY"),
        RATELIMIT_DEFAULT=os.environ.get("RATE_LIMIT", "120 per minute"),
        RATELIMIT_STORAGE_URI="memory://",
        MAX_CONTENT_LENGTH=16 * 1024,
    )
    if config:
        app.config.update(config)

    app.extensions["repo"] = repo if repo is not None else _build_repo()
    OPEN_ALARMS.repo = app.extensions["repo"]
    app.extensions["notifier"] = notifier if notifier is not None else _build_notifier()
    limiter = Limiter(get_remote_address, app=app, default_limits=[app.config["RATELIMIT_DEFAULT"]])

    def repo_():
        return app.extensions["repo"]

    def _limit() -> int:
        raw = request.args.get("limit", "100")
        if not raw.isdigit() or not 1 <= int(raw) <= 500:
            raise ValidationError("limit skal være et tal mellem 1 og 500")
        return int(raw)

    # ---------- sikkerhed ----------
    def require_key(config_name: str):
        def deco(fn):
            @wraps(fn)
            def wrapper(*args, **kwargs):
                expected = app.config.get(config_name)
                given = request.headers.get("X-API-Key")
                if not expected or not given or not secrets.compare_digest(given, expected):
                    log.warning("Afvist kald uden gyldig nøgle til %s", request.path)
                    return jsonify(error="unauthorized", message="Ugyldig eller manglende API-nøgle"), 401
                return fn(*args, **kwargs)
            return wrapper
        return deco

    # ---------- logging pr. request ----------
    @app.before_request
    def _start():
        g.start = time.perf_counter()
        g.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]

    @app.after_request
    def _finish(resp):
        elapsed = time.perf_counter() - g.get("start", time.perf_counter())
        endpoint = request.url_rule.rule if request.url_rule else "unknown"
        REQUEST_SECONDS.labels(request.method, endpoint, resp.status_code).observe(elapsed)
        resp.headers["X-Request-ID"] = g.get("request_id", "")
        resp.headers["X-Content-Type-Options"] = "nosniff"
        if endpoint not in ("/metrics", "/health"):
            log.info("%s %s %s %.1fms", request.method, request.path, resp.status_code, elapsed * 1000,
                     extra={"request_id": g.get("request_id")})
        return resp

    # ---------- fejl: aldrig stack traces ud til klienten ----------
    @app.errorhandler(ValidationError)
    def _validation(exc):
        return jsonify(error="validation_error", message=str(exc)), 422

    @app.errorhandler(HTTPException)
    def _http(exc):
        return jsonify(error=exc.name.lower().replace(" ", "_"), message=exc.description), exc.code

    @app.errorhandler(pymysql.err.OperationalError)
    def _database_down(exc):
        log.error("Databasen svarer ikke: %s", exc.args[0] if exc.args else type(exc).__name__,
                  extra={"request_id": g.get("request_id")})
        return jsonify(error="service_unavailable", message="Databasen svarer ikke. Prøv igen om lidt.",
                       request_id=g.get("request_id")), 503

    @app.errorhandler(Exception)
    def _unexpected(exc):
        log.exception("Uventet fejl", extra={"request_id": g.get("request_id")})
        return jsonify(error="internal_error", message="Der skete en fejl. Oplys request-id.",
                       request_id=g.get("request_id")), 500

    # ---------- endpoints ----------
    @app.get("/")
    def index():
        return jsonify(service="alarm-service", bounded_context="Condition Monitoring",
                       docs="/docs", health="/health")

    @app.get("/health")
    @limiter.exempt
    def health():
        try:
            repo_().ping()
            return jsonify(status="ok", database="ok")
        except Exception as exc:  # noqa: BLE001
            log.error("Health check: database utilgængelig (%s)", type(exc).__name__)
            return jsonify(status="degraded", database="unavailable"), 503

    @app.get("/metrics")
    @limiter.exempt
    def metrics():
        return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}

    @app.get("/sensor-types")
    def sensor_types():
        return jsonify({k: {"unit": r.unit, "warning": r.warning, "critical": r.critical,
                            "physical_min": r.physical_min, "physical_max": r.physical_max}
                        for k, r in SENSOR_RULES.items()})

    @app.post("/sensor-values")
    @require_key("DEVICE_API_KEY")
    def post_sensor_value():
        reading = SensorValue.from_dict(request.get_json(silent=True))
        result = service.register_sensor_value(reading, repo_(), app.extensions["notifier"])
        SENSOR_VALUES.labels(result["outcome"]).inc()
        if result["outcome"] != "sensor_fault":  # en umulig værdi skal ikke tegnes som en måling
            SENSOR_VALUE.labels(reading.turbine_id, reading.sensor_type).set(reading.value)
        if result["alarm"]:
            ALARMS_RAISED.labels(result["alarm"]["priority"]).inc()
            NOTIFICATIONS.labels("sent" if "NotificationSent" in result["events"] else "failed").inc()
        return jsonify(result), 201

    @app.get("/alarms")
    @require_key("OPERATOR_API_KEY")
    def get_alarms():
        status = request.args.get("status")
        if status and status not in ("raised", "acknowledged"):
            raise ValidationError("status skal være raised eller acknowledged")
        turbine_id = request.args.get("turbine_id")
        rows = repo_().list_alarms(status=status, turbine_id=turbine_id,
                                   limit=_limit())
        return jsonify([service.serialize_alarm(r) for r in rows])

    @app.get("/alarms/<int:alarm_id>")
    @require_key("OPERATOR_API_KEY")
    def get_alarm(alarm_id):
        alarm = repo_().get_alarm(alarm_id)
        if alarm is None:
            return jsonify(error="not_found", message=f"Alarm {alarm_id} findes ikke"), 404
        return jsonify(service.serialize_alarm(alarm))

    @app.patch("/alarms/<int:alarm_id>/acknowledge")
    @require_key("OPERATOR_API_KEY")
    def acknowledge(alarm_id):
        body = request.get_json(silent=True) or {}
        by = body.get("acknowledged_by")
        if not isinstance(by, str) or not 2 <= len(by) <= 60:
            raise ValidationError("acknowledged_by skal være et navn på 2-60 tegn")
        try:
            return jsonify(service.acknowledge_alarm(alarm_id, by, repo_()))
        except service.AlarmNotFound:
            return jsonify(error="not_found", message=f"Alarm {alarm_id} findes ikke"), 404
        except service.AlarmAlreadyAcknowledged:
            return jsonify(error="conflict", message=f"Alarm {alarm_id} er allerede kvitteret"), 409

    @app.get("/events")
    @require_key("OPERATOR_API_KEY")
    def get_events():
        rows = repo_().list_events(turbine_id=request.args.get("turbine_id"),
                                   limit=_limit())
        return jsonify(rows)

    # ---------- dokumentation ----------
    docs_dir = os.path.join(os.path.dirname(__file__), "docs")

    @app.get("/openapi.yaml")
    def openapi():
        return send_from_directory(docs_dir, "openapi.yaml", mimetype="application/yaml")

    @app.get("/docs")
    def docs():
        return send_from_directory(docs_dir, "swagger.html")

    return app
