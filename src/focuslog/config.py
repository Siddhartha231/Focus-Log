"""Remembers which sync server you're logged in to (~/.focuslog/config.json)."""

import json
import os

from focuslog.db import home_dir


def config_path():
    return home_dir() / "config.json"


def load() -> dict:
    try:
        with open(config_path(), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save(data: dict) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # the file holds a login token, so create it readable only by the owner
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def clear() -> None:
    try:
        config_path().unlink()
    except FileNotFoundError:
        pass
