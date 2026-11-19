"""Kører hele forløbet mod et kørende API og viser resultatet.

Brug: python scripts/smoke_test.py [--url http://localhost:5000] [--mailpit http://localhost:8025]
Nøglerne læses fra .env (lokalt) eller miljøvariablerne DEVICE_API_KEY og OPERATOR_API_KEY.
"""
import argparse
import os
import sys

import requests

sys.stdout.reconfigure(encoding="utf-8")
p = argparse.ArgumentParser()
p.add_argument("--url", default="http://localhost:5000")
p.add_argument("--mailpit", default="http://localhost:8025")
p.add_argument("--env", default=os.path.join(os.path.dirname(__file__), "..", ".env"))
a = p.parse_args()

env = {}
if os.path.exists(a.env):
    env = dict(l.split("=", 1) for l in open(a.env, encoding="utf-8").read().splitlines()
               if "=" in l and not l.startswith("#"))
DEV = {"X-API-Key": os.environ.get("DEVICE_API_KEY") or env["DEVICE_API_KEY"]}
OPS = {"X-API-Key": os.environ.get("OPERATOR_API_KEY") or env["OPERATOR_API_KEY"]}
U = a.url.rstrip("/")
fejl = 0


def check(navn, ok, detalje=""):
    global fejl
    fejl += not ok
    print(f"{'OK  ' if ok else 'FEJL'} {navn} {detalje}")


h = requests.get(U + "/health", timeout=10)
check("health", h.status_code == 200, h.json())

forventet = {64.0: "normal", 97.5: "anomaly", -60.0: "sensor_fault"}
alarm_id = None
for v, outcome in forventet.items():
    r = requests.post(U + "/sensor-values", headers=DEV, timeout=10,
                      json={"turbine_id": "WT-017", "sensor_type": "gearbox_temperature", "value": v})
    b = r.json()
    check(f"måling {v}", r.status_code == 201 and b.get("outcome") == outcome, b.get("events", b))
    if b.get("alarm"):
        alarm_id = b["alarm"]["id"]

check("uden nøgle giver 401", requests.post(U + "/sensor-values", json={}, timeout=10).status_code == 401)
check("SQL injection giver 422", requests.post(U + "/sensor-values", headers=DEV, timeout=10,
      json={"turbine_id": "x' OR '1'='1", "sensor_type": "vibration", "value": 3}).status_code == 422)

r = requests.get(f"{U}/alarms/{alarm_id}", headers=OPS, timeout=10)
check("alarm kan hentes", r.status_code == 200 and r.json()["status"] == "raised",
      {k: r.json().get(k) for k in ("id", "priority", "response_due_at")})
r = requests.patch(f"{U}/alarms/{alarm_id}/acknowledge", headers=OPS, json={"acknowledged_by": "Danny"}, timeout=10)
check("kvittering", r.status_code == 200 and r.json()["status"] == "acknowledged")
check("kvittering igen giver 409", requests.patch(f"{U}/alarms/{alarm_id}/acknowledge", headers=OPS,
      json={"acknowledged_by": "Danny"}, timeout=10).status_code == 409)

events = [e["event_type"] for e in requests.get(U + "/events?limit=8", headers=OPS, timeout=10).json()]
check("events gemt i databasen", "AlarmRaised" in events and "AlarmAcknowledged" in events, events)

if a.mailpit:
    try:
        m = requests.get(a.mailpit.rstrip("/") + "/api/v1/messages", timeout=10).json()
        subj = [x["Subject"] for x in m["messages"]]
        check("mail i Mailpit", any(f"Alarm {alarm_id}:" in s for s in subj), subj[:1])
    except requests.RequestException as exc:
        check("mail i Mailpit", False, type(exc).__name__)

print("\nAlt virker." if not fejl else f"\n{fejl} tjek fejlede.")
sys.exit(1 if fejl else 0)
