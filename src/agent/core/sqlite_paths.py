"""SQLite path helpers — Azure App Service + local cwd must never fail open."""

from __future__ import annotations

from pathlib import Path


def sqlite_file_from_url(database_url: str) -> Path:
    """Parse aiosqlite/sqlite URL into a filesystem path.

    Handles both absolute forms used on Azure:
      sqlite+aiosqlite:////home/site/data/agent.db  → /home/site/data/agent.db
      sqlite+aiosqlite:///home/site/data/agent.db   → /home/site/data/agent.db (normalize)
    and relative local forms:
      sqlite+aiosqlite:///./agent.db                → ./agent.db
    """
    raw = (database_url or "").strip()
    rest = raw
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if raw.startswith(prefix):
            rest = raw[len(prefix) :]
            break
    else:
        rest = "./agent.db"

    if rest.startswith("/"):
        return Path(rest)
    # Mis-set absolute path without leading slash (3-slash URL + /home stripped wrong)
    if rest.startswith("home/"):
        return Path("/") / rest
    return Path(rest)


def ensure_sqlite_parent(path: Path) -> Path:
    """Create parent directory; fall back to ./agent.db if Azure path is unavailable."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        fallback = Path("agent.db").resolve()
        fallback.parent.mkdir(parents=True, exist_ok=True)
        return fallback


def ensure_sqlite_file(database_url: str) -> Path:
    return ensure_sqlite_parent(sqlite_file_from_url(database_url))


def to_aiosqlite_url(path: Path) -> str:
    """Build a SQLAlchemy aiosqlite URL (4 slashes for absolute paths)."""
    posix = path.as_posix()
    if path.is_absolute():
        return f"sqlite+aiosqlite:///{posix}"
    return f"sqlite+aiosqlite:///{posix}"
