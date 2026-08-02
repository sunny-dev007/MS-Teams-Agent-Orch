from pathlib import Path

from agent.core.sqlite_paths import (
    ensure_sqlite_file,
    sqlite_file_from_url,
    to_aiosqlite_url,
)


def test_sqlite_url_four_slash_absolute():
    path = sqlite_file_from_url("sqlite+aiosqlite:////home/site/data/agent.db")
    assert path == Path("/home/site/data/agent.db")


def test_sqlite_url_three_slash_home_normalized():
    path = sqlite_file_from_url("sqlite+aiosqlite:///home/site/data/agent.db")
    assert path == Path("/home/site/data/agent.db")


def test_sqlite_url_relative():
    path = sqlite_file_from_url("sqlite+aiosqlite:///./agent.db")
    assert path == Path("./agent.db")


def test_to_aiosqlite_url_absolute_has_four_slashes():
    url = to_aiosqlite_url(Path("/home/site/data/agent.db"))
    assert url.startswith("sqlite+aiosqlite:////home/")


def test_ensure_sqlite_file_creates_parent(tmp_path, monkeypatch):
    target = tmp_path / "nested" / "agent.db"
    url = to_aiosqlite_url(target)
    got = ensure_sqlite_file(url)
    assert got.parent.exists()
    assert got == target
