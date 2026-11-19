"""Bygger Postman-collectionen og de to miljøer (lokal og produktion).

Kør:  python postman/lav_postman.py
Ret her og kør igen i stedet for at rette i JSON-filerne.

Nøglerne står IKKE i filerne. Udfyld dem i Postman under Environments:
  lokal:      værdierne fra .env
  produktion: az keyvault secret show --vault-name kv-alarm-uj0vm -n device-api-key --query value -o tsv
"""
import json
import os
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))


def tests(*lines):
    return [{"listen": "test", "script": {"type": "text/javascript", "exec": list(lines)}}]


def request(name, method, path, key=None, body=None, test_lines=(), description="", base="{{base_url}}",
            auth=None):
    headers = []
    if key:
        headers.append({"key": "X-API-Key", "value": "{{" + key + "}}", "type": "text"})
    if body is not None:
        headers.append({"key": "Content-Type", "value": "application/json", "type": "text"})
    raw_url = base + path
    req = {"method": method, "header": headers, "url": {"raw": raw_url, "host": [base], "path": [p for p in path.split("?")[0].strip("/").split("/") if p]},
           "description": description}
    if "?" in path:
        req["url"]["query"] = [{"key": k, "value": v} for k, v in (q.split("=") for q in path.split("?")[1].split("&"))]
    if body is not None:
        req["body"] = {"mode": "raw", "raw": json.dumps(body, ensure_ascii=False, indent=2),
                       "options": {"raw": {"language": "json"}}}
    if auth:
        req["auth"] = auth
    return {"name": name, "event": tests(*test_lines), "request": req}


def reading(value, sensor="gearbox_temperature"):
    return {"turbine_id": "{{turbine_id}}", "sensor_type": sensor, "value": value}


items = [
    request(
        "0. Health check", "GET", "/health",
        description="Viser, at API'et kører og kan nå MySQL. Kræver ingen nøgle.",
        test_lines=[
            'pm.test("200 OK", () => pm.response.to.have.status(200));',
            'pm.test("Databasen svarer", () => pm.expect(pm.response.json().database).to.eql("ok"));',
        ]),
    request(
        "1. Normal måling (64 °C)", "POST", "/sensor-values", key="device_key", body=reading(64.0),
        description="Command Register Sensor Value. Værdien er under grænsen, så der kommer kun SensorValueReceived.",
        test_lines=[
            'pm.test("201 Created", () => pm.response.to.have.status(201));',
            'const b = pm.response.json();',
            'pm.test("Vurderet som normal", () => pm.expect(b.outcome).to.eql("normal"));',
            'pm.test("Ingen alarm", () => pm.expect(b.alarm).to.be.null);',
        ]),
    request(
        "2. Anomali (97,5 °C) rejser alarm", "POST", "/sensor-values", key="device_key", body=reading(97.5),
        description="Over den kritiske grænse på 95 °C. Flowet: SensorValueReceived → AnomalyDetected → "
                    "AlarmRaised → NotificationSent. Alarmens id gemmes til de næste kald.",
        test_lines=[
            'pm.test("201 Created", () => pm.response.to.have.status(201));',
            'const b = pm.response.json();',
            'pm.test("Vurderet som anomali", () => pm.expect(b.outcome).to.eql("anomaly"));',
            'pm.test("Hele event-flowet", () => pm.expect(b.events).to.eql(',
            '  ["SensorValueReceived", "AnomalyDetected", "AlarmRaised", "NotificationSent"]));',
            'pm.test("Prioritet kritisk med svarfrist", () => {',
            '  pm.expect(b.alarm.priority).to.eql("kritisk");',
            '  pm.expect(b.alarm.response_due_at).to.be.a("string");',
            '});',
            'pm.collectionVariables.set("alarm_id", b.alarm.id);',
        ]),
    request(
        "3. Sensorfejl (-60 °C) giver ingen alarm", "POST", "/sensor-values", key="device_key", body=reading(-60.0),
        description="Hot spottet fra Delopgave 1 som regel: en fysisk umulig værdi er en sensorfejl, ikke en anomali.",
        test_lines=[
            'pm.test("201 Created", () => pm.response.to.have.status(201));',
            'const b = pm.response.json();',
            'pm.test("Vurderet som sensorfejl", () => pm.expect(b.outcome).to.eql("sensor_fault"));',
            'pm.test("SensorFaultSuspected og ingen alarm", () => {',
            '  pm.expect(b.events).to.include("SensorFaultSuspected");',
            '  pm.expect(b.alarm).to.be.null;',
            '});',
        ]),
    request(
        "4. Alarmoversigt (åbne alarmer)", "GET", "/alarms?status=raised", key="operator_key",
        description="Read model Alarmoversigt. Kræver operatørens nøgle.",
        test_lines=[
            'pm.test("200 OK", () => pm.response.to.have.status(200));',
            'pm.test("Alarmen fra trin 2 er åben", () => {',
            '  const ids = pm.response.json().map(a => a.id);',
            '  pm.expect(ids).to.include(pm.collectionVariables.get("alarm_id"));',
            '});',
        ]),
    request(
        "5. Se mailen i Mailpit", "GET", "/api/v1/messages", base="{{mailpit_url}}",
        auth={"type": "basic", "basic": [{"key": "username", "value": "{{mailpit_user}}", "type": "string"},
                                          {"key": "password", "value": "{{mailpit_password}}", "type": "string"}]},
        description="Notifikationen: mailen, som API'et sendte, da alarmen blev rejst. "
                    "I produktion kræver Mailpit login (bruger vagt, adgangskode fra Key Vault).",
        test_lines=[
            'pm.test("200 OK", () => pm.response.to.have.status(200));',
            'pm.test("Mail om alarmen findes", () => {',
            '  const subjects = pm.response.json().messages.map(m => m.Subject);',
            '  const id = pm.collectionVariables.get("alarm_id");',
            '  pm.expect(subjects.some(s => s.includes(`Alarm ${id}:`))).to.be.true;',
            '});',
        ]),
    request(
        "6. Kvittér alarmen", "PATCH", "/alarms/{{alarm_id}}/acknowledge", key="operator_key",
        body={"acknowledged_by": "Danny"},
        description="Command Acknowledge Alarm. Giver eventen AlarmAcknowledged.",
        test_lines=[
            'pm.test("200 OK", () => pm.response.to.have.status(200));',
            'pm.test("Status er acknowledged", () => pm.expect(pm.response.json().status).to.eql("acknowledged"));',
        ]),
    request(
        "7. Kvittér igen giver 409", "PATCH", "/alarms/{{alarm_id}}/acknowledge", key="operator_key",
        body={"acknowledged_by": "Danny"},
        description="Domæneregel: en alarm kan kun kvitteres én gang.",
        test_lines=['pm.test("409 Conflict", () => pm.response.to.have.status(409));']),
    request(
        "8. Domain events i databasen", "GET", "/events?limit=10", key="operator_key",
        description="De events, flowet har gemt i MySQL, nyeste først.",
        test_lines=[
            'pm.test("200 OK", () => pm.response.to.have.status(200));',
            'pm.test("Hele forløbet er gemt", () => {',
            '  const t = pm.response.json().map(e => e.event_type);',
            '  ["AlarmAcknowledged", "AlarmRaised", "AnomalyDetected", "SensorFaultSuspected"]',
            '    .forEach(e => pm.expect(t).to.include(e));',
            '});',
        ]),
    {"name": "Sikkerhed", "item": [
        request(
            "9. Uden nøgle giver 401", "POST", "/sensor-values", body=reading(64.0),
            description="Uden X-API-Key kan ingen sende falske målinger.",
            test_lines=['pm.test("401 Unauthorized", () => pm.response.to.have.status(401));']),
        request(
            "10. Enhedens nøgle må ikke læse alarmer", "GET", "/alarms", key="device_key",
            description="En mølle må sende målinger, men ikke læse eller kvittere alarmer.",
            test_lines=['pm.test("401 Unauthorized", () => pm.response.to.have.status(401));']),
        request(
            "11. SQL injection giver 422", "POST", "/sensor-values", key="device_key",
            body={"turbine_id": "x' OR '1'='1", "sensor_type": "vibration", "value": 3},
            description="Angreb 1 fra Hands-on 1. Inputvalidering afviser værdien, og SQL'en bruger pladsholdere.",
            test_lines=['pm.test("422 afvist", () => pm.response.to.have.status(422));']),
        request(
            "12. Ugyldige data giver 422", "POST", "/sensor-values", key="device_key",
            body={"turbine_id": "<script>alert(1)</script>", "sensor_type": "vibration", "value": -9999},
            description="Angreb 2 fra Hands-on 1.",
            test_lines=['pm.test("422 afvist", () => pm.response.to.have.status(422));']),
    ]},
]

