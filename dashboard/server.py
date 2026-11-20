"""Vindmølle-dashboardet: seks skærmbilleder for Horns Rev 1.

Kører på Raspberry Pi'en ved siden af pi_sensor.py. Browseren taler kun med denne server.
Serveren henter vejr (Open-Meteo og MET Norway), strømpris (Energi Data Service) og
alarmer og events fra alarm-servicen i Azure. Operatørnøglen bliver på Pi'en og sendes
aldrig til browseren. Svarene bliver gemt et stykke tid, så de gratis tjenester ikke får
et kald for hver skærmopdatering.

Konfiguration i ~/.alarm-pi.env (samme fil som pi_sensor.py):
    API_URL=http://<vm>:5000
    OPERATOR_API_KEY=<fra Key Vault: operator-api-key>
    DASHBOARD_PORT=8080          (valgfri)

Kør:  python3 server.py
Åbn:  http://<pi>:8080
Kræver kun Pythons standardbibliotek.
"""
from __future__ import annotations

import json
import mimetypes
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from zoneinfo import ZoneInfo
    DK = ZoneInfo("Europe/Copenhagen")
except Exception:  # Windows uden tzdata: vintertid er nok til en lokal test
    DK = timezone(timedelta(hours=1))

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
USER_AGENT = "e26-vindmoelle-dashboard/1.0 (skoleprojekt)"
LAT, LON = 55.49, 7.84  # Horns Rev 1 i Nordsøen ud for Blåvandshuk
MODELS = [
    ("ecmwf_ifs025", "ECMWF", "Europa"),
    ("icon_seamless", "ICON", "Tyskland, DWD"),
    ("gfs_seamless", "GFS", "USA, NOAA"),
    ("dmi_seamless", "HARMONIE", "Danmark, DMI"),
]


def load_env(path: str) -> None:
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key, value)


