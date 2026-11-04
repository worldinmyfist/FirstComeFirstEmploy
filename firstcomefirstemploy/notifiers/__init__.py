"""Notification channels. Enable with NOTIFIERS=email,... in .env."""

import os

from .email_notifier import EmailNotifier

NOTIFIERS = {
    EmailNotifier.name: EmailNotifier,
}


def get_notifiers():
    names = [
        n.strip().lower()
        for n in os.getenv("NOTIFIERS", "email").split(",")
        if n.strip()
    ]

    unknown = [n for n in names if n not in NOTIFIERS]

    if unknown:
        raise ValueError(
            f"Unknown notifier(s): {', '.join(unknown)}. "
            f"Available: {', '.join(NOTIFIERS)}"
        )

    return [NOTIFIERS[n]() for n in names]


def notify_all(all_changes):
    for notifier in get_notifiers():
        notifier.send(all_changes)
