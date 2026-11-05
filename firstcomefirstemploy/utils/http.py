"""HTTP session and retrying request helpers."""

import json
import time

import requests
from requests.adapters import HTTPAdapter

from ..config import HTTP_POOL_SIZE, REQUEST_RETRIES, REQUEST_TIMEOUT

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Accept-Language": "en-US",
}

HTML_HEADERS = {
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": None,
}


def create_session():
    session = requests.Session()
    session.headers.update(HEADERS)

    # Worker threads share one session; size the pool so they reuse
    # keep-alive connections instead of opening (and discarding) new ones.
    adapter = HTTPAdapter(pool_connections=HTTP_POOL_SIZE, pool_maxsize=HTTP_POOL_SIZE)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    return session


def retry(action, exceptions=(requests.RequestException,)):
    """Run `action` up to REQUEST_RETRIES times with linear backoff.

    Exceptions not listed in `exceptions` (e.g. RuntimeError for HTTP 4xx)
    are raised immediately without retrying.
    """
    last_error = None

    for attempt in range(1, REQUEST_RETRIES + 1):
        try:
            return action()
        except exceptions as exc:
            last_error = exc
            if attempt < REQUEST_RETRIES:
                time.sleep(attempt * 2)

    raise RuntimeError(f"Request failed: {last_error}")


def request_json(session, url, params=None, payload=None):
    def attempt():
        if payload is not None:
            response = session.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        else:
            response = session.get(url, params=params, timeout=REQUEST_TIMEOUT)

        status = response.status_code

        if status == 404:
            raise RuntimeError(f"Request failed: HTTP 404 {url}")

        if status == 400:
            raise RuntimeError(
                f"HTTP 400 for {url}\n"
                f"  Request body: {json.dumps(payload or params)}\n"
                f"  Response: {response.text[:400]}"
            )

        if 400 <= status < 500 and status != 429:
            raise RuntimeError(
                f"Request failed: HTTP {status} {url} :: {response.text[:300]}"
            )

        response.raise_for_status()
        return response.json()

    return retry(attempt, (requests.RequestException, ValueError))


def request_text(session, url, params=None):
    def attempt():
        response = session.get(
            url, params=params, headers=HTML_HEADERS, timeout=REQUEST_TIMEOUT
        )

        if response.status_code == 404:
            raise RuntimeError(f"Request failed: HTTP 404 {url}")

        response.raise_for_status()
        return response.text

    return retry(attempt)