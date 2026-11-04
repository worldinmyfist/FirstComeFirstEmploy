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
            "backend", "engineer", "computer scientist",
        ],
        "exclude": [
            "intern", "internship", "apprentice", "staff", "principal",
            "architect", "phd", "fixed term contract", "manager", "lead",
            "qa", "testing", "test", "verification", "asic", "fpga",
            "management", "validation", "design engineer",
            "solutions engineer", "hardware engineer", "modelling engineer",
            "devops engineer", "system engineer", "systems engineer",
            "thermal engineer", "dft engineer", "support engineer",
        ],
    }
}

COUNTRY_ALIASES = {
    "india": [
        "india", "bengaluru", "bangalore", "hyderabad", "mumbai", "pune",
        "chennai", "gurgaon", "gurugram", "noida", "new delhi", "delhi",
        "ahmedabad", "kolkata", "kochi",
    ],
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
}
