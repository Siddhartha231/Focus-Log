import os

import pytest

from focuslog import db


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    # never touch the real ~/.focuslog while testing
    monkeypatch.setenv("FOCUSLOG_HOME", str(tmp_path / "home"))


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "test.db")
    yield connection
    connection.close()


class FakeClock:
    """A clock the test controls. sleep() just moves time forward."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def server_app(tmp_path):
    """The sync server on a throwaway database.

    Uses SQLite by default. Set FOCUSLOG_TEST_DATABASE_URL to run the same
    tests against Postgres (CI does this).
    """
    from focuslog.server.app import create_app
    from focuslog.server.models import Base

    url = os.environ.get("FOCUSLOG_TEST_DATABASE_URL")
    if not url:
        yield create_app("sqlite:///" + str(tmp_path / "server.db"))
        return

    from sqlalchemy import create_engine

    from focuslog.server.settings import normalize_database_url
    engine = create_engine(normalize_database_url(url))
    Base.metadata.drop_all(engine)      # start every test from empty tables
    yield create_app(url)
    Base.metadata.drop_all(engine)
    engine.dispose()
