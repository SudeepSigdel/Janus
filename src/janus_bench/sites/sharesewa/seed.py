"""Seed data for the ShareSewa replica (all companies, banks and numbers fictional)."""

from __future__ import annotations

from typing import Any

FIRST_NEW_ID = 46

ISSUES: dict[str, dict[str, Any]] = {
    "nic-asia-debenture": {
        "name_ne": "एनआईसी एशिया डिबेन्चर २०८३",
        "name_en": "NIC Asia Debenture 2083",
        "open_bs": "2083-04-01",
        "close_bs": "2083-04-15",
        "price": 100,
        "min_kitta": 10,
        "max_kitta": 5000,
    },
    "sunrise-bank-rights": {
        "name_ne": "सनराइज बैंक हक निष्कासन",
        "name_en": "Sunrise Bank Rights Issue",
        "open_bs": "2083-05-10",
        "close_bs": "2083-05-24",
        "price": 100,
        "min_kitta": 10,
        "max_kitta": 8000,
    },
    "himalayan-hydro-ipo": {
        "name_ne": "हिमालयन हाइड्रो आईपीओ",
        "name_en": "Himalayan Hydro IPO",
        "open_bs": "2083-06-01",
        "close_bs": "2083-06-10",
        "price": 100,
        "min_kitta": 10,
        "max_kitta": 3000,
    },
}

BANKS: dict[str, tuple[str, str]] = {
    "nabil": ("नबिल बैंक", "Nabil Bank"),
    "nic-asia": ("एनआईसी एशिया बैंक", "NIC Asia Bank"),
    "himalayan": ("हिमालयन बैंक", "Himalayan Bank"),
}

# (id, issue, bank, kitta, crn, status)
_SEED_ROWS = [
    ("041", "nic-asia-debenture", "nabil", 50, "CRN-100041", "allotted"),
    ("042", "sunrise-bank-rights", "nic-asia", 100, "CRN-100042", "submitted"),
    ("043", "himalayan-hydro-ipo", "himalayan", 200, "CRN-100043", "submitted"),
    ("044", "nic-asia-debenture", "himalayan", 80, "CRN-100044", "allotted"),
    ("045", "sunrise-bank-rights", "nabil", 150, "CRN-100045", "submitted"),
]


def seed(variant: str | None) -> dict[str, Any]:
    applications = {
        app_id: {
            "id": app_id,
            "issue": issue,
            "bank": bank,
            "kitta": kitta,
            "crn": crn,
            "status": status,
        }
        for app_id, issue, bank, kitta, crn, status in _SEED_ROWS
    }
    return {"logged_in": False, "applications": applications, "next_id": FIRST_NEW_ID}
