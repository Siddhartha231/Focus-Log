import pytest
from fastapi.testclient import TestClient

from focuslog.server import security, settings


@pytest.fixture
def client(server_app):
    with TestClient(server_app) as test_client:
        yield test_client


def signup(client, email="sid@example.com", password="correct horse"):
    assert client.post("/v1/auth/register",
                       json={"email": email, "password": password}).status_code == 201
    token = client.post("/v1/auth/login",
                        json={"email": email, "password": password}).json()["token"]
    return {"Authorization": "Bearer " + token}


def session(n=1, **changes):
    data = {
        "id": "00000000-0000-4000-8000-%012d" % n,
        "subject": "Math",
        "started_at": "2026-09-13T10:00:00",
        "duration_seconds": 1500,
        "status": "completed",
        "updated_at": 1000,
        "deleted": False,
    }
    data.update(changes)
    return data


def test_health(client):
    assert client.get("/healthz").json()["status"] == "ok"


# --- accounts ---

def test_password_hash_roundtrip():
    stored = security.hash_password("correct horse")
    assert "correct horse" not in stored
    assert security.verify_password("correct horse", stored)
    assert not security.verify_password("wrong", stored)
    assert not security.verify_password("anything", "not-a-real-hash")


def test_register_rules(client):
    assert client.post("/v1/auth/register",
                       json={"email": "nope", "password": "longenough"}).status_code == 422
    assert client.post("/v1/auth/register",
                       json={"email": "a@b.co", "password": "short"}).status_code == 422
    signup(client, "Sid@Example.com")
    # same address in different capitals is the same account
    again = client.post("/v1/auth/register",
                        json={"email": "sid@example.com", "password": "longenough"})
    assert again.status_code == 409


def test_registration_can_be_closed(client, monkeypatch):
    monkeypatch.setenv("FOCUSLOG_ALLOW_REGISTRATION", "0")
    assert client.post("/v1/auth/register",
                       json={"email": "a@b.co", "password": "longenough"}).status_code == 403


def test_login_failures_look_the_same(client):
    signup(client)
    wrong_password = client.post("/v1/auth/login",
                                 json={"email": "sid@example.com", "password": "wrong-password"})
    no_such_user = client.post("/v1/auth/login",
                               json={"email": "ghost@example.com", "password": "wrong-password"})
    assert wrong_password.status_code == no_such_user.status_code == 401
    assert wrong_password.json() == no_such_user.json()


def test_sync_needs_a_valid_token(client):
    assert client.get("/v1/sync/pull").status_code == 401
    assert client.get("/v1/sync/pull",
                      headers={"Authorization": "Bearer made-up"}).status_code == 401
    assert client.post("/v1/sync/push", json={"sessions": []}).status_code == 401


def test_logout_revokes_the_token(client):
    auth = signup(client)
    assert client.post("/v1/auth/logout", headers=auth).status_code == 204
    assert client.get("/v1/sync/pull", headers=auth).status_code == 401


# --- sync ---

def test_push_then_pull(client):
    auth = signup(client)
    pushed = client.post("/v1/sync/push", headers=auth,
                         json={"sessions": [session(1), session(2)]}).json()
    assert pushed == {"accepted": 2, "ignored": 0, "cursor": 2}

    pulled = client.get("/v1/sync/pull", headers=auth).json()
    assert [s["id"] for s in pulled["sessions"]] == [session(1)["id"], session(2)["id"]]
    assert pulled["cursor"] == 2 and pulled["has_more"] is False

    # nothing new after the cursor
    assert client.get("/v1/sync/pull?since=2", headers=auth).json()["sessions"] == []


def test_pushing_twice_is_harmless(client):
    auth = signup(client)
    body = {"sessions": [session(1)]}
    client.post("/v1/sync/push", headers=auth, json=body)
    again = client.post("/v1/sync/push", headers=auth, json=body).json()
    assert again == {"accepted": 0, "ignored": 1, "cursor": 1}
    assert len(client.get("/v1/sync/pull", headers=auth).json()["sessions"]) == 1


