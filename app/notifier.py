"""Notifikation pr. e-mail, når en alarm rejses.

Lokalt peger SMTP_HOST på Mailpit i Docker Compose, som fanger mailen og viser
den på http://localhost:8025. I drift peger den på en rigtig SMTP-server, og
brugernavn og adgangskode kommer fra miljøvariabler (Key Vault / GitHub Secrets).
"""
from __future__ import annotations

import smtplib
from email.message import EmailMessage


class EmailNotifier:
    def __init__(self, host: str, port: int, sender: str, recipients: list[str],
                 username: str | None = None, password: str | None = None,
                 use_tls: bool = False, timeout: float = 5.0):
        self.host, self.port, self.sender, self.recipients = host, port, sender, recipients
        self.username, self.password, self.use_tls, self.timeout = username, password, use_tls, timeout

    def build_message(self, alarm: dict) -> EmailMessage:
        msg = EmailMessage()
        msg["Subject"] = (f"[{alarm['priority'].upper()}] Alarm {alarm['id']}: "
                          f"{alarm['sensor_type']} på {alarm['turbine_id']}")
        msg["From"] = self.sender
        msg["To"] = ", ".join(self.recipients)
        msg.set_content(
            f"Alarm {alarm['id']} er rejst.\n\n"
            f"Mølle:       {alarm['turbine_id']}\n"
            f"Sensor:      {alarm['sensor_type']}\n"
            f"Værdi:       {alarm['value']}\n"
            f"Prioritet:   {alarm['priority']}\n"
            f"Årsag:       {alarm['reason']}\n"
            f"Svarfrist:   {alarm['response_due_at']:%Y-%m-%d %H:%M} UTC\n\n"
            f"Kvittér alarmen med PATCH /alarms/{alarm['id']}/acknowledge.\n"
        )
        return msg

    def send_alarm(self, alarm: dict) -> None:
        msg = self.build_message(alarm)
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
            if self.use_tls:
                smtp.starttls()
            if self.username and self.password:
                smtp.login(self.username, self.password)
            smtp.send_message(msg)


class NullNotifier:
    """Bruges, når SMTP_HOST ikke er sat. Sender intet."""

    def send_alarm(self, alarm: dict) -> None:
        raise RuntimeError("SMTP er ikke konfigureret")


class RecordingNotifier:
    """Til tests: husker alarmerne i stedet for at sende mail."""

    def __init__(self, fail: bool = False):
        self.sent: list[dict] = []
        self.fail = fail

    def send_alarm(self, alarm: dict) -> None:
        if self.fail:
            raise ConnectionRefusedError("SMTP nede")
        self.sent.append(alarm)
