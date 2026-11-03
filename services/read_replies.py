"""
Step 2 (every 5 minutes): read the hotel mailbox and keep each guest's conversation in the app.

  INBOX  -> guests' emails (their words + attachments) appear in the guest's chat
  Sent   -> replies staff sent from Outlook/webmail appear in the chat too

- Both folders are opened READ-ONLY and every email is fetched with BODY.PEEK, so nothing is
  marked as read, moved or deleted. Outlook shows the mailbox exactly as before.
- Only headers are read first. The full email (with attachments) is downloaded only when the
  backend says it belongs to a guest conversation, so other emails are never copied anywhere.
- An email belongs to a guest when it answers one of our emails (In-Reply-To / References), or
  comes from / goes to the address of a guest we emailed who is arriving, staying, or left
  within the last few days. Everything else is ignored.
- Out-of-office and bounce messages are ignored.
- Emails that arrived before the service first connected are never imported. The last email
  read in each folder is remembered in reader_state.json.
"""
import email
import imaplib
import json
import logging
import re
import ssl
from email import policy

from services import mail_text
from settings import STATE_FILE

log = logging.getLogger("reader")

SENT_GUESSES = ("Sent", "INBOX.Sent", "Sent Items", "INBOX.Sent Items", "Sent Messages", "INBOX.Sent Messages")
HEADER_BATCH = 50


# ---------------------------------------------------------------- remembered position per folder
def load_state():
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    # older versions kept one position per hotel (the inbox)
    for code, v in list(state.items()):
        if isinstance(v, dict) and "uidvalidity" in v:
            state[code] = {"INBOX": v}
    return state


def save_state(state):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


# ---------------------------------------------------------------- IMAP helpers
def _quote(folder):
    return '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'


def find_sent_folder(imap, configured=""):
    if configured:
        return configured
    _, lines = imap.list()
    names = []
    for raw in lines or []:
        line = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
        m = re.match(r'\((?P<flags>[^)]*)\)\s+(?:"[^"]*"|NIL)\s+(?P<name>.+)$', line)
        if not m:
            continue
        name = m.group("name").strip().strip('"')
        if "\\sent" in m.group("flags").lower():
            return name
        names.append(name)
    return next((g for g in SENT_GUESSES if g in names), None)


def _fetch_raw(imap, uids, what):
    status, parts = imap.uid("FETCH", ",".join(str(u) for u in uids), what)
    return parts if status == "OK" else []


def _fetch(imap, uids, what):
    """{uid: bytes}. PEEK in `what` keeps every email unread."""
    out = {}
    for p in _fetch_raw(imap, uids, what):
        if isinstance(p, tuple) and p[0]:
            m = re.search(rb"UID (\d+)", p[0])
            if m:
                out[int(m.group(1))] = p[1]
    for uid in uids:                    # servers that put UID after the body: ask one by one
        if uid not in out:
            data = next((p[1] for p in _fetch_raw(imap, [uid], what) if isinstance(p, tuple)), None)
            if data is not None:
                out[uid] = data
    return out


