"""Offline tests for --bootstrap (no network: platforms/HTTP are mocked).

Run from the folder that contains FirstComeFirstEmploy.py:
    python -m unittest firstcomefirstemploy.tests.test_bootstrap -v
(or `python -m unittest discover -s firstcomefirstemploy/tests -t .`)
"""

import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from .. import config
from ..commands import bootstrap as b
from ..platforms import get_platform
from ..platforms import workday as wd
from ..utils import llm


def board(platform, company, jobs=None, via=None):
    jobs = jobs if jobs is not None else {
        str(i): {"title": "Software Engineer", "location": "Bengaluru, India"} for i in range(10)
    }
    return {"platform": platform, "company": company, "jobs": jobs,
            "total": len(jobs), "live_checked": True}


class _Both:
    def __init__(self, *patches):
        self.patches = patches

    def __enter__(self):
        for p in self.patches:
            p.start()

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()


class Slugs(unittest.TestCase):
    def test_candidates(self):
        self.assertEqual(b.candidate_slugs("Foo Labs AI"),
                         ["foolabsai", "foo-labs-ai", "foo"])
        self.assertEqual(b.candidate_slugs("Acme"), ["acme"])
        self.assertEqual(b.candidate_slugs("Acme Capital Inc"),
                         ["acmecapitalinc", "acme-capital-inc", "acme"])

    def test_countries(self):
        self.assertEqual(b.countries_from_location("BLR / SGP"), ["india", "singapore"])
        self.assertEqual(b.countries_from_location(""), ["india"])


class PlatformOrder(unittest.TestCase):
    def test_order_cheap_first_workday_last(self):
        names = [p.name for p in b._guessing_platforms()]
        self.assertEqual(names[:4], ["greenhouse", "lever", "ashby", "smartrecruiters"])
        self.assertEqual(names[-1], "workday")
        self.assertIn("rippling", names)
        self.assertIn("turbohire", names)

    def test_lever_tries_us_then_eu(self):
        c = get_platform("lever").bootstrap_candidates(["acme"])
        self.assertEqual([x["career_url"] for x in c],
                         ["https://jobs.lever.co/acme", "https://jobs.eu.lever.co/acme"])
        self.assertEqual(c[1]["region"], "eu")

    def test_dedicated_portals(self):
        self.assertEqual(get_platform("google").known_board("Google")["career_url"][:23], "https://www.google.com/")
        self.assertEqual(get_platform("deshaw").known_board("D. E. Shaw")["career_url"],
                         "https://www.deshawindia.com/careers")
        self.assertIsNone(get_platform("google").known_board("Acme"))


