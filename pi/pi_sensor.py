"""IoT-enheden: Raspberry Pi med Sense HAT sender rigtige målinger til alarm-servicen.

Hvert 5. sekund:
  1. Temperaturen fra Sense HAT sendes som gearbox_temperature.
  2. Accelerometeret måles i 1 sekund. Rystelserne sendes som vibration.
  3. LED-matricen viser svaret fra API'et:
       grøn = normal, orange = høj, rød = kritisk, blå = sensorfejl, hvidt kryds = API'et svarer ikke

Joystick trykket ind (midten) sender en SIMULERET kritisk gearkassetemperatur (97,5 °C).
Den er kun til demo, så man kan vise en temperaturalarm uden at varme Pi'en op.

Vibrationen er en forenkling til demoen: RMS af accelerationen (uden tyngdekraft) i m/s²
sendes som vibrationsværdi. Stille: ca. 0. Let rystet: høj. Kraftigt rystet: kritisk.
Slås Pi'en hårdt (over 50), vurderer API'et det som en sensorfejl, ikke en alarm.

Konfiguration i ~/.alarm-pi.env (chmod 600, aldrig i git):
    API_URL=http://<vm>:5000
    DEVICE_API_KEY=<fra Key Vault: device-api-key>
    TURBINE_ID=WT-PI

Kør:  python3 pi_sensor.py            (rigtig Sense HAT)
      python3 pi_sensor.py --fake     (uden Pi, til test på en almindelig computer)
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

GREEN, ORANGE, RED, BLUE, WHITE, OFF = (0, 160, 0), (255, 120, 0), (200, 0, 0), (0, 0, 200), (120, 120, 120), (0, 0, 0)
SIMULATED_CRITICAL_TEMP = 97.5


class FakeSenseHat:
    """Opfører sig som Sense HAT, så programmet kan testes uden Pi."""

    def __init__(self):
        self._shake_until = 0.0
        self.stick = self

    def get_temperature(self):
        return 34.0 + random.uniform(-0.5, 0.5)

    def get_accelerometer_raw(self):
        # Rystelser i 3 sekunder hvert 20. sekund, så testen ser en vibrationsalarm.
        now = time.time()
        if int(now) % 20 == 0:
            self._shake_until = now + 3
        amp = 1.2 if now < self._shake_until else 0.004
        return {"x": random.gauss(0, amp), "y": random.gauss(0, amp), "z": 1 + random.gauss(0, amp)}

    def get_events(self):
        return []

    def clear(self, colour=OFF):
        print(f"    LED: {colour}")

    def set_pixels(self, pixels):
        print("    LED: kryds (fejl)")

    def show_message(self, text, **_):
        print(f"    LED: viser '{text}'")


def load_env(path):
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key, value)


def measure_vibration(sense, seconds=1.0, hz=50):
    """RMS af accelerationen uden tyngdekraft, i m/s². Forenklet vibrationsmål til demoen."""
    samples = []
    end = time.time() + seconds
    while time.time() < end:
        a = sense.get_accelerometer_raw()
        samples.append(math.sqrt(a["x"] ** 2 + a["y"] ** 2 + a["z"] ** 2))
        time.sleep(1 / hz)
    mean = sum(samples) / len(samples)
    rms_g = math.sqrt(sum((s - mean) ** 2 for s in samples) / len(samples))
    return round(rms_g * 9.81, 1)


def post(api_url, key, turbine_id, sensor_type, value):
    body = json.dumps({"turbine_id": turbine_id, "sensor_type": sensor_type, "value": value,
                       "timestamp": datetime.now(timezone.utc).isoformat()}).encode("utf-8")
    req = urllib.request.Request(api_url.rstrip("/") + "/sensor-values", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "X-API-Key": key})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def show(sense, result):
    if result is None:
        x = [WHITE if (i % 8 == i // 8 or i % 8 == 7 - i // 8) else OFF for i in range(64)]
        sense.set_pixels(x)
        return
    outcome = result.get("outcome")
    if outcome == "sensor_fault":
        sense.clear(BLUE)
    elif outcome == "anomaly":
        sense.clear(RED if result["alarm"]["priority"] == "kritisk" else ORANGE)
    else:
        sense.clear(GREEN)


def send(sense, args, sensor_type, value, label=""):
    try:
        result = post(args.api_url, args.key, args.turbine, sensor_type, value)
        alarm = result.get("alarm")
        extra = f"  ALARM {alarm['id']} ({alarm['priority']})" if alarm else ""
        print(f"{datetime.now():%H:%M:%S}  {sensor_type:<20} {value:>6} {label:<12} -> {result['outcome']}{extra}")
        return result
    except urllib.error.HTTPError as exc:
        print(f"{datetime.now():%H:%M:%S}  {sensor_type:<20} {value:>6} -> HTTP {exc.code}", file=sys.stderr)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"{datetime.now():%H:%M:%S}  API'et svarer ikke: {exc}", file=sys.stderr)
    return None


def worst(results):
    """Den alvorligste vurdering bestemmer LED-farven."""
    rank = {"normal": 0, "sensor_fault": 1, "anomaly": 2}
    ok = [r for r in results if r]
    if not ok:
        return None
    return max(ok, key=lambda r: (rank.get(r["outcome"], 0),
                                  1 if r.get("alarm") and r["alarm"]["priority"] == "kritisk" else 0))


def main():
    load_env(os.path.expanduser("~/.alarm-pi.env"))
    p = argparse.ArgumentParser()
    p.add_argument("--fake", action="store_true", help="kør uden Sense HAT")
    p.add_argument("--api-url", default=os.environ.get("API_URL", "http://localhost:5000"))
    p.add_argument("--key", default=os.environ.get("DEVICE_API_KEY"))
    p.add_argument("--turbine", default=os.environ.get("TURBINE_ID", "WT-PI"))
    p.add_argument("--interval", type=float, default=5.0)
    p.add_argument("--rounds", type=int, default=0, help="stop efter N runder (0 = kør altid)")
    args = p.parse_args()
    if not args.key:
        sys.exit("Mangler DEVICE_API_KEY (i ~/.alarm-pi.env eller --key)")

    if args.fake:
        sense = FakeSenseHat()
    else:
        from sense_hat import SenseHat  # findes kun på Raspberry Pi OS (sudo apt-get install sense-hat)
        sense = SenseHat()
        sense.low_light = True

    print(f"Sender til {args.api_url} som {args.turbine}. Ctrl+C stopper.")
    sense.show_message("OK", text_colour=GREEN, scroll_speed=0.05)
    n = 0
    while True:
        n += 1
        start = time.time()
        results = []
        for ev in sense.get_events():
            if ev.direction == "middle" and ev.action == "pressed":
                results.append(send(sense, args, "gearbox_temperature", SIMULATED_CRITICAL_TEMP, "(simuleret)"))
        results.append(send(sense, args, "gearbox_temperature", round(sense.get_temperature(), 1)))
        results.append(send(sense, args, "vibration", measure_vibration(sense)))
        show(sense, worst(results))
        if args.rounds and n >= args.rounds:
            break
        time.sleep(max(0.0, args.interval - (time.time() - start)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStoppet.")
