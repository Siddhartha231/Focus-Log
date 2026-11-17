"""Two "devices" (two SQLite files) syncing through the real server app."""

import os
import threading
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from focuslog import cli, config, db, sync
from focuslog.server.app import create_app


class InProcessTransport:
    """Same interface as sync.HttpTransport, but calls the app directly."""

    def __init__(self, client):
        self.client = client
        self.calls = []

    def request(self, method, path, body=None, token=None):
        headers = {"Authorization": "Bearer " + token} if token else {}
        self.calls.append((method, path))
        response = self.client.request(method, path, json=body, headers=headers)
        return response.status_code, (response.json() if response.content else {})


@pytest.fixture
def server(server_app):
    with TestClient(server_app) as client:
        yield InProcessTransport(client)


@pytest.fixture
def token(server):
    sync.register(server, "sid@example.com", "correct horse")
    return sync.login(server, "sid@example.com", "correct horse", "test device")


@pytest.fixture
def laptop(tmp_path):
    conn = db.connect(tmp_path / "laptop.db")
    yield conn
    conn.close()


@pytest.fixture
def desktop(tmp_path):
    conn = db.connect(tmp_path / "desktop.db")
    yield conn
    conn.close()


def subjects(conn):
    rows = conn.execute("SELECT subject FROM sessions WHERE deleted = 0 ORDER BY started_at")
    return [r[0] for r in rows]


def test_sessions_travel_between_devices(server, token, laptop, desktop):
    db.add_session(laptop, "Math", datetime(2026, 9, 13, 9, 0), 1500, "completed")
    db.add_session(desktop, "OS", datetime(2026, 9, 13, 10, 0), 3000, "completed")

    assert sync.sync(laptop, server, token).pushed == 1
    result = sync.sync(desktop, server, token)
    assert (result.pushed, result.pulled) == (1, 1)
    assert sync.sync(laptop, server, token).pulled == 1

    assert subjects(laptop) == subjects(desktop) == ["Math", "OS"]
    assert sync.pending_count(laptop) == sync.pending_count(desktop) == 0


def test_syncing_again_changes_nothing(server, token, laptop):
    db.add_session(laptop, "Math", datetime(2026, 9, 13, 9, 0), 1500, "completed")
    sync.sync(laptop, server, token)
    result = sync.sync(laptop, server, token)
    assert (result.pushed, result.pulled) == (0, 0)
    assert subjects(laptop) == ["Math"]


def test_retry_after_lost_response_makes_no_duplicates(server, token, laptop, desktop):
    db.add_session(laptop, "Math", datetime(2026, 9, 13, 9, 0), 1500, "completed")
    sync.push(laptop, server, token)
    # pretend the response never arrived, so the laptop still thinks it's unsent
    laptop.execute("UPDATE sessions SET dirty = 1")
    sync.sync(laptop, server, token)

    sync.sync(desktop, server, token)
    assert subjects(desktop) == ["Math"]


def test_delete_reaches_the_other_device(server, token, laptop, desktop):
    session_id = db.add_session(laptop, "Math", datetime(2026, 9, 13, 9, 0), 1500, "completed")
    sync.sync(laptop, server, token)
    sync.sync(desktop, server, token)
    assert subjects(desktop) == ["Math"]

    db.delete_session(laptop, session_id)
    sync.sync(laptop, server, token)
    sync.sync(desktop, server, token)
    assert subjects(desktop) == []


def test_conflict_newer_change_wins_on_both_devices(server, token, laptop, desktop):
    session_id = db.add_session(laptop, "Math", datetime(2026, 9, 13, 9, 0), 1500, "completed")
    sync.sync(laptop, server, token)
    sync.sync(desktop, server, token)

    # both devices change the same session while offline; the desktop's is newer
    laptop.execute("UPDATE sessions SET subject = 'Laptop edit', dirty = 1, updated_at = ? "
                   "WHERE uuid = ?", (db.now_ms() + 1000, session_id))
    desktop.execute("UPDATE sessions SET subject = 'Desktop edit', dirty = 1, updated_at = ? "
                    "WHERE uuid = ?", (db.now_ms() + 2000, session_id))

    sync.sync(desktop, server, token)
    sync.sync(laptop, server, token)   # laptop's older edit is ignored, then it pulls the winner
    assert subjects(laptop) == subjects(desktop) == ["Desktop edit"]
    assert sync.pending_count(laptop) == 0


