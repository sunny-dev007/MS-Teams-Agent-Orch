from pathlib import Path

from agent.services.code_guards import (
    config_change_is_safe,
    fix_basesettings_import,
    sanitize_file_changes,
)


def test_fix_basesettings_import_from_pydantic():
    broken = "from pydantic import BaseSettings\n\nclass Settings(BaseSettings):\n    x: str = ''\n"
    fixed = fix_basesettings_import(broken)
    assert "from pydantic import BaseSettings" not in fixed
    assert "from pydantic_settings import BaseSettings, SettingsConfigDict" in fixed


def test_fix_basesettings_keeps_other_pydantic_imports():
    mixed = "from pydantic import BaseSettings, SecretStr\n"
    fixed = fix_basesettings_import(mixed)
    assert "from pydantic_settings import BaseSettings, SettingsConfigDict" in fixed
    assert "from pydantic import SecretStr" in fixed
    assert "from pydantic import BaseSettings" not in fixed


def test_sanitize_rejects_stub_config(tmp_path: Path):
    cfg = tmp_path / "src" / "agent" / "config.py"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        "from pydantic_settings import BaseSettings, SettingsConfigDict\n"
        "class Settings(BaseSettings):\n"
        "    model_config = SettingsConfigDict(extra='ignore')\n"
        "    azure_openai_endpoint: str = ''\n"
        "    whatsapp_access_token: str = ''\n"
        "    database_url: str = ''\n"
        "    azdo_org_url: str = ''\n",
        encoding="utf-8",
    )
    stub = {
        "path": "src/agent/config.py",
        "action": "modify",
        "content": "from pydantic import BaseSettings\n\nclass Settings(BaseSettings):\n    VERSION: str = '0.2.0'\n",
    }
    result = sanitize_file_changes(tmp_path, [stub])
    assert result == []
    assert "VERSION" not in cfg.read_text(encoding="utf-8")


def test_sanitize_accepts_safe_config_edit(tmp_path: Path):
    cfg = tmp_path / "src" / "agent" / "config.py"
    cfg.parent.mkdir(parents=True)
    good = (
        "from pydantic import SecretStr\n"
        "from pydantic_settings import BaseSettings, SettingsConfigDict\n\n"
        "class Settings(BaseSettings):\n"
        "    model_config = SettingsConfigDict(extra='ignore')\n"
        "    azure_openai_endpoint: str = ''\n"
        "    whatsapp_access_token: SecretStr = SecretStr('')\n"
        "    database_url: str = ''\n"
        "    azdo_org_url: str = ''\n"
        "    new_flag: bool = True\n"
    )
    assert config_change_is_safe(good)
    result = sanitize_file_changes(
        tmp_path,
        [{"path": "src/agent/config.py", "action": "modify", "content": good}],
    )
    assert len(result) == 1
