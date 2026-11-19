"""IoT-simulator: spiller vindmølle og sender sensorværdier til API'et.

Kør fra en anden computer for at vise, at API'et er remote:
    python simulator.py --url http://<api-adresse>:5000 --key <DEVICE_API_KEY>

Scenarier:
    --scenario normal     kun normale værdier
    --scenario anomaly    en normal værdi, derefter en kritisk gearkassetemperatur
    --scenario fault      en fysisk umulig værdi (sensorfejl, ingen alarm)
    --scenario random     løbende tilfældige værdier (Ctrl+C stopper)
"""
import argparse
import os
import random
import time
from datetime import datetime, timezone

import requests


def send(url, key, turbine_id, sensor_type, value):
    body = {"turbine_id": turbine_id, "sensor_type": sensor_type, "value": round(value, 1),
            "timestamp": datetime.now(timezone.utc).isoformat()}
    r = requests.post(f"{url}/sensor-values", json=body, headers={"X-API-Key": key}, timeout=10)
    try:
        data = r.json()
    except ValueError:
        data = {}
    print(f"{r.status_code}  {turbine_id} {sensor_type}={body['value']:>6}  ->  "
          f"{data.get('outcome', data.get('error'))}  {data.get('events', '')}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default=os.environ.get("API_URL", "http://localhost:5000"))
    p.add_argument("--key", default=os.environ.get("DEVICE_API_KEY"))
    p.add_argument("--turbine", default="WT-017")
    p.add_argument("--scenario", choices=["normal", "anomaly", "fault", "random"], default="anomaly")
    a = p.parse_args()
    if not a.key:
        raise SystemExit("Mangler nøgle: brug --key eller sæt DEVICE_API_KEY")

    if a.scenario == "normal":
        for _ in range(3):
            send(a.url, a.key, a.turbine, "gearbox_temperature", random.uniform(55, 70))
    elif a.scenario == "anomaly":
        send(a.url, a.key, a.turbine, "gearbox_temperature", 64.0)
        send(a.url, a.key, a.turbine, "gearbox_temperature", 97.5)
    elif a.scenario == "fault":
        send(a.url, a.key, a.turbine, "gearbox_temperature", -60.0)
    else:
        turbines = [f"WT-{n:03d}" for n in range(1, 6)]
        while True:
            sensor = random.choice(["gearbox_temperature", "generator_temperature", "vibration"])
            value = {"gearbox_temperature": random.gauss(65, 10),
                     "generator_temperature": random.gauss(85, 15),
                     "vibration": abs(random.gauss(4, 2.5))}[sensor]
            send(a.url, a.key, random.choice(turbines), sensor, value)
            time.sleep(2)


if __name__ == "__main__":
    main()
