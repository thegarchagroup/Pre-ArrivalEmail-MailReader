"""
Step 1: send the pre-arrival email to every guest arriving tomorrow .. today + DAYS_AHEAD
(on 13 Nov: 14, 15 and 16 Nov). Guests arriving today are never emailed.
who hasn't had it yet.

    "To send"  -> sent now
    "Failed"   -> tried again, up to MAX_SEND_ATTEMPTS attempts in total
    "Sent"     -> never sent again (the backend also refuses to send twice)
    "No email" -> nothing to send
"""
import logging
import time
from datetime import timedelta

from services.api_client import ApiError
from settings import DAYS_AHEAD, MAX_SEND_ATTEMPTS, SEND_PAUSE_SECONDS, START_DAYS_AHEAD

log = logging.getLogger("reader")


def send_due_emails(api, hotel, today, dry_run=False):
    summary = {"sent": 0, "failed": 0, "skipped": 0, "gave_up": 0}
    first_day = today + timedelta(days=START_DAYS_AHEAD)     # tomorrow: today's arrivals aren't emailed
    last_day = first_day + timedelta(days=DAYS_AHEAD - 1)

    try:
        template = api.template(hotel.code)
    except ApiError as e:
        if e.status != 404:
            raise                         # backend down or login rejected: reported by main.py
        log.warning("%s: no email template saved for this hotel, nothing sent", hotel.name)
        return summary
    if not template.get("is_active"):
        log.info("%s: email template is switched off, nothing sent", hotel.name)
        return summary

    guests = []
    for status in ("pending", "failed"):
        guests += api.arrivals(hotel=hotel.code, date_from=first_day.isoformat(),
                               date_to=last_day.isoformat(), email_status=status, limit=5000)

    due = []
    for g in guests:
        if not g.get("email"):
            continue
        if g["email_status"] == "failed" and (g.get("email_attempts") or 0) >= MAX_SEND_ATTEMPTS:
            summary["gave_up"] += 1
            continue
        due.append(g)
    due.sort(key=lambda g: (g["check_in"], g["last_name"], g["first_name"]))

    if summary["gave_up"]:
        log.info("%s: %d guest(s) failed %d times and are left for staff (see Emails due)",
                 hotel.name, summary["gave_up"], MAX_SEND_ATTEMPTS)
    if not due:
        log.info("%s: no emails due for %s to %s", hotel.name, first_day, last_day)
        return summary
    log.info("%s: %d email(s) due for %s to %s", hotel.name, len(due), first_day, last_day)

    for n, g in enumerate(due, start=1):
        who = f"{g['first_name']} {g['last_name']} <{g['email']}> arriving {g['check_in']}"
        if dry_run:
            log.info("  [dry run] would send to %s", who)
            continue
        try:
            result = api.send_email(g["id"])
        except ApiError as e:
            summary["failed"] += 1
            log.error("  FAILED  %s: %s", who, e)
        else:
            status = result.get("status")
            summary[status if status in summary else "skipped"] += 1
            reason = f" ({result['reason']})" if result.get("reason") else ""
            log.info("  %-7s %s%s", status.upper(), who, reason)
        if n < len(due):
            time.sleep(SEND_PAUSE_SECONDS)
    return summary