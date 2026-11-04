"""--test-email: send a sample notification to verify channel settings."""

from ..notifiers import notify_all

SAMPLE = {
    "SMTP TEST": {
        "added": {
            "TEST001": {
                "title": "Software Engineer - Backend",
                "location": "Bangalore, India",
                "url": "https://example.com/job/1",
                "posted": "Today",
            },
            "TEST002": {
                "title": "Machine Learning Engineer",
                "location": "Remote, India",
                "url": "https://example.com/job/2",
                "posted": "Yesterday",
            },
        },
        "removed": {
            "TEST003": {
                "title": "Quantitative Researcher",
                "location": "Mumbai, India",
                "url": "https://example.com/job/3",
            },
        },
        "modified": {
            "TEST004": {
                "old": {"title": "Software Engineer", "location": "Hyderabad"},
                "new": {
                    "title": "Senior Software Engineer",
                    "location": "Bangalore",
                    "url": "https://example.com/job/4",
                },
            },
        },
    }
}


def send_test_email():
    notify_all(SAMPLE)