class Resolve(unittest.TestCase):
    def setUp(self):
        self.calls = []
        patcher = mock.patch.object(b, "POLITE_DELAY", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake_verify(self, hits):
        """hits: {career_url: board}; records every URL tried."""
        def verify(platform_self, session, company):
            self.calls.append((platform_self.name, company["career_url"]))
            hit = hits.get(company["career_url"])
            return dict(hit) if hit else None
        stack = mock.patch("firstcomefirstemploy.platforms.base.Platform.verify", verify)
        wd_patch = mock.patch.object(wd.Workday, "verify", verify)  # Workday overrides verify
        return _Both(stack, wd_patch)

    def test_eu_lever_hit_stops_search(self):
        url = "https://jobs.eu.lever.co/acme"
        with self.fake_verify({url: board("lever", {"slug": "acme", "career_url": url, "region": "eu"})}), \
             mock.patch.object(wd.Workday, "find_board", side_effect=AssertionError("too early")):
            got, _ = b.resolve_company(None, "Acme", "BLR", use_llm=False)
        self.assertEqual(got["platform"], "lever")
        self.assertEqual(got["found_via"], "probe")
        self.assertEqual(self.calls[-1], ("lever", url))
        self.assertEqual(self.calls[0][0], "greenhouse")

    def test_tiny_board_does_not_beat_a_real_one(self):
        # ramp case: greenhouse has a 1-job stub, ashby has the real board
        gh = "https://boards.greenhouse.io/ramp"
        ab = "https://jobs.ashbyhq.com/ramp"
        stub = board("greenhouse", {"slug": "ramp", "career_url": gh}, jobs={"1": {"title": "x", "location": ""}})
        real = board("ashby", {"slug": "ramp", "career_url": ab})
        with self.fake_verify({gh: stub, ab: real}):
            got, _ = b.resolve_company(None, "Ramp", "BLR", use_llm=False)
        self.assertEqual(got["platform"], "ashby")
        self.assertEqual(got["found_via"], "probe")

    def test_only_tiny_board_is_kept_but_flagged(self):
        gh = "https://boards.greenhouse.io/solo"
        stub = board("greenhouse", {"slug": "solo", "career_url": gh}, jobs={"1": {"title": "x", "location": ""}})
        with self.fake_verify({gh: stub}), \
             mock.patch.object(wd.Workday, "find_board", side_effect=AssertionError("not needed")):
            got, _ = b.resolve_company(None, "Solo", "BLR", use_llm=False)
        self.assertEqual(got["found_via"], "probe:small-board")

    def test_known_portal_skips_probing(self):
        with self.fake_verify({"https://www.amazon.jobs/en/search":
                               {"platform": "amazon", "company": {"career_url": "https://www.amazon.jobs/en/search"},
                                "jobs": {}, "total": None, "live_checked": False}}):
            got, _ = b.resolve_company(None, "Amazon", "BLR", use_llm=False)
        self.assertEqual(got["found_via"], "known-portal")
        self.assertEqual(len(self.calls), 1)

    def test_weak_first_word_flag(self):
        url = "https://boards.greenhouse.io/foo"
        with self.fake_verify({url: board("greenhouse", {"slug": "foo", "career_url": url})}):
            got, _ = b.resolve_company(None, "Foo Labs AI", "BLR", use_llm=False)
        self.assertEqual(got["found_via"], "probe:first-word-slug")

    def test_workday_runs_after_all_cheap_platforms(self):
        order = []
        with self.fake_verify({}), \
             mock.patch.object(wd.Workday, "find_board", lambda s, sess, slugs, delay=0: order.append(("workday", list(slugs)))):
            got, info = b.resolve_company(None, "Nothing Corp", "BLR", use_llm=False)
        self.assertIsNone(got)
        self.assertEqual(order, [("workday", ["nothingcorp", "nothing-corp", "nothing"])])
        # 3 slugs x (gh, lever us+eu, ashby, sr, rippling, turbohire) = 3 x 7
        self.assertEqual(len(self.calls), 21)
        self.assertEqual(info["tried_slugs"], "nothingcorp nothing-corp nothing")

    def test_llm_url_is_verified_by_our_platform(self):
        url = "https://acme.wd5.myworkdayjobs.com/en-US/Acme_Careers"
        answer = {"platform": "workday", "career_url": url, "slug": None, "notes": "found"}
        with self.fake_verify({url: board("workday", {"career_url": url})}), \
             mock.patch.object(wd.Workday, "find_board", return_value=None), \
             mock.patch.object(b, "ask_board", return_value=answer):
            got, info = b.resolve_company(None, "Acme", "BLR", use_llm=True)
        self.assertEqual(got["found_via"], "llm")
        self.assertEqual(got["platform"], "workday")
        self.assertEqual(info["llm_platform"], "workday")

    def test_llm_hallucination_goes_to_unknown_with_hint(self):
        answer = {"platform": "icims", "career_url": "https://careers-acme.icims.com/jobs",
                  "slug": None, "notes": "uses iCIMS"}
        with self.fake_verify({}), \
             mock.patch.object(wd.Workday, "find_board", return_value=None), \
             mock.patch.object(b, "ask_board", return_value=answer):
            got, info = b.resolve_company(None, "Acme", "BLR", use_llm=True)
        self.assertIsNone(got)  # unsupported platform: nothing to verify
        self.assertEqual(info["llm_platform"], "icims")
        self.assertEqual(info["llm_career_url"], "https://careers-acme.icims.com/jobs")

    def test_llm_slug_for_supported_platform_is_verified(self):
        url = "https://jobs.ashbyhq.com/acme-hq"
        answer = {"platform": "ashby", "career_url": None, "slug": "acme-hq"}
        with self.fake_verify({url: board("ashby", {"slug": "acme-hq", "career_url": url})}), \
             mock.patch.object(wd.Workday, "find_board", return_value=None), \
             mock.patch.object(b, "ask_board", return_value=answer):
            got, _ = b.resolve_company(None, "Acme", "BLR", use_llm=True)
        self.assertEqual(got["found_via"], "llm")

    def test_llm_error_is_unknown_not_crash(self):
        with self.fake_verify({}), \
             mock.patch.object(wd.Workday, "find_board", return_value=None), \
             mock.patch.object(b, "ask_board", return_value={"error": "HTTP 401"}):
            got, info = b.resolve_company(None, "Acme", "BLR", use_llm=True)
        self.assertIsNone(got)
        self.assertEqual(info["llm_notes"], "HTTP 401")


class FakeWorkday:
    """CXS POST server. boards: {(tenant, pod): {site: total}}.

    unknown_style="refuse": made-up tenants fail to connect (distinguishable)
    unknown_style="404":    made-up tenants answer exactly like a real tenant
                            with a wrong site (indistinguishable -> blind mode)
    """

    class Resp:
        def __init__(self, status, payload=None, text=""):
            self.status_code, self._payload, self.text = status, payload, text

        def json(self):
            if self._payload is None:
                raise ValueError("no json")
            return self._payload

    def __init__(self, boards, unknown_style="refuse"):
        self.boards, self.unknown_style, self.posts = boards, unknown_style, []

    def post(self, url, json=None, timeout=None):
        import requests
        self.posts.append(url)
        host, path = url.split("/")[2], url.split("/")
        tenant, pod = host.split(".")[0], int(host.split(".")[1][2:])
        site = path[path.index("cxs") + 2]
        sites = self.boards.get((tenant, pod))

        if sites is None:
            if self.unknown_style == "refuse":
                raise requests.ConnectionError("no such host")
            return self.Resp(404, None, f"tenant {tenant} site {site} not found")

        if site in sites:
            return self.Resp(200, {"total": sites[site], "jobPostings": []})

        return self.Resp(404, None, f"tenant {tenant} site {site} not found")


class WorkdayGuess(unittest.TestCase):
    META = {"api_url": "x", "job_base_url": "x", "total_postings": 120}
    PAGE = {"jobPostings": [{"title": "Software Engineer", "locationsText": "Bangalore",
                             "externalPath": "/job/Bangalore/SE_JR1234", "bulletFields": ["JR1234"]}]}

    def setUp(self):
        wd._reset_gate()
        self.addCleanup(wd._reset_gate)

    def run_find(self, fake, slug):
        meta = dict(self.META, tenant=slug)
        with mock.patch.object(wd.Workday, "discover", return_value=meta), \
             mock.patch.object(wd, "request_json", return_value=self.PAGE):
            return wd.Workday().find_board(fake, [slug])

    def test_selective_gate_finds_pod_and_site(self):
        fake = FakeWorkday({("salesforce", 12): {"External_Career_Site": 1605},
                            ("acme", 3): {"AcmeJobs": 5, "External": 120}})
        got = self.run_find(fake, "acme")
        self.assertEqual(got["company"]["career_url"], "https://acme.wd3.myworkdayjobs.com/External")
        self.assertEqual(got["company"]["tenant"], "acme")
        self.assertEqual(got["total"], 120)
        self.assertTrue(got["sampled"])
        self.assertEqual(wd._MODE["value"], "selective")

    def test_pod_beyond_first_three_is_reached(self):
        # the live bug: salesforce sits on wd12, the 4th pod
        fake = FakeWorkday({("salesforce", 12): {"External_Career_Site": 1605}})
        got = self.run_find(fake, "salesforce")
        self.assertEqual(got["company"]["career_url"],
                         "https://salesforce.wd12.myworkdayjobs.com/External_Career_Site")

    def test_selective_gate_miss_is_cheap(self):
        fake = FakeWorkday({("salesforce", 12): {"External_Career_Site": 1605}})
        self.assertIsNone(self.run_find(fake, "nothingcorp"))
        # calibration(2) + 10 controls... + 10 pod checks, no site brute force
        self.assertLess(len(fake.posts), 40)

    def test_blind_gate_falls_back_to_bounded_brute_force(self):
        fake = FakeWorkday({("salesforce", 12): {"External_Career_Site": 1605},
                            ("acme", 5): {"External": 50}}, unknown_style="404")
        got = self.run_find(fake, "acme")
        self.assertEqual(wd._MODE["value"], "blind")
        self.assertEqual(got["company"]["career_url"], "https://acme.wd5.myworkdayjobs.com/External")
        brute = [u for u in fake.posts if "/acme/" in u and "zz-probe" not in u]
        self.assertLessEqual(len(brute), len(config.WORKDAY_PODS) * 6)

    def test_site_not_in_guess_list_is_not_found(self):
        fake = FakeWorkday({("salesforce", 12): {"External_Career_Site": 1605},
                            ("acme", 5): {"SomethingUnusual": 50}})
        self.assertIsNone(self.run_find(fake, "acme"))

    def test_bad_slug_chars_skipped(self):
        fake = FakeWorkday({})
        self.assertIsNone(wd.Workday().find_board(fake, ["Foo Bar", "a_b"]))
        self.assertEqual(fake.posts, [])


class LLMParsing(unittest.TestCase):
    def test_extract_json_ignores_prose(self):
        text = 'Sure! {"platform": "lever", "slug": "x"} hope that helps'
        self.assertEqual(llm._extract_json(text)["slug"], "x")
        self.assertIsNone(llm._extract_json("no json here"))

    def test_clean(self):
        self.assertEqual(llm.clean(None), "")
        self.assertEqual(llm.clean("null"), "")
        self.assertEqual(llm.clean(" a  b "), "a b")

    def test_provider_selection(self):
        with mock.patch.object(llm, "BOOTSTRAP_LLM_PROVIDER", "auto"), \
             mock.patch.object(llm, "ANTHROPIC_API_KEY", ""), \
             mock.patch.object(llm, "OPENAI_API_KEY", "sk-test"):
            self.assertEqual(llm.selected_provider(), "openai")
        with mock.patch.object(llm, "BOOTSTRAP_LLM_PROVIDER", "anthropic"), \
             mock.patch.object(llm, "ANTHROPIC_API_KEY", ""), \
             mock.patch.object(llm, "OPENAI_API_KEY", "sk-test"):
            self.assertFalse(llm.llm_available())

    def test_openai_response_and_search_fallback(self):
        calls = []

        def post(body):
            calls.append(dict(body))
            if "tools" in body:
                raise RuntimeError("HTTP 400: unsupported web search")
            return {"output": [{"type": "message", "content": [
                {"type": "output_text", "text": '{"platform":"lever","slug":"acme"}'},
            ]}]}

        with mock.patch.object(llm, "BOOTSTRAP_LLM_PROVIDER", "openai"), \
             mock.patch.object(llm, "OPENAI_API_KEY", "sk-test"), \
             mock.patch.object(llm, "BOOTSTRAP_LLM_MODEL", ""), \
             mock.patch.object(llm, "BOOTSTRAP_LLM_WEB_SEARCH", True), \
             mock.patch.object(llm, "_post_openai", post):
            out = llm.ask_board("Acme", "BLR")
        self.assertEqual(["tools" in b for b in calls], [True, False])
        self.assertEqual(calls[0]["model"], "gpt-4.1-mini")
        self.assertEqual(out["slug"], "acme")

    def test_ask_board_retries_without_web_search(self):
        calls = []

        def post(body):
            calls.append("tools" in body)
            if "tools" in body:
                raise RuntimeError("HTTP 400: tool not supported")
            return {"content": [{"type": "text", "text": '{"platform":"ashby","slug":"a"}'}]}

        with mock.patch.object(llm, "BOOTSTRAP_LLM_WEB_SEARCH", True), \
             mock.patch.object(llm, "BOOTSTRAP_LLM_PROVIDER", "anthropic"), \
             mock.patch.object(llm, "ANTHROPIC_API_KEY", "sk-test"), \
             mock.patch.object(llm, "_post", post):
            out = llm.ask_board("Acme", "BLR")
        self.assertEqual(calls, [True, False])
        self.assertEqual(out["platform"], "ashby")


class EndToEnd(unittest.TestCase):
    def test_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src = tmp / "in.csv"
            src.write_text("Name,Location\nAcme,BLR\nGhost Co,BLR\nOpenAI,SF\nstealth co,x\nA/B,y\nAcme,BLR\n")
            gen, unres, cfg = tmp / "gen.json", tmp / "unres.csv", tmp / "companies.json"
            url = "https://jobs.lever.co/acme"

            def verify(self_, session, company):
                if company["career_url"] == url:
                    return board("lever", dict(company))
                if company["career_url"] == "https://jobs.ashbyhq.com/openai":
                    return board("ashby", dict(company))
                return None

            ghost = {"platform": "bamboohr", "career_url": "https://ghost.bamboohr.com/careers", "notes": "BambooHR"}
            with mock.patch.multiple(b, GENERATED_FILE=gen, UNRESOLVED_FILE=unres, CONFIG_FILE=cfg, POLITE_DELAY=0), \
                 mock.patch("firstcomefirstemploy.platforms.base.Platform.verify", verify), \
                 mock.patch.object(wd.Workday, "find_board", return_value=None), \
                 mock.patch.object(b, "llm_available", return_value=True), \
                 mock.patch.object(b, "ask_board", return_value=ghost):
                b.run_bootstrap(src, use_llm=True)

            data = json.loads(gen.read_text())["companies"]
            self.assertEqual([c["name"] for c in data], ["Acme", "OpenAI"])
            self.assertEqual(data[0]["platform"], "lever")
            self.assertEqual(data[0]["slug"], "acme")
            self.assertEqual(data[0]["found_via"], "probe")
            self.assertEqual(data[1]["found_via"], "known-board")

            with open(unres, encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual([r["name"] for r in rows], ["Ghost Co"])
            self.assertEqual(rows[0]["llm_platform"], "bamboohr")
            self.assertEqual(rows[0]["llm_career_url"], "https://ghost.bamboohr.com/careers")

            # the generated file merges unchanged
            with mock.patch.object(b, "CONFIG_FILE", cfg):
                b.run_merge(gen)
            self.assertEqual(len(json.loads(cfg.read_text())["companies"]), 2)


if __name__ == "__main__":
    unittest.main()

class GeminiLLM(unittest.TestCase):
    def test_explicit_provider(self):
        with mock.patch.object(llm, "BOOTSTRAP_LLM_PROVIDER", "gemini"), \
             mock.patch.object(llm, "GEMINI_API_KEY", "test-key"):
            self.assertEqual(llm.selected_provider(), "gemini")
            self.assertTrue(llm.llm_available())

    def test_auto_provider_with_only_gemini_key(self):
        with mock.patch.object(llm, "BOOTSTRAP_LLM_PROVIDER", "auto"), \
             mock.patch.object(llm, "GEMINI_API_KEY", "test-key"), \
             mock.patch.object(llm, "ANTHROPIC_API_KEY", ""), \
             mock.patch.object(llm, "OPENAI_API_KEY", ""):
            self.assertEqual(llm.selected_provider(), "gemini")

    def test_gemini_request_and_response(self):
        payload = {"candidates": [{"content": {"parts": [{"text": '{"platform":"ashby","career_url":"https://jobs.ashbyhq.com/acme"}'}]}}]}
        with mock.patch.object(llm, "BOOTSTRAP_LLM_PROVIDER", "gemini"), \
             mock.patch.object(llm, "GEMINI_API_KEY", "test-key"), \
             mock.patch.object(llm, "BOOTSTRAP_LLM_WEB_SEARCH", False), \
             mock.patch.object(llm, "_post_gemini", return_value=payload) as post:
            answer = llm.ask_board("Acme", "India")
        self.assertEqual(answer["platform"], "ashby")
        body, model = post.call_args.args
        self.assertEqual(model, llm.GEMINI_LLM_MODEL)
        self.assertNotIn("tools", body)

    def test_grounding_error_retries_without_tool(self):
        payload = {"candidates": [{"content": {"parts": [{"text": '{"platform":"unknown","career_url":null}'}]}}]}
        calls = []
        def fake_post(body, model):
            calls.append(dict(body))
            if len(calls) == 1:
                raise RuntimeError("HTTP 429")
            return payload
        with mock.patch.object(llm, "BOOTSTRAP_LLM_PROVIDER", "gemini"), \
             mock.patch.object(llm, "GEMINI_API_KEY", "test-key"), \
             mock.patch.object(llm, "BOOTSTRAP_LLM_WEB_SEARCH", True), \
             mock.patch.object(llm, "_post_gemini", side_effect=fake_post):
            answer = llm.ask_board("Acme", "India")
        self.assertEqual(answer["platform"], "unknown")
        self.assertIn("tools", calls[0])
        self.assertNotIn("tools", calls[1])
