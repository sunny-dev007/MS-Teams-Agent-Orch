"""Hard guards so agent codegen cannot break CI with pydantic v1 imports."""

from __future__ import annotations

import re
from pathlib import Path

from agent.core.logging import get_logger

logger = get_logger(__name__)

_BASESETTINGS_FROM_PYDANTIC = re.compile(
    r"^from\s+pydantic\s+import\s+(.+)$",
    re.MULTILINE,
)
_BASESETTINGS_FROM_SETTINGS = re.compile(
    r"^from\s+pydantic_settings\s+import\s+.+$",
    re.MULTILINE,
)

# Minimal Settings surface — agent must not wipe these when rewriting config.py
_CONFIG_REQUIRED = (
    "azure_openai_endpoint",
    "whatsapp_access_token",
    "database_url",
    "azdo_org_url",
    "SettingsConfigDict",
)

_PORTAL_REQUIRED = (
    "APIRouter",
    "FileResponse",
    "portal.html",
)

# Agent portal redesigns must never gut the FastAPI entrypoint (causes webhook 404s).
_MAIN_REQUIRED = (
    "whatsapp_router",
    "gmail_router",
    "health_router",
    "portal_router",
    "include_router",
    "lifespan",
)


_FORBIDDEN_IMPORT = "from " + "pydantic" + " import " + "BaseSettings"


def fix_basesettings_import(content: str) -> str:
    """Rewrite pydantic v1 BaseSettings import to pydantic-settings (Pydantic v2)."""
    if "BaseSettings" not in content:
        return content

    has_good = bool(
        re.search(
            r"from\s+pydantic_settings\s+import\s+[^\n]*\bBaseSettings\b",
            content,
        )
    )
    has_bad = bool(
        re.search(r"from\s+pydantic\s+import\s+[^\n]*\bBaseSettings\b", content)
    )
    if has_good and not has_bad:
        return content

    lines = content.splitlines(keepends=True)
    out: list[str] = []
    inserted = has_good
    for line in lines:
        stripped = line.strip()
        match = re.match(r"from\s+pydantic\s+import\s+(.+)$", stripped)
        if match:
            parts = [p.strip() for p in match.group(1).split(",") if p.strip()]
            if "BaseSettings" in parts:
                parts = [p for p in parts if p != "BaseSettings"]
                if not inserted:
                    out.append(
                        "from pydantic_settings import BaseSettings, SettingsConfigDict\n"
                    )
                    inserted = True
                if parts:
                    out.append(f"from pydantic import {', '.join(parts)}\n")
                continue
        out.append(line)

    if not inserted:
        out.insert(0, "from pydantic_settings import BaseSettings, SettingsConfigDict\n")

    return "".join(out)


def is_config_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    return normalized.endswith("agent/config.py") or normalized == "src/agent/config.py"


def is_portal_api_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    return normalized.endswith("agent/api/portal.py") or normalized == "src/agent/api/portal.py"


def is_main_app_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    return normalized.endswith("agent/main.py") or normalized == "src/agent/main.py"


def main_change_is_safe(content: str) -> bool:
    if "from src.agent" in content:
        return False
    return all(marker in content for marker in _MAIN_REQUIRED)


def is_legacy_web_portal_path(path: str) -> bool:
    """Agent often invents src/web/portal.html — real portal is src/agent/web/."""
    normalized = path.replace("\\", "/").lstrip("./")
    return normalized.startswith("src/web/") or normalized.startswith("web/portal")


def config_change_is_safe(content: str) -> bool:
    if _FORBIDDEN_IMPORT in content:
        return False
    if "from pydantic_settings import" not in content or "BaseSettings" not in content:
        return False
    return all(marker in content for marker in _CONFIG_REQUIRED)


def portal_change_is_safe(content: str) -> bool:
    if "from src.agent.config import" in content:
        return False
    if "Jinja2Templates" in content:
        return False
    return all(marker in content for marker in _PORTAL_REQUIRED)


def sanitize_file_changes(
    repo_dir: Path, file_changes: list[dict]
) -> list[dict]:
    """Fix / drop destructive agent edits before they hit disk or CI."""
    safe: list[dict] = []
    for change in file_changes:
        path = str(change.get("path") or "")
        action = change.get("action", "modify")
        content = change.get("content")

        if is_config_path(path) and action == "delete":
            logger.warning("Refusing to delete %s (agent guard)", path)
            continue

        if is_portal_api_path(path) and action == "delete":
            logger.warning("Refusing to delete %s (agent guard)", path)
            continue

        if isinstance(content, str) and "BaseSettings" in content:
            fixed = fix_basesettings_import(content)
            if fixed != content:
                logger.warning("Auto-fixed BaseSettings import in %s", path)
                change = {**change, "content": fixed}
                content = fixed

        if is_legacy_web_portal_path(path) and action in ("modify", "create"):
            remapped = path.replace("\\", "/").replace("src/web/", "src/agent/web/", 1)
            if remapped.startswith("web/"):
                remapped = "src/agent/" + remapped
            logger.warning("Remapping agent portal path %s → %s", path, remapped)
            change = {**change, "path": remapped}
            path = remapped

        if is_config_path(path) and action in ("modify", "create"):
            if not isinstance(content, str) or not config_change_is_safe(content):
                existing = repo_dir / path
                if existing.is_file():
                    logger.warning(
                        "Rejecting unsafe rewrite of %s — keeping existing file",
                        path,
                    )
                    continue
                logger.warning("Rejecting unsafe create of %s", path)
                continue

        if is_portal_api_path(path) and action in ("modify", "create"):
            if not isinstance(content, str) or not portal_change_is_safe(content):
                existing = repo_dir / path
                if existing.is_file():
                    logger.warning(
                        "Rejecting unsafe rewrite of %s — keeping existing file",
                        path,
                    )
                    continue
                logger.warning("Rejecting unsafe create of %s", path)
                continue

        if is_main_app_path(path) and action == "delete":
            logger.warning("Refusing to delete %s (agent guard)", path)
            continue

        if is_main_app_path(path) and action in ("modify", "create"):
            if not isinstance(content, str) or not main_change_is_safe(content):
                existing = repo_dir / path
                if existing.is_file():
                    logger.warning(
                        "Rejecting unsafe rewrite of %s — keeping existing entrypoint",
                        path,
                    )
                    continue
                logger.warning("Rejecting unsafe create of %s", path)
                continue

        safe.append(change)
    return safe
