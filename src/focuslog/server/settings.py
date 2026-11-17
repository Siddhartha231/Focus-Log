"""Server settings, read from environment variables."""

import os


def database_url() -> str:
    url = os.environ.get("DATABASE_URL", "sqlite:///./focuslog-server.db")
    return normalize_database_url(url)


def normalize_database_url(url: str) -> str:
    # Heroku and some other hosts hand out "postgres://..." URLs. SQLAlchemy
    # wants the driver spelled out, and we use psycopg 3.
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def registration_open() -> bool:
    # set FOCUSLOG_ALLOW_REGISTRATION=0 once your own account exists
    return os.environ.get("FOCUSLOG_ALLOW_REGISTRATION", "1") not in ("0", "false", "no")


def sentry_dsn():
    return os.environ.get("SENTRY_DSN") or None