def test_last_write_wins(client):
    auth = signup(client)
    client.post("/v1/sync/push", headers=auth, json={"sessions": [session(1, updated_at=2000)]})

    older = session(1, subject="Stale edit", updated_at=1500)
    assert client.post("/v1/sync/push", headers=auth,
                       json={"sessions": [older]}).json()["ignored"] == 1

    newer = session(1, deleted=True, updated_at=3000)
    assert client.post("/v1/sync/push", headers=auth,
                       json={"sessions": [newer]}).json()["accepted"] == 1

    row = client.get("/v1/sync/pull", headers=auth).json()["sessions"][0]
    assert row["subject"] == "Math" and row["deleted"] is True
    # the change moved to the end of the feed so other devices see it
    after_first = client.get("/v1/sync/pull?since=1", headers=auth).json()["sessions"]
    assert after_first[0]["deleted"] is True


def test_pull_pages(client):
    auth = signup(client)
    client.post("/v1/sync/push", headers=auth,
                json={"sessions": [session(n) for n in range(1, 6)]})
    first = client.get("/v1/sync/pull?limit=2", headers=auth).json()
    assert len(first["sessions"]) == 2 and first["has_more"] is True
    rest = client.get("/v1/sync/pull?limit=10&since=%d" % first["cursor"], headers=auth).json()
    assert len(rest["sessions"]) == 3 and rest["has_more"] is False


def test_users_cannot_see_each_other(client):
    sid = signup(client, "sid@example.com")
    other = signup(client, "other@example.com")
    client.post("/v1/sync/push", headers=sid, json={"sessions": [session(1)]})
    assert client.get("/v1/sync/pull", headers=other).json()["sessions"] == []
    # the same uuid under another account is a separate row, not an overwrite
    client.post("/v1/sync/push", headers=other,
                json={"sessions": [session(1, subject="Theirs", updated_at=9999)]})
    assert client.get("/v1/sync/pull", headers=sid).json()["sessions"][0]["subject"] == "Math"


@pytest.mark.parametrize("bad", [
    {"id": "not-a-uuid"},
    {"started_at": "yesterday"},
    {"duration_seconds": -5},
    {"duration_seconds": 10 ** 7},
    {"subject": ""},
])
def test_push_rejects_bad_rows(client, bad):
    auth = signup(client)
    response = client.post("/v1/sync/push", headers=auth, json={"sessions": [session(1, **bad)]})
    assert response.status_code == 422


def test_push_size_limit(client):
    auth = signup(client)
    too_many = [session(n) for n in range(501)]
    assert client.post("/v1/sync/push", headers=auth,
                       json={"sessions": too_many}).status_code == 422


def test_two_devices_pushing_at_once_get_distinct_seq_numbers(client, server_app):
    """The row lock in push() only does something on Postgres, so only test it there."""
    import os
    from concurrent.futures import ThreadPoolExecutor

    if not os.environ.get("FOCUSLOG_TEST_DATABASE_URL"):
        pytest.skip("needs Postgres (set FOCUSLOG_TEST_DATABASE_URL)")

    auth = signup(client)

    def push_batch(worker):
        rows = [session(worker * 100 + n) for n in range(10)]
        return client.post("/v1/sync/push", headers=auth, json={"sessions": rows}).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert set(pool.map(push_batch, range(1, 9))) == {200}

    # 80 changes must have used seq 1..80 exactly once each: no duplicates, no gaps
    seen = []
    cursor = 0
    while True:
        page = client.get("/v1/sync/pull?since=%d&limit=25" % cursor, headers=auth).json()
        seen += [s["id"] for s in page["sessions"]]
        cursor = page["cursor"]
        if not page["has_more"]:
            break
    assert len(seen) == len(set(seen)) == 80
    assert cursor == 80


def test_database_url_normalizing():
    fix = settings.normalize_database_url
    assert fix("postgres://u:p@host/db") == "postgresql+psycopg://u:p@host/db"
    assert fix("postgresql://u:p@host/db") == "postgresql+psycopg://u:p@host/db"
    assert fix("sqlite:///x.db") == "sqlite:///x.db"
