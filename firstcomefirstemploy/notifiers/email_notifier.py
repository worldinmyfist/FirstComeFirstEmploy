"""HTML email over SMTP (SSL)."""

import os
import smtplib
from email.mime.text import MIMEText
from html import escape

from ..state import count_changes
from .base import Notifier


def _job_lines(jid, job, posted=True, link=True):
    lines = [
        f"<b>{escape(job['title'])}</b>",
        f"Location: {escape(job.get('location', ''))}",
        f"ID: {escape(str(jid))}",
    ]

    if posted:
        lines.append(f"Posted: {escape(job.get('posted', ''))}")

    if link:
        lines.append(f'<a href="{escape(job["url"])}">View Job</a>')

    return "<p>" + "<br>".join(lines) + "</p>"


def build_email(all_changes):
    parts = ["<html><body><h2>FIRSTCOMEFIRSTEMPLOY UPDATE</h2><hr>"]

    for name, changes in all_changes.items():
        parts.append(f"<h2>MONITOR: {escape(name)}</h2>")

        if changes["added"]:
            parts.append(f"<h3>NEW JOBS ({len(changes['added'])})</h3>")
            parts.extend(_job_lines(j, job) for j, job in changes["added"].items())

        if changes["removed"]:
            parts.append(f"<h3>REMOVED JOBS ({len(changes['removed'])})</h3>")
            parts.extend(
                _job_lines(j, job, posted=False, link=False)
                for j, job in changes["removed"].items()
            )

        if changes["modified"]:
            parts.append(f"<h3>MODIFIED JOBS ({len(changes['modified'])})</h3>")

            for jid, details in changes["modified"].items():
                old, new = details["old"], details["new"]
                parts.append(
                    f"<p><b>{escape(new['title'])}</b><br>"
                    f"ID: {escape(str(jid))}<br>"
                    f"Old: {escape(old['title'])} | {escape(old.get('location', ''))}<br>"
                    f"New: {escape(new['title'])} | {escape(new.get('location', ''))}<br>"
                    f'<a href="{escape(new["url"])}">View Job</a></p>'
                )

    parts.append("</body></html>")
    return "".join(parts)


class EmailNotifier(Notifier):
    name = "email"

    def __init__(self):
        self.sender = os.getenv("EMAIL_FROM", "")
        self.recipient = os.getenv("EMAIL_TO", "")
        self.password = os.getenv("EMAIL_PASSWORD", "")
        self.server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        self.port = int(os.getenv("SMTP_PORT", "465"))

    def send(self, all_changes):
        if not all([self.sender, self.recipient, self.password]):
            raise RuntimeError(
                "Configure EMAIL_FROM, EMAIL_TO and EMAIL_PASSWORD in .env"
            )

        total = sum(count_changes(c) for c in all_changes.values())

        message = MIMEText(build_email(all_changes), "html")
        message["Subject"] = (
            f"[FirstComeFirstEmploy] {total} changes detected "
            f"across {len(all_changes)} monitors"
        )
        message["From"] = self.sender
        message["To"] = self.recipient

        with smtplib.SMTP_SSL(self.server, self.port, timeout=30) as smtp:
            smtp.login(self.sender, self.password)
            smtp.send_message(message)

        print("\nEmail notification sent successfully.")
