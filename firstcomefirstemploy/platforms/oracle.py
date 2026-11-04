"""Oracle Recruiting Cloud (hcmUI/CandidateExperience)."""

import re
import time
from urllib.parse import urlparse

from ..config import POLITE_DELAY
from ..filters import description_filters_requested, is_audit
from ..utils.http import request_json
from ..utils.text import clean_description
from .base import Platform
from .registry import register

PAGE_SIZE = 100


@register
class Oracle(Platform):
    name = "oracle"

    def detect(self, host, path):
        return (
            host.endswith("oraclecloud.com")
            and "/hcmui/candidateexperience/" in path
        )

    def discover(self, session, company):
        parsed = urlparse(company["career_url"])

        match = re.search(
            r"/hcmUI/CandidateExperience/([^/]+)/sites/([^/]+)",
            parsed.path,
            re.I,
        )

        if not match:
            raise ValueError("Invalid Oracle Candidate Experience URL")

        language, site = match.groups()
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        rest = f"{base_url}/hcmRestApi/resources/latest"

        return {
            "oracle_site": company.get("oracle_site", site),
            "oracle_language": language,
            "site_base_url": base_url,
            "api_url": f"{rest}/recruitingCEJobRequisitions",
            "detail_api_url": f"{rest}/recruitingCEJobRequisitionDetails",
        }

    def cache_key(self, metadata, search):
        return (
            self.name,
            metadata["api_url"],
            metadata["oracle_site"],
            description_filters_requested(search),
        )

    def _description(self, session, metadata, job_id):
        data = request_json(
            session,
            metadata["detail_api_url"],
            params={
                "onlyData": "true",
                "expand": "all",
                "finder": (
                    f'ById;Id="{job_id}",siteNumber={metadata["oracle_site"]}'
                ),
            },
        )

        items = data.get("items") or []

        if not items:
            return ""

        detail = items[0]

        return clean_description("\n".join(
            str(part) for part in (
                detail.get("ExternalDescriptionStr"),
                detail.get("ExternalResponsibilitiesStr"),
                detail.get("ExternalQualificationsStr"),
                detail.get("ShortDescriptionStr"),
            ) if part
        ))

    def fetch_raw(self, session, company, metadata, search):
        audit = is_audit(search)
        need_description = description_filters_requested(search)

        jobs = {}
        offset = 0
        total = None

        while True:
            data = request_json(
                session,
                metadata["api_url"],
                params={
                    "onlyData": "true",
                    "expand": "requisitionList",
                    "finder": (
                        f"findReqs;siteNumber={metadata['oracle_site']},"
                        f"limit={PAGE_SIZE},offset={offset}"
                    ),
                },
            )

            items = data.get("items") or []

            if not items:
                if offset == 0:
                    raise RuntimeError("Oracle returned no requisition results")
                break

            block = items[0]
            postings = block.get("requisitionList") or []

            if total is None:
                total = block.get("TotalJobsCount") or data.get("totalResults") or 0
                print("  Oracle postings:", total)

            if not postings:
                break

            for posting in postings:
                if posting.get("Id") is None:
                    continue

                jid = str(posting["Id"])

                description = clean_description(
                    posting.get("ExternalDescriptionStr")
                    or posting.get("ShortDescriptionStr")
                    or ""
                )

                if need_description:
                    try:
                        description = self._description(session, metadata, jid)
                    except Exception as exc:
                        if not audit:
                            raise RuntimeError(
                                f"Oracle description unavailable for {jid}: {exc}"
                            ) from exc

                        print(f"  Description unavailable: {jid}")

                url = (
                    posting.get("ExternalUrlSeo")
                    or posting.get("ExternalUrl")
                    or (
                        f"{metadata['site_base_url']}/hcmUI/CandidateExperience/"
                        f"{metadata['oracle_language']}/sites/"
                        f"{metadata['oracle_site']}/job/{jid}"
                    )
                )

                jobs[jid] = {
                    "title": (posting.get("Title") or "").strip(),
                    "location": posting.get("PrimaryLocation") or "",
                    "url": url,
                    "posted": (
                        posting.get("PostedDate")
                        or posting.get("ExternalPostedStartDate")
                        or ""
                    ),
                    "_description": description,
                }

            offset += len(postings)

            if len(postings) < PAGE_SIZE:
                break

            time.sleep(POLITE_DELAY)

        if total and not jobs:
            raise RuntimeError("Oracle reported jobs but no job IDs were parsed")

        return jobs, False
