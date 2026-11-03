"""
All settings come from the .env file next to this file (see .env.example).
"""
import os
from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Singapore has no daylight saving, so a fixed +08:00 offset is exact.
SGT = timezone(timedelta(hours=8), "SGT")


def _int(name, default):
    value = (os.getenv(name) or "").strip()
    return int(value) if value else default


def _bool(name, default=True):
    value = (os.getenv(name) or "").strip().lower()
    return default if not value else value in ("1", "true", "yes", "on")


# ---------------------------------------------------------------- backend API
# Where the Pre-Arrival backend is reachable from this machine, for example
#   http://127.0.0.1:1802                              (same server, direct)
#   https://ai.thegarchagroup.com/prearrival-api       (through the web server)
API_URL = (os.getenv("PREARRIVAL_API_URL") or "http://127.0.0.1:1802").rstrip("/")
API_TIMEOUT_SECONDS = _int("API_TIMEOUT_SECONDS", 90)

# Option A: one admin login (AUTH_USERNAME in the backend .env) for all hotels.
ADMIN_USERNAME = (os.getenv("PREARRIVAL_API_USERNAME") or "").strip()
ADMIN_PASSWORD = os.getenv("PREARRIVAL_API_PASSWORD") or ""

# ---------------------------------------------------------------- what the job does
DAYS_AHEAD = _int("DAYS_AHEAD", 3)                  # today + 3 days: 02 -> 02, 03, 04, 05
MAX_SEND_ATTEMPTS = _int("MAX_SEND_ATTEMPTS", 3)    # failed emails are retried up to this many times
SEND_PAUSE_SECONDS = _int("SEND_PAUSE_SECONDS", 2)  # gap between emails, to stay under mail-server limits
SEND_EVERY_MINUTES = _int("SEND_EVERY_MINUTES", _int("RUN_EVERY_MINUTES", 60))  # sends on the hour: 09:00, 10:00 ...
CHECK_MAIL_EVERY_MINUTES = _int("CHECK_MAIL_EVERY_MINUTES", 5)                   # mailbox: 09:00, 09:05, 09:10 ...
RUN_EVERY_MINUTES = SEND_EVERY_MINUTES   # older name, kept so existing .env files still work

DATA_DIR = Path(os.getenv("DATA_DIR") or BASE_DIR)    # /data in Docker; this folder otherwise
STATE_FILE = DATA_DIR / "reader_state.json"         # last email read in each inbox
LOG_FILE = DATA_DIR / "reader.log"


# ---------------------------------------------------------------- hotels
@dataclass(frozen=True)
class Hotel:
    code: str
    name: str
    # login to the backend API
    api_username: str
    api_password: str
    # inbox (IMAP). On cPanel this is the same mailbox and password used for sending.
    imap_server: str
    imap_port: int
    mailbox: str
    mailbox_password: str
    ssl_verify: bool
    n: int = 0
    read_sent: bool = True       # also read the Sent folder, for replies staff send from Outlook
    sent_folder: str = ""        # found automatically; set IMAPn_SENT_FOLDER if it isn't

    @property
    def can_use_api(self):
        return bool(self.api_username and self.api_password)

    @property
    def can_read_inbox(self):
        return bool(self.imap_server and self.mailbox and self.mailbox_password)


def _hotel(code, name, n, default_server):
    """n: 1 = Vagabond, 2 = Serangoon, 3 = Maxwell (same numbering as the backend's SMTPn_*)."""
    return Hotel(
        code=code,
        name=name,
        # Option B: this hotel's own staff login. Used when set; otherwise the admin login.
        api_username=(os.getenv(f"{code}_API_USERNAME") or ADMIN_USERNAME).strip(),
        api_password=os.getenv(f"{code}_API_PASSWORD") or ADMIN_PASSWORD,
        imap_server=os.getenv(f"IMAP{n}_SERVER") or os.getenv(f"SMTP{n}_SERVER") or default_server,
        imap_port=_int(f"IMAP{n}_PORT", 993),
        mailbox=(os.getenv(f"IMAP{n}_EMAIL") or os.getenv(f"SMTP{n}_EMAIL") or "").strip(),
        mailbox_password=os.getenv(f"IMAP{n}_PASSWORD") or os.getenv(f"SMTP{n}_PASSWORD") or "",
        ssl_verify=_bool(f"IMAP{n}_SSL_VERIFY", True),
        n=n,
        read_sent=_bool("READ_SENT_FOLDER", True),
        sent_folder=(os.getenv(f"IMAP{n}_SENT_FOLDER") or "").strip(),
    )


HOTELS = [
    _hotel("TVC", "The Vagabond Club", 1, "mail.hotelvagabondsingapore.com"),
    _hotel("TSH", "The Serangoon House", 2, "mail.serangoonhouse.com"),
    _hotel("MR", "Maxwell Reserve", 3, "mail.maxwellreserve.com"),
]