# ---------------------------------------------------------------- one folder
def _read_folder(api, hotel, imap, folder, direction, state, dry_run, summary):
    status, _ = imap.select(_quote(folder), readonly=True)       # read-only: nothing is marked as read
    if status != "OK":
        raise RuntimeError(f"couldn't open {folder}")
    uidvalidity = int(imap.response("UIDVALIDITY")[1][0])
    hotel_state = state.setdefault(hotel.code, {})
    saved = hotel_state.get(folder) or {}

    if saved.get("uidvalidity") != uidvalidity:
        # First run (or the server renumbered the folder): start from the newest email now,
        # so old emails are never imported as new.
        _, data = imap.uid("SEARCH", None, "ALL")
        newest = max((int(u) for u in data[0].split()), default=0)
        hotel_state[folder] = {"uidvalidity": uidvalidity, "last_uid": newest}
        if not dry_run:
            save_state(state)
        log.info("%s: %s connected; watching for emails from now on", hotel.name, folder)
        return

    last_uid = int(saved.get("last_uid", 0))
    _, data = imap.uid("SEARCH", None, f"UID {last_uid + 1}:*")
    new_uids = sorted(int(u) for u in data[0].split() if int(u) > last_uid)
    mailbox = hotel.mailbox.lower()

    for start in range(0, len(new_uids), HEADER_BATCH):
        batch = new_uids[start:start + HEADER_BATCH]
        headers = _fetch(imap, batch, "(UID BODY.PEEK[HEADER])")
        candidates, items = {}, []
        for uid in batch:
            raw = headers.get(uid)
            if raw is None:
                continue
            msg = email.message_from_bytes(raw, policy=policy.default)
            name, from_addr = mail_text.sender(msg)
            if direction == "in":
                if not from_addr or from_addr == mailbox or mail_text.is_auto_reply(msg):
                    continue
                addrs = [from_addr]
            else:
                addrs = [a for a in mail_text.addresses(msg, "To", "Cc") if a != mailbox]
                if not addrs:
                    continue
            candidates[uid] = msg
            items.append({"key": str(uid), "direction": direction, "addresses": addrs,
                          "message_id": str(msg.get("Message-ID") or "") or None,
                          "in_reply_to": str(msg.get("In-Reply-To") or "") or None,
                          "references": [str(msg.get("References") or "")]})

        matches = api.match_messages(hotel.code, items) if items else {}   # ApiError -> retry next run

        for uid in batch:
            res = matches.get(str(uid)) or {}
            summary["read"] += 1
            if res.get("arrival_id") and not res.get("known"):
                full = _fetch(imap, [uid], "(UID BODY.PEEK[])").get(uid)
                if full is None:
                    log.warning("%s: couldn't download email %s in %s, will try again", hotel.name, uid, folder)
                    return
                msg = email.message_from_bytes(full, policy=policy.default)
                name, from_addr = mail_text.sender(msg)
                words = mail_text.own_words(msg)
                files, skipped = mail_text.attachments(msg)
                meta = {
                    "direction": direction, "message_id": str(msg.get("Message-ID") or "") or None,
                    "in_reply_to": str(msg.get("In-Reply-To") or "") or None,
                    "references": [str(msg.get("References") or "")],
                    "from_email": from_addr, "from_name": name or None,
                    "to": mail_text.addresses(msg, "To", "Cc"),
                    "addresses": [from_addr] if direction == "in" else
                                 [a for a in mail_text.addresses(msg, "To", "Cc") if a != mailbox],
                    "subject": str(msg.get("Subject") or ""), "date": str(msg.get("Date") or ""),
                    "text": words, "skipped_files": skipped,
                }
                who = from_addr if direction == "in" else ", ".join(meta["addresses"])
                extra = f" + {len(files)} file(s)" if files else ""
                if dry_run:
                    log.info("  [dry run] would add to chat (%s %s): %r%s", "from" if direction == "in" else "to",
                             who, words[:80], extra)
                else:
                    r = api.store_message(hotel.code, meta, files)
                    if r.get("stored"):
                        summary["guest" if direction == "in" else "staff"] += 1
                        log.info("  %s %s: %r%s", "GUEST " if direction == "in" else "OUTLOOK", who, words[:80], extra)
            else:
                summary["ignored"] += 1
            if not dry_run:
                hotel_state[folder]["last_uid"] = uid
                save_state(state)


# ---------------------------------------------------------------- one mailbox
def read_replies(api, hotel, today, state, dry_run=False):
    summary = {"read": 0, "guest": 0, "staff": 0, "ignored": 0}
    if not hotel.can_read_inbox:
        log.warning("%s: mailbox not checked, IMAP settings are missing in .env", hotel.name)
        return summary

    ctx = ssl.create_default_context()
    if not hotel.ssl_verify:
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE

    imap = imaplib.IMAP4_SSL(hotel.imap_server, hotel.imap_port, ssl_context=ctx, timeout=60)
    try:
        imap.login(hotel.mailbox, hotel.mailbox_password)
        _read_folder(api, hotel, imap, "INBOX", "in", state, dry_run, summary)
        if hotel.read_sent:
            sent = find_sent_folder(imap, hotel.sent_folder)
            if sent:
                _read_folder(api, hotel, imap, sent, "out", state, dry_run, summary)
            elif not state.get(hotel.code, {}).get("_no_sent_warned"):
                log.warning("%s: no Sent folder found; set IMAP%s_SENT_FOLDER in .env to read Outlook replies",
                            hotel.name, hotel.n)
                state.setdefault(hotel.code, {})["_no_sent_warned"] = True
    finally:
        try:
            imap.logout()
        except Exception:
            pass

    if summary["guest"] or summary["staff"]:
        log.info("%s: %d new email(s) checked, %d from guests, %d staff replies from Outlook, %d ignored",
                 hotel.name, summary["read"], summary["guest"], summary["staff"], summary["ignored"])
    return summary