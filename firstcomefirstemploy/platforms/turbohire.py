"""TurboHire careers sites (https://<tenant>.turbohire.co).

The careers page is a client-side app. It makes three requests to
thapi.azurewebsites.net, and so does this plugin:

  1. GET  /api/token/noauth                    anonymous token (the endpoint
                                               checks Origin/Referer)
  2. GET  /api/publicorganizations?accountName=<tenant>   -> org id
  3. POST /api/careerpagev2/filteredjobs?orgId=<id>&pageType=0   body {}
                                               -> every public posting, no
                                               pagination, description inline

Only pageType=0 (the public careers page) is ever requested.
Set "org_id" in companies.json to skip request 2.
"""

import json
import re
from urllib.parse import quote, unquote

from ..config import REQUEST_TIMEOUT
from ..utils.http import retry
from ..utils.text import clean_description
from .base import Platform
from .registry import register

import requests

API = "https://thapi.azurewebsites.net"
UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I
)


def _get(row, key, default=""):
    """Field lookup that tolerates different key casing."""
    if key in row:
        return row[key]

    lowered = key.lower()

    for name, value in row.items():
        if str(name).lower() == lowered:
            return value

    return default


def _rows(data):
    """The list of posting dicts, or None if the shape is unrecognised."""
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]

    if isinstance(data, dict):
        for value in data.values():
            if isinstance(value, list) and (
                not value or isinstance(value[0], dict)
            ):
                return [r for r in value if isinstance(r, dict)]

        for value in data.values():
            if isinstance(value, dict):
                found = _rows(value)

                if found is not None:
                    return found

    return None


def _location(raw):
    """'[{"Address": "Bengaluru, India"}]' -> 'Bengaluru, India'."""
    if not raw:
        return ""

    items = raw

    if isinstance(raw, str):
        try:
            items = json.loads(raw)
        except ValueError:
            return raw.strip()

    if isinstance(items, dict):
        items = [items]

    places = []

    for item in items if isinstance(items, list) else []:
        text = _get(item, "Address") if isinstance(item, dict) else item

        if isinstance(text, str) and text.strip():
            places.append(text.strip())

    return "; ".join(dict.fromkeys(places))


def _job_url(tenant, obfuscated):
    # The id arrives percent-encoded; decode once and encode like the site.
    encoded = quote(unquote(str(obfuscated)), safe="!~*'()")
    return f"https://{tenant}.turbohire.co/job/publicjobs/{encoded}"


@register
class TurboHire(Platform):
    name = "turbohire"
    bootstrap_url = "https://{slug}.turbohire.co"
    bootstrap_order = 60

    def detect(self, host, path):
        return host.endswith(".turbohire.co")

    def _origin(self, tenant):
        return f"https://{tenant}.turbohire.co"

    def _headers(self, tenant, token=""):
        origin = self._origin(tenant)
        headers = {"Origin": origin, "Referer": origin + "/"}

        if token:
            headers["Authorization"] = f"Bearer {token}"

        return headers

    def _call(self, session, method, url, headers, **kwargs):
        def attempt():
            response = session.request(
                method, url, headers=headers, timeout=REQUEST_TIMEOUT, **kwargs
            )

            if 400 <= response.status_code < 500 and response.status_code != 429:
                raise RuntimeError(
                    f"Request failed: HTTP {response.status_code} {url} :: "
                    f"{response.text[:300]}"
                )

            response.raise_for_status()
            return response

        return retry(attempt, (requests.RequestException,))

    def _token(self, session, tenant):
        response = self._call(
            session, "GET", f"{API}/api/token/noauth", self._headers(tenant)
        )

        try:
            data = response.json()
        except ValueError:
            data = response.text

        if isinstance(data, dict):
            for key in ("token", "access_token", "accessToken", "Token", "data"):
                if isinstance(data.get(key), str):
                    return data[key]

            return next((v for v in data.values() if isinstance(v, str)), "")

        return str(data).strip().strip('"')

    def _org_id(self, session, tenant, token):
        response = self._call(
            session,
            "GET",
            f"{API}/api/publicorganizations",
            self._headers(tenant, token),
            params={"accountName": tenant},
        )
        match = UUID.search(response.text)

        if not match:
            raise RuntimeError(
                f"TurboHire: no organization id for '{tenant}'. "
                "Set org_id in companies.json."
            )

        return match.group(0)

    def discover(self, session, company):
        from urllib.parse import urlparse

        tenant = company.get("tenant") or urlparse(
            company["career_url"]
        ).netloc.split(".")[0]
        org_id = company.get("org_id") or self._org_id(
            session, tenant, self._token(session, tenant)
        )

        return self.validated(session, company, {
            "tenant": tenant,
            "org_id": org_id,
            "api_url": f"{API}/api/careerpagev2/filteredjobs",
        })

    def _post_jobs(self, session, metadata, token):
        return self._call(
            session,
            "POST",
            metadata["api_url"],
            self._headers(metadata["tenant"], token),
            params={"orgId": metadata["org_id"], "pageType": 0},
            json={},
        )

    def fetch_raw(self, session, company, metadata, search):
        tenant = metadata["tenant"]
        token = self._token(session, tenant)

        try:
            response = self._post_jobs(session, metadata, token)
        except RuntimeError as exc:
            # The jobs endpoint may not want the token: retry without it.
            if token and ("HTTP 401" in str(exc) or "HTTP 403" in str(exc)):
                response = self._post_jobs(session, metadata, "")
            else:
                raise

        rows = _rows(response.json())

        if rows is None:
            raise RuntimeError(
                "TurboHire: unrecognised response shape from filteredjobs."
            )

        jobs = {}

        for row in rows:
            title = str(_get(row, "JobTitle")).strip()
            obfuscated = _get(row, "JobIdObfuscated")
            jid = str(_get(row, "JobCode") or unquote(str(obfuscated))).strip()

            if not (title and obfuscated and jid):
                continue

            jobs[jid] = {
                "title": title,
                "location": _location(_get(row, "Location")),
                "url": _job_url(tenant, obfuscated),
                "posted": str(_get(row, "PublishedDate") or "")[:10],
                "_description": clean_description(_get(row, "JobDescV2")),
            }

        return jobs, False