def fetch_json(url: str, headers: dict | None = None, timeout: float = 10):
    # Kun http og https. Uden tjekket kunne en file://-adresse i konfigurationen læse filer på Pi'en (Bandit B310).
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        raise ValueError(f"Kun http og https er tilladt: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    # Bandit B310 er håndteret: skemaet er tjekket ovenfor
    with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310
        return json.load(r)


# ---------------------------------------------------------------- cache
_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


def cached(key: str, ttl: float, fn) -> dict:
    """Returnerer et gemt svar, hvis det er nyt nok. Fejler kilden, bruges det gamle svar."""
    now = time.time()
    with _lock:
        hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    try:
        value = fn()
    except Exception as exc:  # netværk, timeout eller ændret format hos kilden
        print(f"{datetime.now():%H:%M:%S} {key}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        if hit:
            return {**hit[1], "stale": True}
        return {"error": f"{key} kunne ikke hentes ({type(exc).__name__})"}
    with _lock:
        _cache[key] = (now, value)
    return value


# ---------------------------------------------------------------- datakilder
def weather() -> dict:
    q = urllib.parse.urlencode({
        "latitude": LAT, "longitude": LON, "hourly": "wind_speed_80m,wind_direction_80m",
        "models": ",".join(m[0] for m in MODELS), "wind_speed_unit": "ms",
        "timezone": "Europe/Copenhagen", "past_days": 1, "forecast_days": 2,
    })
    d = fetch_json("https://api.open-meteo.com/v1/forecast?" + q)
    h = d["hourly"]
    models = []
    for mid, name, origin in MODELS:
        speed = h.get(f"wind_speed_80m_{mid}")
        direction = h.get(f"wind_direction_80m_{mid}")
        if speed and any(v is not None for v in speed):
            models.append({"id": mid, "name": name, "origin": origin, "speed": speed, "direction": direction})
    result = {"source": "Open-Meteo", "height_m": 80, "time": h["time"], "models": models,
              "updated": datetime.now(DK).isoformat(timespec="seconds")}
    try:  # MET Norway er en ekstra kilde; fejler den, vises de fire modeller alligevel
        met = fetch_json(f"https://api.met.no/weatherapi/locationforecast/2.0/compact?lat={LAT}&lon={LON}")
        first = met["properties"]["timeseries"][0]
        det = first["data"]["instant"]["details"]
        result["met"] = {"name": "MET Norway", "origin": "Norge, yr.no", "height_m": 10,
                         "speed": det.get("wind_speed"), "direction": det.get("wind_from_direction"),
                         "time": first["time"]}
    except Exception as exc:
        print(f"MET Norway: {type(exc).__name__}", file=sys.stderr, flush=True)
    return result


def prices() -> dict:
    today = datetime.now(DK).date()
    q = urllib.parse.urlencode({
        "start": (today - timedelta(days=1)).isoformat(), "end": (today + timedelta(days=3)).isoformat(),
        "filter": json.dumps({"PriceArea": ["DK1"]}), "sort": "TimeDK asc", "limit": 1000,
    })
    d = fetch_json("https://api.energidataservice.dk/dataset/DayAheadPrices?" + q, timeout=15)
    first, last = today.isoformat(), (today + timedelta(days=2)).isoformat()
    hours: dict[str, list[float]] = {}
    for r in d.get("records", []):
        t = r.get("TimeDK", "")
        if first <= t[:10] < last and r.get("DayAheadPriceDKK") is not None:
            hours.setdefault(t[:13], []).append(r["DayAheadPriceDKK"])  # 15-minutters priser til timer
    return {"source": "Energi Data Service (Energinet)", "area": "DK1", "unit": "kr/MWh",
            "hours": [{"hour": k, "price": round(sum(v) / len(v), 2)} for k, v in sorted(hours.items())],
            "updated": datetime.now(DK).isoformat(timespec="seconds")}


def alarm_service(path: str) -> dict:
    base, key = os.environ.get("API_URL"), os.environ.get("OPERATOR_API_KEY")
    if not base or not key:
        return {"error": "API_URL eller OPERATOR_API_KEY mangler i ~/.alarm-pi.env"}
    data = fetch_json(base.rstrip("/") + path, headers={"X-API-Key": key}, timeout=8)
    return {"items": data, "updated": datetime.now(DK).isoformat(timespec="seconds")}


def sensor_rules() -> dict:
    base = os.environ.get("API_URL")
    return fetch_json(base.rstrip("/") + "/sensor-types", timeout=8) if base else {}


ROUTES = {
    "/api/weather": lambda: cached("weather", 600, weather),
    "/api/prices": lambda: cached("prices", 900, prices),
    "/api/alarms": lambda: cached("alarms", 4, lambda: alarm_service("/alarms?limit=50")),
    "/api/events": lambda: cached("events", 4, lambda: alarm_service("/events?limit=60")),
    "/api/rules": lambda: cached("rules", 3600, sensor_rules),
    "/api/status": lambda: {"host": socket.gethostname(), "time": datetime.now(DK).isoformat(timespec="seconds"),
                            "alarm_service": bool(os.environ.get("API_URL") and os.environ.get("OPERATOR_API_KEY"))},
}


class Handler(BaseHTTPRequestHandler):
    server_version = "vindmoelle-dashboard"

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path in ROUTES:
            try:
                body, status = json.dumps(ROUTES[path](), ensure_ascii=False).encode("utf-8"), 200
            except Exception as exc:
                body, status = json.dumps({"error": type(exc).__name__}).encode(), 502
            return self._send(status, body, "application/json; charset=utf-8", "no-store")
        if path == "/":
            path = "/index.html"
        name = path.lstrip("/")
        full = os.path.realpath(os.path.join(STATIC, name))
        # Kun filer direkte i static/, så ../ ikke kan nå andre filer på Pi'en
        if os.path.dirname(full) != os.path.realpath(STATIC) or not os.path.isfile(full):
            return self._send(404, b"Ikke fundet", "text/plain; charset=utf-8", "no-store")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            self._send(200, f.read(), ctype, "no-cache")

    def _send(self, status, body, ctype, cache):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # kun fejl i loggen, ellers skriver den hvert 5. sekund
        if len(args) > 1 and str(args[1])[:1] in "45":
            sys.stderr.write(f"{self.address_string()} {fmt % args}\n")


def main():
    load_env(os.path.expanduser("~/.alarm-pi.env"))
    port = int(os.environ.get("DASHBOARD_PORT", "8080"))
    # Alle netværkskort er bevidst: skærmen skal kunne åbnes fra andre maskiner på hjemmenettet.
    # Dashboardet viser kun data, og nøglerne forlader aldrig Pi'en. DASHBOARD_HOST=127.0.0.1 lukker det.
    host = os.environ.get("DASHBOARD_HOST", "0.0.0.0")  # nosec B104
    mimetypes.add_type("text/javascript", ".js")
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"Dashboardet kører på http://{socket.gethostname()}:{port}  (Ctrl+C stopper)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStoppet.")


if __name__ == "__main__":
    main()
