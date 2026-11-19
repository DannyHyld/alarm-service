# Alarm-service

MVP til Intelligent IoT Solutions A/S (6. semester, Delopgave 2). Servicen hører til bounded contexten Condition Monitoring. Den modtager sensorværdier fra vindmøller, tjekker dem mod faste tærskler og rejser en alarm, når en værdi er for høj. Når alarmen er rejst, sender servicen en e-mail til vagten.

## Event-flow

```
SensorValueReceived -> threshold-check -> AnomalyDetected -> AlarmRaised -> NotificationSent
                                       \-> SensorFaultSuspected (fysisk umulig værdi, ingen alarm)
```

Hver event gemmes i tabellen `events`. Alarmer gemmes i tabellen `alarms` med prioritet og svarfrist.

| Sensortype | Enhed | Høj fra | Kritisk fra | Sensorfejl uden for |
|---|---|---|---|---|
| `gearbox_temperature` | °C | 80 | 95 | -40 til 150 |
| `generator_temperature` | °C | 110 | 130 | -40 til 180 |
| `vibration` | mm/s | 7,1 | 11 | 0 til 50 |

Svarfristen er 4 timer for kritiske alarmer og 24 timer for høje. Tallene er antagelser for MVP'en.

## Kør lokalt

Kræver Docker Desktop.

```bash
cp .env.example .env      # udfyld nøgler og adgangskoder
docker compose up --build
```

| Hvad | Adresse |
|---|---|
| Swagger-dokumentation | http://localhost:5000/docs |
| Health check | http://localhost:5000/health |
| Mails (Mailpit) | http://localhost:8025 |
| MySQL (Workbench) | localhost:3307 |

Send en måling fra en anden computer:

```bash
python simulator.py --url http://<ip-adresse>:5000 --key <DEVICE_API_KEY> --scenario anomaly
```

## Produktion på Azure

Produktion kører på en Ubuntu-VM i Azure med `docker-compose.remote.yml`. `infra/azure-setup.ps1` opretter VM'en, Key Vault og GitHub Secrets én gang.

Hvert push til `main` kører pipelinen. Når testene består, bygger den imaget, logger ind på VM'en over SSH og henter secrets fra Key Vault med VM'ens managed identity (`scripts/fetch-secrets.sh`). Derefter starter den den nye version og tjekker `/health`.

| Miljø | Hvor | Secrets |
|---|---|---|
| Udvikling | Egen computer, `docker-compose.yml` | `.env` (committes aldrig) |
| Produktion | Azure VM, `docker-compose.remote.yml` | Azure Key Vault → `.env` på VM'en |

GitHub Secrets indeholder kun det, pipelinen skal bruge for at nå VM'en: SSH-nøgle, host, host key og navnet på vaulten. Udvikling og produktion har hver deres nøgler.

## Endpoints

| Metode | Sti | Nøgle | Hvad |
|---|---|---|---|
| POST | `/sensor-values` | enhed | Command Register Sensor Value |
| GET | `/alarms` | operatør | Alarmoversigt, filtrér med `status` og `turbine_id` |
| GET | `/alarms/{id}` | operatør | Én alarm |
| PATCH | `/alarms/{id}/acknowledge` | operatør | Command Acknowledge Alarm |
| GET | `/events` | operatør | Domain events fra databasen |
| GET | `/sensor-types` | ingen | Tærskler |
| GET | `/health`, `/metrics` | ingen | Drift og Prometheus-metrics |

Nøglen sendes i headeren `X-API-Key`. Enheder og operatører har hver sin nøgle, så en kompromitteret mølle ikke kan læse eller kvittere alarmer.

## Sikkerhed

- Input valideres. Et forkert `turbine_id`, en ukendt sensortype eller en værdi uden for -1000 til 1000 giver 422.
- Al SQL bruger pladsholdere, så SQL injection ikke virker.
- API-nøglerne sammenlignes med `secrets.compare_digest`.
- Rate limit på 120 kald i minuttet pr. IP giver 429.
- Uventede fejl giver 500 med et request-id. Detaljerne står kun i loggen.
- Secrets ligger i `.env` lokalt og i GitHub Secrets eller Azure Key Vault i drift. `.env` er i `.gitignore`.
- Containeren kører som en almindelig bruger, ikke root.

## Tests og pipeline

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
pytest
```

GitHub Actions (`.github/workflows/ci.yml`) kører ved hvert push og pull request: secret scanning med detect-secrets, SAST med Bandit, unit tests med pytest og build af Docker-imaget. På `main` bliver imaget skubbet til GitHub Container Registry.

Bandit virker ikke med Python 3.14 endnu. Pipelinen kører Python 3.12.

## Struktur

```
app/domain.py       reglerne: validering, threshold-check, svarfrist
app/service.py      flowet: events, alarm og notifikation
app/repository.py   MySQL (drift) og hukommelse (tests)
app/notifier.py     e-mail via SMTP
app/__init__.py     Flask: endpoints, nøgler, logging, fejl
db/init.sql         tabellerne events og alarms
simulator.py        spiller IoT-enhed
```
