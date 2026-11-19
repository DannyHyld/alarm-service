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

## Overvågning med Grafana

Prometheus henter `/metrics` fra API'et hvert 15. sekund, og Grafana viser dashboardet "Alarm-service: Condition Monitoring". Datakilde og dashboard oprettes automatisk ud fra filerne i `monitoring/`. Ret i JSON-filen i repoet, ikke i Grafana.

| Panel | Metric |
|---|---|
| API oppe/nede | `up{job="alarm-service"}` |
| Åbne alarmer, kritisk og høj | `alarms_open{priority}`, talt i MySQL ved hver scrape |
| Alarmer rejst og fejlede mails seneste time | `alarms_raised_total`, `notifications_total{result="failed"}` |
| Gearkassetemperatur og vibration pr. mølle med grænser | `sensor_value{turbine_id,sensor_type}` |
| Målinger pr. minut efter vurdering | `sensor_values_total{outcome}` |
| Svartid p95 og HTTP-status | `http_request_seconds` |

| Miljø | Adresse | Login |
|---|---|---|
| Lokalt | http://localhost:3000 | `admin`, adgangskode i `.env` |
| Produktion | `http://<vm>:3000` | `admin`, adgangskode i Key Vault (`grafana-admin-password`) |

Prometheus er kun åben på VM'en selv. API'et kører med én proces og fire tråde, så tællerne i `/metrics` er ét samlet tal.

## Postman

Mappen `postman/` har en collection med hele forløbet og to miljøer, `lokal` og `produktion`.

1. Importér de tre JSON-filer i Postman (Import → vælg filerne).
2. Vælg miljøet øverst til højre, og udfyld `device_key`, `operator_key` og (i produktion) `mailpit_password`. Lokalt står værdierne i `.env`. I produktion henter du dem fra Key Vault:
   ```bash
   az keyvault secret show --vault-name kv-alarm-uj0vm -n device-api-key --query value -o tsv
   ```
3. Kør request 0-8 i rækkefølge, eller kør hele collectionen med **Run**.

| # | Request | Viser |
|---|---|---|
| 0 | Health check | API og database kører |
| 1 | Normal måling | Ingen alarm |
| 2 | Anomali | AnomalyDetected, AlarmRaised og NotificationSent |
| 3 | Sensorfejl | SensorFaultSuspected, ingen alarm |
| 4 | Alarmoversigt | Alarmen er åben |
| 5 | Mailpit | Mailen om alarmen |
| 6-7 | Kvittér to gange | 200, derefter 409 |
| 8 | Events | Hele forløbet er gemt i MySQL |
| 9-12 | Sikkerhed | 401 uden nøgle, 422 ved angreb |

Nøglerne står ikke i filerne. `postman/lav_postman.py` bygger filerne, og `node postman/run_collection.mjs lokal` kører collectionen uden Postman.

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
