"""Shared configuration and constants for the TTD booking assistant."""

import json
import os
import re
import time
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TTD_URL = "https://ttdevasthanams.ap.gov.in/home/dashboard"

# Seva-specific behavior observed on TTD Screen 2. This seva uses one
# booking ticket while still allowing multiple pilgrim rows on the details form.
GOTHRAM_SEVA_NAME = "Sri Srinivasa Divyaanugraha Homam"
GOTHRAM_FIELD_LABEL = "Gothram"
BROWSER_PROFILE_DIR = Path.home() / "ttd_browser_profile"
TTD_AUTH_STATE_FILE = BROWSER_PROFILE_DIR / "ttd_auth_state.json"
_PACKAGE_DATA_FILE = Path(__file__).resolve().parent / "pilgrims.json"
DATA_FILE = _PACKAGE_DATA_FILE if _PACKAGE_DATA_FILE.exists() else Path.cwd() / "pilgrims.json"
ARTIFACT_DIR = Path(__file__).resolve().parent / "ttd_runtime"
ARTIFACT_DIR.mkdir(exist_ok=True)

# Terminal output is intentionally concise by default. Set TTD_VERBOSE=1
# when detailed DOM/debug diagnostics are needed.
VERBOSE_LOGGING = os.getenv("TTD_VERBOSE", "0").strip().lower() in {"1", "true", "yes", "on"}

def debug(*args, **kwargs):
    if VERBOSE_LOGGING:
        print(*args, **kwargs)

FIELD_PATTERNS = {
    "name": [r'^\s*name\s*\*?\s*$', r'pilgrim.*name'],
    "age": [r'^\s*age\s*\*?\s*$', r'pilgrim.*age'],
    "gender": [r'^\s*gender\s*\*?\s*$', r'^\s*sex\s*$'],
    "id_type": [r'photo\s*id\s*proof', r'id\s*proof\s*type', r'proof\s*type'],
    "id_number": [r'photo\s*id\s*number', r'id\s*proof\s*number', r'proof\s*number', r'aadhaar', r'aadhar'],
    "email": [r'^\s*email\s*(?:address|id)?\s*\*?\s*$', r'e-mail'],
    "city": [r'^\s*city\s*\*?\s*$', r'town'],
    "state": [r'^\s*state\s*\*?\s*$'],
    "country": [r'^\s*country\s*\*?\s*$'],
    "pincode": [r'pincode', r'pin\s*code', r'postal\s*code', r'zip'],
    "tickets": [r'no\.?\s*of\s*tickets', r'number\s*of\s*tickets', r'tickets'],
    "laddus": [r'no\.?\s*of\s*laddus', r'number\s*of\s*laddus', r'laddu', r'laddoo'],
    "hundi": [r'hundi\s*offerings', r'hundi', r'offering'],
}

RELEASE_TEXT = [
    r"book\s*now", r"select\s*date", r"darshan\s*date",
    r"select\s*(?:time|slot)", r"available\s*quota",
    r"pilgrim\s*details", r"devotee\s*details", r"booking\s*details"
]
CLOSED_TEXT = [r"quota\s*not\s*released", r"quota\s*will\s*be\s*released", r"not\s*released"]
PAYMENT_TEXT = [
    r"\bpayment\b", r"pay\s*now", r"proceed\s*to\s*pay",
    r"payment\s*gateway", r"\bupi\b", r"credit\s*card",
    r"debit\s*card", r"net\s*banking"
]
BOOKING_IN_PROGRESS_TEXT = [
    r"pilgrims?\s+booking\s+is\s+already\s+in\s+progress",
    r"booking\s+is\s+already\s+in\s+progress",
    r"pilgrims?\s+booking\s+already\s+in\s+progress",
]

TTD_DATE_SELECTABLE_COLORS = {
    "rgb(255, 191, 0)",   # TTD "Filling Fast" (observed live DOM)
    "rgb(247, 202, 76)",  # alternate yellow used by some TTD builds
    "rgb(94, 184, 18)",   # Available green
    "rgb(121, 193, 57)",  # alternate green
}
TTD_DATE_BLOCKED_COLORS = {
    "rgb(163, 0, 14)",
    "rgb(232, 72, 60)",
    "rgb(255, 30, 34)",
    "rgb(121, 192, 235)",
    "rgb(5, 175, 232)",
    "rgb(204, 204, 204)",
}
