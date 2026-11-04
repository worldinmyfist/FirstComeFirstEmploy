"""Base class for notification channels."""


class Notifier:
    """Subclass, set `name`, implement `send`, add to NOTIFIERS in
    notifiers/__init__.py. `all_changes` is
    {monitor_name: {"added": {...}, "removed": {...}, "modified": {...}}}."""

    name = ""

    def send(self, all_changes):
        raise NotImplementedError
