# Vindmølle-dashboard for Horns Rev 1

Seks skærmbilleder, der skifter hvert 20. sekund. Det kører på Raspberry Pi'en ved siden af `pi/pi_sensor.py` og kan vises på en skærm ved Pi'en eller i en browser på samme netværk.

| Skærm | Viser | Data |
|---|---|---|
| 1 Vejret | Vind i 80 m højde fra fire vejrmodeller og MET Norway, og hvor uenige de er | Open-Meteo, MET Norway (rigtige) |
| 2 Parken i realtid | 80 møller, der drejer med vinden. Et simuleret fejlforløb: fejl, alarm, stop, tekniker, i drift | Simuleret forløb, rigtige alarmer markeret med ring |
| 3 Beregneren | Skal møllerne køre (effektkurve for Vestas V80)? Skal møllen drejes mod vinden (tab = 1 - cos³ vinkel)? | Udregnet fra vejrdata |
| 4 Strømpris og regnskab | Day-ahead-pris time for time i DK1, produktion, indtjening, hvad et stop koster og CO₂ | Energi Data Service (rigtige), produktion anslået |
| 5 Alarmcentralen | Åbne alarmer med svarfrist og live events fra alarm-servicen, også Pi'ens målinger | alarm-servicen i Azure (rigtige) |
| 6 Serviceprognose | Hvilke møller rammer advarselsgrænsen for gearkassen først, med lineær trend | Simuleret, grænsen kommer fra `/sensor-types` |

## Sådan virker det

`server.py` bruger kun Pythons standardbibliotek. Browseren taler kun med den. Serveren henter vejr hvert 10. minut, strømpris hvert 15. minut og alarmer og events hvert 4. sekund, og den gemmer svarene imens. Fejler en kilde, viser den det sidste svar. Operatørnøglen til alarm-servicen bliver på Pi'en og bliver aldrig sendt til browseren.

Pi'en har nu både enhedsnøglen og operatørnøglen. Operatørnøglen må også kvittere for alarmer, selv om dashboardet kun læser. En egen læsenøgle ville være bedre (se rapportens afsnit F).

## Installér på Pi'en

`~/.alarm-pi.env` skal have `API_URL` og `OPERATOR_API_KEY` (fra Key Vault-secret `operator-api-key`). Kopiér mappen til Pi'en og kør:

```bash
cd dashboard
bash install.sh
```

Åbn `http://<pi>:8080`. Taster: pil venstre og højre skifter skærm, 1-6 vælger, mellemrum pauser. `?screen=3&rotate=0` i adressen viser én skærm uden at skifte.

## Kør på din egen computer

```powershell
powershell -ExecutionPolicy Bypass -File dashboard\run_local.ps1
```

Scriptet henter operatørnøglen fra Key Vault (kræver `az login`) og starter på `http://localhost:8090`.
