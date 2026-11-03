"""
Turns one email into what the chat shows: the writer's own words (the quoted older
emails underneath are cut off) and the attached files.
"""
import re
from email.utils import getaddresses, parseaddr
from html import unescape

MAX_TEXT = 20_000
MAX_FILE_BYTES = 20 * 1024 * 1024        # bigger files are listed in the chat but not copied
SMALL_INLINE_IMAGE = 50 * 1024           # logos and icons in email signatures

AUTO_SUBJECT = re.compile(r"^\s*(automatic reply|auto[- ]?reply|auto[- ]?response|out of (the )?office|"
                          r"undeliverable|delivery status notification|mail delivery failed)", re.I)

# where the quoted older email starts (Gmail, Outlook, Apple Mail, phones)
QUOTE_START = re.compile(
    r"^(on\s.{3,250}\swrote:?\s*$"
    r"|-{2,}\s*original message\s*-{2,}"
    r"|-{2,}\s*forwarded message"
    r"|from:\s.+"
    r"|sent from my \w+"
    r"|sent from (outlook|mail|yahoo)"
    r"|get outlook for"
    r"|_{8,}\s*$)", re.I)
HTML_QUOTE_START = re.compile(
    r'<div[^>]+class="?[^">]*gmail_quote|<blockquote|<div[^>]+id="?(divRplyFwdMsg|appendonsend)'
    r'|<hr[^>]*>\s*<div[^>]*id="?divRplyFwdMsg|<div[^>]+class="?[^">]*(yahoo_quoted|moz-cite-prefix)', re.I)


def is_auto_reply(msg):
    return ((msg.get("Auto-Submitted") or "no").strip().lower() not in ("no", "")
            or bool(msg.get("X-Autoreply") or msg.get("X-Autorespond"))
            or (msg.get("Precedence") or "").strip().lower() in ("auto_reply", "bulk", "junk")
            or bool(AUTO_SUBJECT.match(str(msg.get("Subject") or ""))))


def addresses(msg, *headers):
    vals = [str(v) for h in headers for v in (msg.get_all(h) or [])]
    return [a.strip().lower() for _, a in getaddresses(vals) if a and "@" in a]


def sender(msg):
    name, addr = parseaddr(str(msg.get("From") or ""))
    return name.strip().strip('"'), addr.strip().lower()


def html_to_text(html):
    m = HTML_QUOTE_START.search(html)
    if m:
        html = html[:m.start()]
    html = re.sub(r"(?is)<(head|style|script|title)[^>]*>.*?</\1>", "", html)
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    html = re.sub(r"(?i)</(p|div|li|tr|h[1-6]|table)>|<hr[^>]*>", "\n", html)
    html = re.sub(r"(?i)<li[^>]*>", "• ", html)
    return unescape(re.sub(r"<[^>]+>", "", html)).replace("\xa0", " ")


def _cut_quotes(body):
    lines = body.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    for i, line in enumerate(lines):
        s = line.strip()
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if QUOTE_START.match(s) or (s.lower().startswith("on ") and nxt.lower().endswith("wrote:")):
            break
        if s == "--" or line.rstrip() == "-- ":           # signature separator
            break
        if s.startswith(">"):
            continue
        out.append(s)
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:MAX_TEXT] + ("…" if len(text) > MAX_TEXT else "")


def _body_parts(msg):
    plain = msg.get_body(preferencelist=("plain",))
    html = msg.get_body(preferencelist=("html",))
    return plain, html


def own_words(msg):
    """The writer's new text, without the quoted conversation below it."""
    plain, html = _body_parts(msg)
    try:
        if plain is not None:
            text = _cut_quotes(plain.get_content())
            if text:
                return text
        if html is not None:
            return _cut_quotes(html_to_text(html.get_content()))
    except Exception:
        pass
    return ""


def attachments(msg):
    """-> (files, skipped): files [(filename, content_type, bytes)], skipped [{filename, size, reason}]"""
    plain, html = _body_parts(msg)
    body_ids = {id(p) for p in (plain, html) if p is not None}
    files, skipped, n = [], [], 0
    for part in msg.walk():
        if part.is_multipart() or id(part) in body_ids:
            continue
        ctype = part.get_content_type()
        disp = part.get_content_disposition()
        name = part.get_filename()
        if ctype in ("text/plain", "text/html") and disp != "attachment" and not name:
            continue                                   # other versions of the body
        if ctype == "message/rfc822":
            data = part.get_payload(0).as_bytes() if part.get_payload() else b""
            name = name or "attached-email.eml"
        else:
            data = part.get_payload(decode=True)
        if not data:
            continue
        if disp != "attachment" and part.get("Content-ID") and len(data) < SMALL_INLINE_IMAGE:
            continue                                   # signature logos, social icons
        n += 1
        if not name:
            ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
                   "application/pdf": ".pdf"}.get(ctype, "")
            name = f"attachment-{n}{ext}"
        if len(data) > MAX_FILE_BYTES:
            skipped.append({"filename": name, "size": len(data),
                            "reason": f"Larger than {MAX_FILE_BYTES // 1048576} MB: open the email in Outlook"})
            continue
        files.append((name, ctype, data))
    return files, skipped