"""Persistering. MySqlRepository bruges i drift, InMemoryRepository i tests.

Al SQL er parametriseret (%s-pladsholdere), så værdier aldrig bliver til kode.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

import pymysql
from pymysql.cursors import DictCursor

ALARM_COLUMNS = ("id, turbine_id, sensor_type, value, threshold, priority, status, reason, "
                 "measured_at, raised_at, response_due_at, acknowledged_at, acknowledged_by")


def _naive_utc(dt: datetime | None) -> datetime | None:
    """MySQL DATETIME gemmer ikke tidszone. Vi gemmer altid UTC."""
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


class MySqlRepository:
    def __init__(self, host: str, port: int, user: str, password: str, database: str):
        self._params = dict(host=host, port=port, user=user, password=password,
                            database=database, cursorclass=DictCursor, autocommit=True,
                            charset="utf8mb4", connect_timeout=5)

    def _conn(self):
        return pymysql.connect(**self._params)

    def ping(self) -> bool:
        with self._conn() as con, con.cursor() as cur:
            cur.execute("SELECT 1")
            return True

    def add_event(self, event_type: str, turbine_id: str, payload: dict, occurred_at: datetime) -> int:
        with self._conn() as con, con.cursor() as cur:
            cur.execute(
                "INSERT INTO events (event_type, turbine_id, payload, occurred_at) VALUES (%s, %s, %s, %s)",
                (event_type, turbine_id, json.dumps(payload, ensure_ascii=False), _naive_utc(occurred_at)),
            )
            return cur.lastrowid

    def add_alarm(self, a: dict) -> int:
        with self._conn() as con, con.cursor() as cur:
            cur.execute(
                "INSERT INTO alarms (turbine_id, sensor_type, value, threshold, priority, status, reason, "
                "measured_at, raised_at, response_due_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (a["turbine_id"], a["sensor_type"], a["value"], a["threshold"], a["priority"], a["status"],
                 a["reason"], _naive_utc(a["measured_at"]), _naive_utc(a["raised_at"]),
                 _naive_utc(a["response_due_at"])),
            )
            return cur.lastrowid

    def get_alarm(self, alarm_id: int) -> dict | None:
        with self._conn() as con, con.cursor() as cur:
            cur.execute(f"SELECT {ALARM_COLUMNS} FROM alarms WHERE id = %s", (alarm_id,))
            return cur.fetchone()

    def list_alarms(self, status: str | None = None, turbine_id: str | None = None, limit: int = 100) -> list[dict]:
        sql = f"SELECT {ALARM_COLUMNS} FROM alarms WHERE 1=1"
        args: list = []
        if status:
            sql += " AND status = %s"
            args.append(status)
        if turbine_id:
            sql += " AND turbine_id = %s"
            args.append(turbine_id)
        sql += " ORDER BY id DESC LIMIT %s"
        args.append(limit)
        with self._conn() as con, con.cursor() as cur:
            cur.execute(sql, args)
            return list(cur.fetchall())

    def acknowledge_alarm(self, alarm_id: int, by: str, at: datetime) -> bool:
        with self._conn() as con, con.cursor() as cur:
            cur.execute(
                "UPDATE alarms SET status = 'acknowledged', acknowledged_by = %s, acknowledged_at = %s "
                "WHERE id = %s AND status = 'raised'",
                (by, _naive_utc(at), alarm_id),
            )
            return cur.rowcount == 1

    def list_events(self, turbine_id: str | None = None, limit: int = 100) -> list[dict]:
        sql = "SELECT id, event_type, turbine_id, payload, occurred_at FROM events"
        args: list = []
        if turbine_id:
            sql += " WHERE turbine_id = %s"
            args.append(turbine_id)
        sql += " ORDER BY id DESC LIMIT %s"
        args.append(limit)
        with self._conn() as con, con.cursor() as cur:
            cur.execute(sql, args)
            rows = list(cur.fetchall())
        for r in rows:
            if isinstance(r["payload"], (str, bytes)):
                r["payload"] = json.loads(r["payload"])
            r["occurred_at"] = r["occurred_at"].replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        return rows


class InMemoryRepository:
    """Samme metoder som MySqlRepository, uden database. Til unit tests."""

    def __init__(self):
        self.events: list[dict] = []
        self.alarms: dict[int, dict] = {}
        self._lock = threading.Lock()

    def ping(self) -> bool:
        return True

    def add_event(self, event_type, turbine_id, payload, occurred_at):
        with self._lock:
            eid = len(self.events) + 1
            self.events.append({"id": eid, "event_type": event_type, "turbine_id": turbine_id,
                                "payload": payload, "occurred_at": occurred_at.isoformat()})
            return eid

    def add_alarm(self, a):
        with self._lock:
            aid = len(self.alarms) + 1
            self.alarms[aid] = {**a, "id": aid, "acknowledged_at": None, "acknowledged_by": None}
            return aid

    def get_alarm(self, alarm_id):
        a = self.alarms.get(alarm_id)
        return dict(a) if a else None

    def list_alarms(self, status=None, turbine_id=None, limit=100):
        rows = [dict(a) for a in reversed(list(self.alarms.values()))
                if (not status or a["status"] == status) and (not turbine_id or a["turbine_id"] == turbine_id)]
        return rows[:limit]

    def acknowledge_alarm(self, alarm_id, by, at):
        with self._lock:
            a = self.alarms.get(alarm_id)
            if not a or a["status"] != "raised":
                return False
            a.update(status="acknowledged", acknowledged_by=by, acknowledged_at=at)
            return True

    def list_events(self, turbine_id=None, limit=100):
        rows = [e for e in reversed(self.events) if not turbine_id or e["turbine_id"] == turbine_id]
        return rows[:limit]
