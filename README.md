# Pre-Arrival Email Reader

Runs every hour, on the hour (Singapore time), for all three hotels:

1. **Sends** the pre-arrival email to guests arriving **today + the next 3 days** who haven't had it yet.
   On 02 Nov that's guests arriving 02, 03, 04 and 05 Nov.
   - "To send" guests are emailed. "Failed" ones are retried, up to 3 attempts in total.
   - Guests already emailed are never emailed again.
2. **Reads the hotel inbox** and moves guests who replied into the app's **Replied** section,
   with the first lines of their reply saved. Every other email is ignored.
   - The inbox is opened **read-only**, so nothing is marked as read, moved or deleted.
   - Only emails that arrive after the service first connects are looked at.
   - A reply counts when the sender's address belongs to a guest of that hotel who was sent the
     email, hasn't replied yet, and is still arriving or staying. Out-of-office auto-replies don't count.

The service doesn't touch the database. It works through the Pre-Arrival backend API, so the hotel
templates, Bonvoy line, "never send twice" check and history all work exactly as in the app.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt     # Windows: .venv\Scripts\pip install -r requirements.txt
cp ..env.example ..env                          # then fill it in
```

In `.env` you need:

- `PREARRIVAL_API_URL`: where the backend is (`http://127.0.0.1:1802` on the same server).
- A login for the API: each hotel's staff login (`TVC_API_USERNAME` / `TSH_...` / `MR_...`),
  or one admin login (`PREARRIVAL_API_USERNAME`).
- Each hotel mailbox: the same email and password as `SMTPn_*` in the backend `.env`, plus the
  IMAP server.

## Run

```bash
.venv/bin/python main.py --once --dry-run   # shows who would be emailed; sends and changes nothing
.venv/bin/python main.py --once             # one real run
.venv/bin/python main.py                    # runs now, then every hour on the hour
```

Output goes to the screen and to `reader.log`. The last email read in each inbox is remembered in
`reader_state.json`; delete that file to start watching each inbox again from "now".

## Run it permanently on the server (systemd)

Edit the paths and user in `prearrival-reader.service` if yours differ, then:

```bash
sudo cp prearrival-reader.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now prearrival-reader
sudo systemctl status prearrival-reader      # should say "active (running)"
journalctl -u prearrival-reader -f           # live log (Ctrl+C to leave)
```

The service starts with the server and restarts itself if it ever stops.
To pause sending for one hotel, switch its template off on the app's Email template page.
