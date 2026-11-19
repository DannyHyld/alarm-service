# Raspberry Pi med Sense HAT som IoT-enhed

Pi'en spiller vindmøllen `WT-PI` og sender rigtige målinger til alarm-servicen på Azure. Opgaven kalder det "en anden computer, der spiller IoT-enhed".

| Sense HAT | Sendes som | Hvordan man laver en alarm |
|---|---|---|
| Temperatur | `gearbox_temperature` | Joysticket trykket ind sender en **simuleret** kritisk værdi (97,5 °C) |
| Accelerometer | `vibration` | Ryst Pi'en. Let rystet giver høj, kraftigt rystet giver kritisk |

Vibrationen er en forenkling til demoen. RMS af accelerationen uden tyngdekraft (m/s²) sendes som vibrationsværdi. Bliver Pi'en slået hårdt, så værdien kommer over 50, vurderer API'et det som en sensorfejl og rejser ingen alarm.

LED-matricen viser API'ets svar:

| Farve | Betyder |
|---|---|
| Grøn | Normal |
| Orange | Alarm, prioritet høj |
| Rød | Alarm, prioritet kritisk |
| Blå | Sensorfejl, ingen alarm |
| Hvidt kryds | API'et svarer ikke |

## Opsætning

1. Sæt Pi'en op efter vejledningen fra undervisningen (Imager, wifi, SSH) og montér Sense HAT.
2. Opret nøglefilen på Pi'en. Den må aldrig komme i git:
   ```bash
   nano ~/.alarm-pi.env
   ```
   ```
   API_URL=http://alarm-service-uj0vm.denmarkeast.cloudapp.azure.com:5000
   DEVICE_API_KEY=<fra Key Vault: device-api-key>
   TURBINE_ID=WT-PI
   ```
3. Kopiér `pi_sensor.py` og `install.sh` til Pi'en, og kør:
   ```bash
   bash install.sh
   ```
   Programmet starter nu, og det starter igen af sig selv, hver gang Pi'en tændes.
4. Se, hvad Pi'en sender:
   ```bash
   journalctl -u alarm-pi -f
   ```

Programmet kan testes uden Pi: `python pi/pi_sensor.py --fake --key <nøgle>`.

Pi'en har kun enhedsnøglen. Den kan sende målinger, men ikke læse eller kvittere alarmer.