collection = {
    "info": {
        "_postman_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "alarm-service-collection")),
        "name": "Alarm-service (Delopgave 2)",
        "description": "Hele forløbet til videoen: sensorværdi sendes, anomali registreres, alarm gemmes, "
                       "mail sendes, alarmen kvitteres, og events kan ses i databasen. "
                       "Vælg miljøet 'lokal' eller 'produktion' øverst til højre, og kør mappen med Run.",
        "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
    },
    "item": items,
    "variable": [{"key": "alarm_id", "value": ""}],
}


def environment(name, base_url, mailpit_url, mailpit_user):
    return {
        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "alarm-service-env-" + name)),
        "name": name,
        "values": [
            {"key": "base_url", "value": base_url, "type": "default", "enabled": True},
            {"key": "mailpit_url", "value": mailpit_url, "type": "default", "enabled": True},
            {"key": "turbine_id", "value": "WT-017", "type": "default", "enabled": True},
            {"key": "device_key", "value": "", "type": "secret", "enabled": True},
            {"key": "operator_key", "value": "", "type": "secret", "enabled": True},
            {"key": "mailpit_user", "value": mailpit_user, "type": "default", "enabled": True},
            {"key": "mailpit_password", "value": "", "type": "secret", "enabled": True},
        ],
        "_postman_variable_scope": "environment",
    }


AZURE = "http://alarm-service-uj0vm.denmarkeast.cloudapp.azure.com"
files = {
    "alarm-service.postman_collection.json": collection,
    "lokal.postman_environment.json": environment("lokal", "http://localhost:5000", "http://localhost:8025", ""),
    "produktion.postman_environment.json": environment("produktion", AZURE + ":5000", AZURE + ":8025", "vagt"),
}
for fname, data in files.items():
    with open(os.path.join(HERE, fname), "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("skrev", fname)
