"""
Talks to the Pre-Arrival backend API. The backend does the real work (picking the
hotel template, filling in the Bonvoy line, sending from the hotel's mailbox, never
sending twice, writing the history), so this service never touches the database.
"""
import json

import requests

from settings import API_TIMEOUT_SECONDS, API_URL

PREFIX = "/api/prearrival"


class ApiError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status          # HTTP status code, or None if the backend couldn't be reached


class PrearrivalApi:
    def __init__(self, username, password, base_url=API_URL, timeout=API_TIMEOUT_SECONDS):
        self.base_url = base_url
        self.username = username
        self.password = password
        self.timeout = timeout
        self.session = requests.Session()
        self.token = None

    # ------------------------------------------------------------ plumbing
    def login(self):
        try:
            r = self.session.post(f"{self.base_url}/auth/login",
                                  data={"username": self.username, "password": self.password},
                                  timeout=self.timeout)
        except requests.RequestException as e:
            raise ApiError(f"Can't reach the backend at {self.base_url}: {e}") from e
        if r.status_code == 401:
            raise ApiError(f"Login '{self.username}' was rejected. Check the username and password in .env", 401)
        if not r.ok:
            raise ApiError(f"Login failed ({r.status_code}): {r.text[:200]}", r.status_code)
        self.token = r.json()["access_token"]

    def _call(self, method, path, retry_login=True, **kwargs):
        if not self.token:
            self.login()
        try:
            r = self.session.request(method, f"{self.base_url}{path}",
                                     headers={"Authorization": f"Bearer {self.token}"},
                                     timeout=self.timeout, **kwargs)
        except requests.RequestException as e:
            raise ApiError(f"Can't reach the backend at {self.base_url}: {e}") from e
        if r.status_code == 401 and retry_login:   # token expired: log in again once
            self.token = None
            return self._call(method, path, retry_login=False, **kwargs)
        if not r.ok:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = r.text[:200]
            raise ApiError(f"{method} {path} failed ({r.status_code}): {detail}", r.status_code)
        return r.json()

    # ------------------------------------------------------------ endpoints used
    def template(self, hotel_code):
        return self._call("GET", f"{PREFIX}/templates/{hotel_code}")

    def arrivals(self, **params):
        params = {k: v for k, v in params.items() if v is not None}
        return self._call("GET", f"{PREFIX}/arrivals", params=params)

    def send_email(self, arrival_id):
        """Backend sends the hotel template; returns {"status": sent|failed|skipped, ...}."""
        return self._call("POST", f"{PREFIX}/arrivals/{arrival_id}/send-email")

    def mark_reply(self, arrival_id, notes=None):
        return self._call("POST", f"{PREFIX}/arrivals/{arrival_id}/reply", json={"notes": notes})

    def match_messages(self, hotel_code, items):
        """Which emails belong to a guest conversation: {key: {"arrival_id", "known"}}"""
        return self._call("POST", f"{PREFIX}/mailbox/{hotel_code}/match", json={"items": items})

    def store_message(self, hotel_code, meta, files):
        """Adds one email (and its attachments) to the guest's conversation."""
        return self._call("POST", f"{PREFIX}/mailbox/{hotel_code}/messages",
                          data={"meta": json.dumps(meta)},
                          files=[("files", (name, data, ctype)) for name, ctype, data in files] or None)