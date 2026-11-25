"""User-tunable matching data: title presets and country aliases."""

# Matching is on whole words, case-insensitive.
TITLE_PRESETS = {
    "tech_roles": {
        "any": [
            "software engineer", "software developer",
            "software development engineer", "sde",
            "backend engineer", "full stack engineer",
            "machine learning", "ml", "ai engineer",
            "applied scientist", "research scientist",
            "research engineer", "data scientist",
            "forward deployed", "fde",
            "member of technical staff", "member technical staff",
            "quantitative", "quant", "member technical", "mts",
            "software engineering", "mle", "sr engineer",
            "backend", "engineer", "computer scientist", "application developer"
        ],
        "exclude": [
            "intern", "internship", "apprentice", "senior staff", "staff engineer", "staff software", "staff research", "staff mechanical", "staff forward", "staff partner", "staff information", "principal",
            "architect", "phd", "fixed term contract", "manager", "lead",
            "qa", "testing", "test", "verification", "asic", "fpga",
            "management", "validation", "design engineer",
            "solutions engineer", "hardware engineer", "modelling engineer",
            "devops engineer", "system engineer", "systems engineer",
            "thermal engineer", "dft engineer", "support engineer", "director", "president", "quality assurance"
        ],
    }
}

COUNTRY_ALIASES = {
    "india": [
        "india", "bengaluru", "bangalore", "hyderabad"],
#    "india": [
#        "india", "bengaluru", "bangalore", "hyderabad", "mumbai", "pune",
#        "chennai", "gurgaon", "gurugram", "noida", "new delhi", "delhi",
#        "ahmedabad", "kolkata", "kochi",
#    ],
    "united states": [
        "united states", "usa", "u.s.", "new york", "california", "texas",
        "seattle", "san francisco", "austin", "chicago", "boston",
    ],
    "united kingdom": [
        "united kingdom", "uk", "london", "manchester", "cambridge",
        "edinburgh",
    ],
    "canada": [
        "canada", "toronto", "vancouver", "montreal", "ottawa", "waterloo",
    ],
    "australia": ["australia", "sydney", "melbourne", "brisbane", "perth"],
    "singapore": ["singapore"],
    "germany": ["germany", "berlin", "munich", "frankfurt", "hamburg"],
}

# Bootstrap configuration
# normalized company name -> (platform, slug)
KNOWN_BOARDS = {
    "glean": ("greenhouse", "gleanwork"),
    "razorpay": ("greenhouse", "razorpaysoftwareprivatelimited"),
    "mistral ai": ("lever", "mistral"),
    "openai": ("ashby", "openai"),
    "anthropic": ("greenhouse", "anthropic"),
    "databricks": ("greenhouse", "databricks"),
    "elevenlabs": ("ashby", "elevenlabs"),
    "cohere": ("ashby", "cohere"),
    "stripe": ("greenhouse", "stripe"),
}

SKIP_NAMES = {"stealth co"}

BOOTSTRAP_NOISE_WORDS = {"ai", "labs", "capital", "technologies", "inc", "hq"}

# CSV location aliases, in priority order. Add countries here as needed.
BOOTSTRAP_COUNTRY_ALIASES = {
    "india": {"blr", "hyd", "india", "bangalore", "bengaluru", "hyderabad", "pune", "mumbai", "delhi", "noida"},
    "singapore": {"sgp", "singapore"},
    "united kingdom": {"london", "uk", "united kingdom", "u.k."},
    "remote": {"remote"},
    "united states": {"usa", "us", "u.s.", "united states", "new york", "san francisco", "seattle"},
    "canada": {"canada", "toronto", "vancouver", "montreal", "ottawa"},
    "australia": {"australia", "sydney", "melbourne", "brisbane", "perth"},
    "germany": {"germany", "berlin", "munich", "frankfurt", "hamburg"},
}
BOOTSTRAP_DEFAULT_COUNTRIES = ("india",)

# A board this small is probably another company's (or a stub).
MIN_CONFIDENT_JOBS = 5

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

