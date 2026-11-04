"""Platform registry and career-URL detection."""

from urllib.parse import urlparse

_REGISTRY = {}


def register(cls):
    """Class decorator: make a Platform available by `cls.name`."""
    instance = cls()

    if not instance.name:
        raise ValueError(f"{cls.__name__} has no name")

    _REGISTRY[instance.name] = instance
    return cls


def get_platform(name):
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ValueError(f"Unsupported platform: {name}") from None


def detect_platform(company):
    configured = company.get("platform", "auto").lower()

    if configured != "auto":
        get_platform(configured)  # validates
        return configured

    parsed = urlparse(company["career_url"])
    host = parsed.netloc.lower()
    path = parsed.path.lower()

    for platform in _REGISTRY.values():
        if platform.detect(host, path):
            return platform.name

    raise ValueError(f"Unsupported career URL: {company['career_url']}")
