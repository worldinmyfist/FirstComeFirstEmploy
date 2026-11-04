"""Job-portal integrations.

Every module in this folder (except base/registry and names starting with
"_") is imported automatically, so adding a portal = adding one file.
"""

import importlib
import pkgutil

from .registry import detect_platform, get_platform, register  # noqa: F401

for _module in pkgutil.iter_modules(__path__):
    if _module.name not in ("base", "registry") and not _module.name.startswith("_"):
        importlib.import_module(f"{__name__}.{_module.name}")
