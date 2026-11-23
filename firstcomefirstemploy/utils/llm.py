"""LLM fallback for --bootstrap: ask which careers platform a company uses.

Uses Anthropic Messages, OpenAI Responses, or Gemini generateContent API over plain `requests`. The answer is only a *hint*: bootstrap re-checks every
URL against a real platform integration before trusting it.
"""

import json
import re

import requests

from ..config import (
    ANTHROPIC_API_KEY,
    GEMINI_API_KEY,
    GEMINI_LLM_MODEL,
    OPENAI_API_KEY,
    BOOTSTRAP_LLM_PROVIDER,
    ANTHROPIC_LLM_MODEL,
    OPENAI_LLM_MODEL,
    BOOTSTRAP_LLM_MODEL,
    BOOTSTRAP_LLM_TIMEOUT,
    BOOTSTRAP_LLM_WEB_SEARCH,
)

API_URL = "https://api.anthropic.com/v1/messages"
OPENAI_API_URL = "https://api.openai.com/v1/responses"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models"

# What the model should return for platforms we can scrape.
PLATFORM_URL_HINTS = {
    "greenhouse": "https://boards.greenhouse.io/<slug>",
    "lever": "https://jobs.lever.co/<slug> (EU: https://jobs.eu.lever.co/<slug>)",
    "ashby": "https://jobs.ashbyhq.com/<slug>",
    "smartrecruiters": "https://jobs.smartrecruiters.com/<slug>",
    "rippling": "https://ats.rippling.com/<slug>/jobs",
    "turbohire": "https://<tenant>.turbohire.co",
    "workday": "https://<tenant>.wd<N>.myworkdayjobs.com/<site>",
    "oracle": "https://<host>.oraclecloud.com/hcmUI/CandidateExperience/<lang>/sites/<site>",
    "eightfold": "https://<company>.eightfold.ai/careers",
}

_PROMPT = """Find which applicant-tracking / careers platform the public careers site of this company uses.

Company: {name}
Location hint: {location}

Platforms I can scrape, with the URL shape to return:
{hints}

Reply with ONLY one JSON object, no prose, no markdown:
{{"platform": "<one name from the list, or the real platform name if it is something else, or \\"unknown\\">",
  "career_url": "<the exact public job-board URL in the shape above, or null>",
  "slug": "<board slug/tenant if the platform uses one, else null>",
  "confidence": "high|medium|low",
  "notes": "<one short sentence>"}}

Rules: never invent a URL. If you are not sure the board exists, return null for career_url and say so in notes. Prefer the company's own current job board over third-party aggregators (LinkedIn, Naukri, Indeed)."""


def selected_provider():
    """Resolve the configured provider; return None if its API key is missing."""
    provider = BOOTSTRAP_LLM_PROVIDER
    if provider == "auto":
        provider = ("anthropic" if ANTHROPIC_API_KEY else
                    "openai" if OPENAI_API_KEY else "gemini")
    if provider == "anthropic" and ANTHROPIC_API_KEY:
        return provider
    if provider == "openai" and OPENAI_API_KEY:
        return provider
    if provider == "gemini" and GEMINI_API_KEY:
        return provider
    return None


def llm_available():
    return selected_provider() is not None


def _post(body):
    """Anthropic Messages request (kept as a separate function for existing tests)."""
    response = requests.post(
        API_URL,
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json=body,
        timeout=BOOTSTRAP_LLM_TIMEOUT,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")
    return response.json()


def _post_openai(body):
    """OpenAI Responses request; no openai Python dependency required."""
    response = requests.post(
        OPENAI_API_URL,
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=BOOTSTRAP_LLM_TIMEOUT,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")
    return response.json()



def _post_gemini(body, model):
    """Gemini Developer API request via requests (no extra dependency)."""
    response = requests.post(
        f"{GEMINI_API_URL}/{model}:generateContent",
        headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"},
        json=body, timeout=BOOTSTRAP_LLM_TIMEOUT,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")
    return response.json()


def _gemini_text(data):
    """Collect textual parts from Gemini's candidate response."""
    return "".join(
        part.get("text", "")
        for candidate in data.get("candidates", [])
        for part in candidate.get("content", {}).get("parts", [])
        if isinstance(part, dict)
    )

def _extract_json(text):
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _openai_text(data):
    """Extract model text from Responses API output blocks."""
    return "".join(
        item.get("text", "")
        for output in data.get("output", [])
        for item in output.get("content", [])
        if item.get("type") == "output_text"
    )


def ask_board(name, location=""):
    """Return the model's answer dict, or {"error": "..."} on failure."""
    provider = selected_provider()
    if not provider:
        return {"error": "LLM provider not configured (set GEMINI_API_KEY, OPENAI_API_KEY, or ANTHROPIC_API_KEY)"}

    hints = "\n".join(f"- {k}: {v}" for k, v in PLATFORM_URL_HINTS.items())
    prompt = _PROMPT.format(name=name, location=location or "unknown", hints=hints)

    if provider == "gemini":
        model = BOOTSTRAP_LLM_MODEL or GEMINI_LLM_MODEL
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": 1000},
        }
        if BOOTSTRAP_LLM_WEB_SEARCH:
            body["tools"] = [{"google_search": {}}]
        post = lambda payload: _post_gemini(payload, model)
    elif provider == "openai":
        body = {
            "model": BOOTSTRAP_LLM_MODEL or OPENAI_LLM_MODEL,
            "input": prompt,
            "max_output_tokens": 700,
        }
        if BOOTSTRAP_LLM_WEB_SEARCH:
            body["tools"] = [{"type": "web_search_preview", "search_context_size": "low"}]
        post = _post_openai
    else:
        body = {
            "model": BOOTSTRAP_LLM_MODEL or ANTHROPIC_LLM_MODEL,
            "max_tokens": 600,
            "messages": [{"role": "user", "content": prompt}],
        }
        if BOOTSTRAP_LLM_WEB_SEARCH:
            body["tools"] = [{
                "type": "web_search_20250305", "name": "web_search", "max_uses": 3,
            }]
        post = _post

    try:
        try:
            data = post(body)
        except RuntimeError:
            if "tools" not in body:
                raise
            # Model/account without web search: retry without the tool.
            body.pop("tools")
            data = post(body)
    except (RuntimeError, requests.RequestException) as exc:
        return {"error": str(exc)[:200]}

    if provider == "gemini":
        output = _gemini_text(data)
    elif provider == "openai":
        output = _openai_text(data)
    else:
        output = "".join(
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        )
    return _extract_json(output) or {"error": "LLM returned no JSON"}


def clean(value):
    """LLM fields come back as str/None/'null': normalize to str or ''."""
    if value is None:
        return ""

    value = re.sub(r"\s+", " ", str(value)).strip()
    return "" if value.lower() in ("null", "none", "unknown", "n/a") else value
