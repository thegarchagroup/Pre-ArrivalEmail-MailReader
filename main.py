"""
Pre-arrival email service (Singapore time).

Every hour, on the hour, for all three hotels:
  1. Sends the pre-arrival email to guests arriving in the next 3 days, starting tomorrow,
     who haven't had it yet (e.g. on 13 Nov: guests arriving 14, 15 and 16 Nov).
     Guests arriving today are never emailed.

Every 5 minutes:
  2. Reads each hotel mailbox (read-only, nothing marked as read): guests' emails and
     attachments go into their chat in the app, and so do replies staff send from Outlook
     (Sent folder). Other emails are ignored.

    python main.py                  run now, then keep running
    python main.py --once           send + read once and exit
    python main.py --once --dry-run show what would happen; sends nothing, changes nothing
"""
import argparse
import logging
import math
import signal
import sys
import time
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

from services.api_client import ApiError, PrearrivalApi
from services.read_replies import load_state, read_replies
from services.send_due_emails import send_due_emails
from settings import CHECK_MAIL_EVERY_MINUTES, DAYS_AHEAD, HOTELS, LOG_FILE, SEND_EVERY_MINUTES, SGT, START_DAYS_AHEAD

log = logging.getLogger("reader")


def setup_logging():
    class SgtFormatter(logging.Formatter):
        def formatTime(self, record, datefmt=None):
            return datetime.fromtimestamp(record.created, SGT).strftime("%Y-%m-%d %H:%M:%S")

    fmt = SgtFormatter("%(asctime)s %(levelname)-7s %(message)s")
    console = logging.StreamHandler(sys.stdout)
    file_ = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    for h in (console, file_):
        h.setFormatter(fmt)
        log.addHandler(h)
    log.setLevel(logging.INFO)


def run_once(dry_run=False, send=True, read=True):
    today = datetime.now(SGT).date()
    tag = " (DRY RUN: nothing is sent or changed)" if dry_run else ""
    if send:
        log.info("=" * 70)
        first = today + timedelta(days=START_DAYS_AHEAD)
        log.info("Run started%s. Arrivals %s to %s", tag, first, first + timedelta(days=DAYS_AHEAD - 1))

    apis = {}   # one login per set of credentials

    def api_for(hotel):
        key = (hotel.api_username, hotel.api_password)
        if key not in apis:
            apis[key] = PrearrivalApi(*key)
        return apis[key]

    # 1. send due emails (hourly)
    if send:
        for hotel in HOTELS:
            if not hotel.can_use_api:
                log.warning("%s: skipped, no API login in .env", hotel.name)
                continue
            try:
                send_due_emails(api_for(hotel), hotel, today, dry_run=dry_run)
            except ApiError as e:
                log.error("%s: sending stopped: %s", hotel.name, e)
            except Exception:
                log.exception("%s: sending stopped by an unexpected error", hotel.name)

    # 2. read mailboxes into the guests' chats (every few minutes)
    if read:
        state = load_state()
        for hotel in HOTELS:
            if not hotel.can_use_api:
                continue
            try:
                read_replies(api_for(hotel), hotel, today, state, dry_run=dry_run)
            except ApiError as e:
                log.error("%s: mailbox check stopped, will retry next time: %s", hotel.name, e)
            except Exception as e:
                log.error("%s: mailbox check failed, will retry next time: %s", hotel.name, e)

    if send:
        log.info("Run finished")


def next_slot_after(now, step_minutes):
    """Next time on the clock that is a multiple of step_minutes (5 -> 10:05, 10:10, ...)."""
    step = max(1, step_minutes)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    minutes_since = (now - midnight).total_seconds() / 60
    return midnight + timedelta(minutes=(int(minutes_since // step) + 1) * step)


def is_send_slot(slot):
    minutes = slot.hour * 60 + slot.minute
    return minutes % max(1, SEND_EVERY_MINUTES) == 0


def main():
    ap = argparse.ArgumentParser(description="Hourly pre-arrival email sender and mailbox reader")
    ap.add_argument("--once", action="store_true", help="run one time and exit")
    ap.add_argument("--dry-run", action="store_true", help="show what would happen; send and change nothing")
    args = ap.parse_args()
    setup_logging()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))

    if args.once:
        run_once(dry_run=args.dry_run)
        return

    step = math.gcd(max(1, CHECK_MAIL_EVERY_MINUTES), max(1, SEND_EVERY_MINUTES))   # lands on every send time
    log.info("Pre-arrival email service started. Sends every %d minutes, checks mailboxes every %d minutes, "
             "Singapore time.", SEND_EVERY_MINUTES, CHECK_MAIL_EVERY_MINUTES)
    send = True                                     # first run: do everything
    while True:
        try:
            run_once(dry_run=args.dry_run, send=send, read=True)
        except Exception:                           # never let one bad run stop the service
            log.exception("Run failed")
        nxt = next_slot_after(datetime.now(SGT), step)
        send = is_send_slot(nxt)
        if send:
            log.info("Next send at %s", nxt.strftime("%Y-%m-%d %H:%M SGT"))
        while datetime.now(SGT) < nxt:              # short sleeps: stays accurate after a PC sleep/wake
            time.sleep(min(30, max(1, (nxt - datetime.now(SGT)).total_seconds())))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Stopped")