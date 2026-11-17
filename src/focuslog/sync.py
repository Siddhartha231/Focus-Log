"""Client side of sync: upload local changes, then download everyone else's.

How it works (the longer version is in docs/SYNC.md):

* Every session has a uuid made on the device, so uploading the same session
  twice can never create a duplicate. That makes retries safe.
* Rows changed locally are marked dirty = 1. Push sends the dirty rows.
* The server gives every change it accepts a growing number (seq). Pull asks
  for "everything after the last seq I saw" - that number is the cursor.
* If two devices changed the same session, the newer updated_at wins.
"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from focuslog import db

PUSH_BATCH = 200
PULL_PAGE = 500


class SyncError(Exception):
    """Anything that stops a sync, with a message that is safe to show the user."""


class HttpTransport:
    """Talks JSON to the server using only the standard library."""

    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(self, method: str, path: str, body=None, token=None):
        """Returns (status_code, parsed_json)."""
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = "Bearer " + token

        req = urllib.request.Request(
            self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, _parse(resp.read())
        except urllib.error.HTTPError as err:
            # 4xx/5xx still carry a JSON body with the reason
            return err.code, _parse(err.read())
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            reason = getattr(err, "reason", err)
            raise SyncError("can't reach %s (%s)" % (self.base_url, reason)) from err


def _parse(raw: bytes):
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        return {}


def _check(status: int, payload, action: str):
    if 200 <= status < 300:
        return
    if status == 401:
        raise SyncError("not logged in, or the login expired. Run: focuslog login")
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, list):  # validation errors come back as a list
        detail = "; ".join(str(item.get("msg", item)) for item in detail)
    raise SyncError("%s failed (%d): %s" % (action, status, detail or "unknown error"))


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

def register(transport, email: str, password: str) -> None:
    status, payload = transport.request(
        "POST", "/v1/auth/register", {"email": email, "password": password})
    _check(status, payload, "register")


def login(transport, email: str, password: str, device_name: str) -> str:
    status, payload = transport.request(
        "POST", "/v1/auth/login",
        {"email": email, "password": password, "device_name": device_name})
    _check(status, payload, "login")
    return payload["token"]


def logout(transport, token: str) -> None:
    status, payload = transport.request("POST", "/v1/auth/logout", token=token)
    _check(status, payload, "logout")


def bind_account(conn, account: str) -> None:
    """Remember which account this database syncs with.

    If it changes (different server or user), start over: forget the cursor
    and mark every row dirty so the new account receives the full history.
    """
    if db.get_state(conn, "account") == account:
        return
    conn.execute("BEGIN")
    try:
        conn.execute("UPDATE sessions SET dirty = 1")
        db.set_state(conn, "cursor", 0)
        db.set_state(conn, "account", account)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


# ---------------------------------------------------------------------------
# Push and pull
# ---------------------------------------------------------------------------

@dataclass
class SyncResult:
    pushed: int = 0
    pulled: int = 0
    cursor: int = 0


def pending_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM sessions WHERE dirty = 1").fetchone()[0]


def push(conn, transport, token: str) -> int:
    """Upload every dirty row. Returns how many were sent."""
    sent = 0
    while True:
        rows = conn.execute(
            """SELECT uuid, subject, started_at, duration_seconds, status, updated_at, deleted
               FROM sessions WHERE dirty = 1 ORDER BY id LIMIT ?""",
            (PUSH_BATCH,),
        ).fetchall()
        if not rows:
            return sent

        body = {"sessions": [
            {
                "id": r["uuid"],
                "subject": r["subject"],
                "started_at": r["started_at"],
                "duration_seconds": r["duration_seconds"],
                "status": r["status"],
                "updated_at": r["updated_at"],
                "deleted": bool(r["deleted"]),
            }
            for r in rows
        ]}
        status, payload = transport.request("POST", "/v1/sync/push", body, token)
        _check(status, payload, "push")

        # Only clear the flag if the row hasn't changed since we read it.
        # (If it did change, it stays dirty and goes up next time.)
        conn.execute("BEGIN")
        for r in rows:
            conn.execute(
                "UPDATE sessions SET dirty = 0 WHERE uuid = ? AND updated_at = ?",
                (r["uuid"], r["updated_at"]),
            )
        conn.execute("COMMIT")
        sent += len(rows)


def pull(conn, transport, token: str) -> int:
    """Download changes made after our cursor. Returns how many rows changed here."""
    changed = 0
    while True:
        cursor = int(db.get_state(conn, "cursor", 0))
        status, payload = transport.request(
            "GET", "/v1/sync/pull?since=%d&limit=%d" % (cursor, PULL_PAGE), token=token)
        _check(status, payload, "pull")

        # apply the page and move the cursor in one transaction, so a crash
        # halfway can't leave the cursor ahead of the data
        conn.execute("BEGIN")
        try:
            for s in payload["sessions"]:
                # last write wins: the incoming row only replaces ours if it is newer
                cur = conn.execute(
                    """INSERT INTO sessions
                           (uuid, subject, started_at, duration_seconds, status,
                            updated_at, deleted, dirty)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                       ON CONFLICT(uuid) DO UPDATE SET
                           subject = excluded.subject,
                           started_at = excluded.started_at,
                           duration_seconds = excluded.duration_seconds,
                           status = excluded.status,
                           updated_at = excluded.updated_at,
                           deleted = excluded.deleted,
                           dirty = 0
                       WHERE excluded.updated_at > sessions.updated_at""",
                    (s["id"], s["subject"], s["started_at"], s["duration_seconds"],
                     s["status"], s["updated_at"], int(s["deleted"])),
                )
                changed += cur.rowcount
            db.set_state(conn, "cursor", payload["cursor"])
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        if not payload.get("has_more"):
            return changed


def sync(conn, transport, token: str) -> SyncResult:
    pushed = push(conn, transport, token)
    pulled = pull(conn, transport, token)
    return SyncResult(pushed, pulled, int(db.get_state(conn, "cursor", 0)))
