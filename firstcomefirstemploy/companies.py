"""Load and select companies from companies.json."""

from .config import CONFIG_FILE
from .utils.storage import load_json
from .utils.text import normalize


def load_companies():
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"Missing configuration: {CONFIG_FILE}")

    companies = load_json(CONFIG_FILE).get("companies", [])
    names = set()

    for company in companies:
        if not company.get("name"):
            raise ValueError("Company name missing")

        if not company.get("career_url"):
            raise ValueError(f"Career URL missing: {company['name']}")

        key = normalize(company["name"])

        if key in names:
            raise ValueError(f"Duplicate company: {company['name']}")

        names.add(key)

    return companies


def select_companies(companies, requested=None):
    if not requested or requested == "*":
        return [c for c in companies if c.get("enabled", True)]

    matches = [
        c for c in companies if normalize(c["name"]) == normalize(requested)
    ]

    if not matches:
        raise ValueError(f"Company not found: {requested}")

    return matches


def list_companies(companies):
    print("\nCONFIGURED COMPANIES")
    print("=" * 65)

    for company in companies:
        status = "ENABLED" if company.get("enabled", True) else "DISABLED"
        print(f"\n{company['name']} | {company.get('platform', 'auto')} | {status}")
        print("URL:", company["career_url"])

        for search in company.get("searches", []):
            print("  -", search["name"])