def test_large_history_goes_in_batches(server, token, laptop, desktop, monkeypatch):
    monkeypatch.setattr(sync, "PUSH_BATCH", 40)
    monkeypatch.setattr(sync, "PULL_PAGE", 30)
    for n in range(100):
        db.add_session(laptop, "Math", datetime(2026, 9, 1, 9, n % 60, n // 60), 60, "completed")
    assert sync.sync(laptop, server, token).pushed == 100
    assert sync.sync(desktop, server, token).pulled == 100
    pushes = [c for c in server.calls if c[1] == "/v1/sync/push"]
    assert len(pushes) == 3            # 40 + 40 + 20


def test_row_changed_during_upload_stays_dirty(server, token, laptop):
    session_id = db.add_session(laptop, "Math", datetime(2026, 9, 13, 9, 0), 1500, "completed")

    class EditsDuringPush(InProcessTransport):
        def request(self, method, path, body=None, token=None):
            result = super().request(method, path, body, token)
            if path == "/v1/sync/push" and not self.edited:
                self.edited = True
                db.delete_session(laptop, session_id)   # user deletes it mid-request
            return result

    transport = EditsDuringPush(server.client)
    transport.edited = False
    sync.push(laptop, transport, token)
    assert sync.pending_count(laptop) == 0          # second loop pass sent the delete too
    assert len([c for c in transport.calls if c[1] == "/v1/sync/push"]) == 2


def test_switching_accounts_reuploads_everything(server, token, laptop):
    db.add_session(laptop, "Math", datetime(2026, 9, 13, 9, 0), 1500, "completed")
    sync.bind_account(laptop, "sid@example.com@server")
    sync.sync(laptop, server, token)
    assert sync.pending_count(laptop) == 0

    sync.bind_account(laptop, "sid@example.com@server")     # same account: nothing happens
    assert sync.pending_count(laptop) == 0
    sync.bind_account(laptop, "new@example.com@server")     # different account: start over
    assert sync.pending_count(laptop) == 1
    assert db.get_state(laptop, "cursor") == "0"


def test_expired_login_gives_a_clear_error(server, laptop):
    with pytest.raises(sync.SyncError, match="focuslog login"):
        sync.sync(laptop, server, "not-a-real-token")


def test_unreachable_server_gives_a_clear_error(laptop):
    transport = sync.HttpTransport("http://127.0.0.1:9", timeout=1)   # nothing listens there
    with pytest.raises(sync.SyncError, match="can't reach"):
        sync.pull(laptop, transport, "token")


def test_cli_register_sync_logout(server, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("getpass.getpass", lambda prompt: "correct horse")
    factory = lambda url: server  # noqa: E731
    args = cli.build_parser().parse_args(
        ["register", "--server", "focus.example.com", "--email", "Sid@Example.com"])

    conn = db.connect(tmp_path / "cli.db")
    assert cli.register_command(conn, args, transport_factory=factory) == 0
    saved = config.load()
    assert saved["server_url"] == "https://focus.example.com"   # https added by default
    assert saved["email"] == "sid@example.com"
    if os.name != "nt":   # Windows doesn't have these permission bits
        assert oct(config.config_path().stat().st_mode & 0o777) == "0o600"

    db.add_session(conn, "Math", datetime(2026, 9, 13, 9, 0), 1500, "completed")
    assert cli.sync_command(conn, None, transport_factory=factory) == 0
    assert "Synced: 1 uploaded, 0 downloaded." in capsys.readouterr().out

    assert cli.logout_command(conn, None, transport_factory=factory) == 0
    assert config.load() == {}
    conn.close()


def test_real_http_transport_against_a_live_server(tmp_path, laptop):
    """One end-to-end check over a real socket, with uvicorn in a thread."""
    import socket
    import time

    import uvicorn

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    app = create_app("sqlite:///" + str(tmp_path / "live.db"))
    live = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=live.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 10
        while not live.started and time.time() < deadline:
            time.sleep(0.05)
        transport = sync.HttpTransport("http://127.0.0.1:%d" % port)
        sync.register(transport, "sid@example.com", "correct horse")
        with pytest.raises(sync.SyncError, match="409"):
            sync.register(transport, "sid@example.com", "correct horse")
        live_token = sync.login(transport, "sid@example.com", "correct horse", "laptop")

        db.add_session(laptop, "Math", datetime(2026, 9, 13, 9, 0), 1500, "completed")
        result = sync.sync(laptop, transport, live_token)
        assert (result.pushed, result.cursor) == (1, 1)
        sync.logout(transport, live_token)
        with pytest.raises(sync.SyncError, match="focuslog login"):
            sync.sync(laptop, transport, live_token)
    finally:
        live.should_exit = True
        thread.join(timeout=10)
