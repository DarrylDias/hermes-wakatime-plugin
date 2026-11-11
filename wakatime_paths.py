"""Discovery of wakatime-cli, config files, credentials and CLI state.

Everything here is read-only: the plugin never writes ~/.wakatime.cfg, never prints secret
values, and never starts or installs a CLI. `reset-backoff` is deliberately exposed only as
an explicit user-invoked CLI command that backs up the internal config first.
"""

from __future__ import annotations

import configparser
import datetime as dt
import os
import re
import shutil
from pathlib import Path
from typing import Any, Optional

PLUGIN_NAME = "wakatime"
PLUGIN_VERSION = "0.2.0"
EDITOR_NAME = "hermes"
EDITOR_LEVEL_VERSION = "1.0.0"

_CLI_NAMES = (
    "wakatime-cli.exe",
    "wakatime-cli-windows-amd64.exe",
    "wakatime-cli-windows-amd64",
    "wakatime-cli",
    "wakatime-cli.bin",
)


def wakatime_home() -> Path:
    """The CLI's base home: $WAKATIME_HOME when set, else the user's home."""
    env = os.environ.get("WAKATIME_HOME")
    return Path(env).expanduser() if env else Path.home()


def _cli_candidates(explicit: Optional[str] = None):
    if explicit:
        yield Path(explicit).expanduser()
    env = os.environ.get("WAKATIME_CLI")
    if env:
        yield Path(env).expanduser()
    home = wakatime_home()
    base = home / ".wakatime"
    for name in _CLI_NAMES:
        yield base / name
        yield home / name
    for name in ("wakatime-cli", "wakatime", "wakatime.exe"):
        found = shutil.which(name)
        if found:
            yield Path(found)


def find_cli(explicit: Optional[str] = None) -> Optional[Path]:
    """First existing wakatime-cli candidate. Explicit path/env first, then ~/.wakatime, PATH."""
    seen = set()
    for candidate in _cli_candidates(explicit):
        try:
            resolved = candidate.resolve() if candidate.exists() else candidate
        except OSError:
            resolved = candidate
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        if resolved.is_file():
            return resolved
    return None


def config_candidates():
    """Config locations in the order wakatime-cli resolves them."""
    env_home = os.environ.get("WAKATIME_HOME")
    home = wakatime_home()
    if env_home:
        yield home / ".wakatime.cfg"
    yield Path.home() / ".wakatime.cfg"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        yield Path(xdg) / "wakatime" / "wakatime.cfg"
    yield Path.home() / ".config" / "wakatime" / "wakatime.cfg"


def find_config() -> Optional[Path]:
    for candidate in config_candidates():
        if candidate.is_file():
            return candidate
    return None


def internal_config_path() -> Path:
    return wakatime_home() / ".wakatime" / "wakatime-internal.cfg"


def offline_db_path() -> Path:
    return wakatime_home() / ".wakatime" / "offline_heartbeats.bdb"


def read_cfg_value(path: Optional[Path], key: str, section: str = "settings") -> Optional[str]:
    """Read a [settings] key without depending on configparser quirks in the CLI."""
    if not path:
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    parser = configparser.ConfigParser()
    try:
        parser.read_string(text)
        if parser.has_option(section, key):
            value = parser.get(section, key, fallback="")
            return value.strip() or None
    except configparser.Error:
        pass
    # Fallback: tolerant line scan (api_key = waka_... in [settings])
    in_section = section.lower() == "settings"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_section = stripped[1:-1].strip().lower() == section.lower()
            continue
        if in_section and "=" in stripped:
            left, _, right = stripped.partition("=")
            if left.strip().lower() == key.lower():
                value = right.strip()
                if value:
                    return value
    return None


def api_key_config() -> Optional[str]:
    return read_cfg_value(find_config(), "api_key")


def api_key_source() -> Optional[str]:
    """Where the API key comes from: env or config filename, or None."""
    if os.environ.get("WAKATIME_API_KEY", "").strip():
        return "env WAKATIME_API_KEY"
    cfg = find_config()
    if cfg and read_cfg_value(cfg, "api_key"):
        return str(cfg)
    return None


def oauth_token(plugin_ctx: Any = None) -> Optional[str]:
    """Personal OAuth token for REST API reads: env first, then plugin setting."""
    env = os.environ.get("WAKATIME_OAUTH_TOKEN", "").strip()
    if env:
        return env
    if plugin_ctx is not None:
        try:
            value = plugin_ctx.get_config("oauth_token", default="")
            if value and str(value).strip():
                return str(value).strip()
        except Exception:
            pass
    value = plugin_config_value("oauth_token", "")
    return str(value).strip() if value else None


def plugin_config_value(key: str, default: Any = None) -> Any:
    """Read plugins.entries.wakatime.settings.<key> from the active Hermes profile.

    Used by the `hermes wakatime` CLI command, which has no PluginContext.
    """
    try:
        from hermes_cli.config import load_config  # type: ignore

        cfg = load_config() or {}
        plugins = cfg.get("plugins") or {}
        entries = plugins.get("entries") or {}
        entry = entries.get(PLUGIN_NAME) or {}
        settings = entry.get("settings") or {}
        return settings.get(key, default)
    except Exception:
        return default


def mask_key(key: Optional[str]) -> str:
    """A non-reversible hint for status output; never the credential."""
    if not key:
        return "not configured"
    return f"configured (…{key[-4:]})" if len(key) > 8 else "configured"


def plugin_string() -> str:
    return f"{EDITOR_NAME}/{EDITOR_LEVEL_VERSION} hermes-wakatime/{PLUGIN_VERSION}"


def _parse_iso(value: Optional[str]) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def backoff_state(now: Optional[dt.datetime] = None) -> dict:
    """Snapshot of the CLI's API backoff from wakatime-internal.cfg."""
    path = internal_config_path()
    if not path.is_file():
        return {"active": False, "path": str(path), "backoff_at": None, "retries": None}
    text = ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"active": False, "path": str(path), "backoff_at": None, "retries": None}

    def value_for(key: str) -> Optional[str]:
        for line in text.splitlines():
            stripped = line.strip()
            if "=" in stripped:
                left, _, right = stripped.partition("=")
                if left.strip().lower() == key.lower():
                    return right.strip() or None
        return None

    backoff_at = value_for("backoff_at")
    retries = value_for("backoff_retries")
    parsed = _parse_iso(backoff_at)
    reference = now or dt.datetime.now(dt.timezone.utc)
    active = parsed is not None and parsed > reference
    return {
        "active": active,
        "path": str(path),
        "backoff_at": backoff_at,
        "retries": retries,
    }


def reset_backoff() -> tuple[bool, str]:
    """Remove backoff_at/backoff_retries from the internal config after a timestamped backup.

    Returns (changed, detail). Explicit user action only; never called automatically.
    """
    path = internal_config_path()
    if not path.is_file():
        return False, f"no internal config at {path}"
    backup = path.with_name(path.name + f".bak-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        backup.write_text(text, encoding="utf-8")
        kept = [
            line for line in text.splitlines(True)
            if not line.strip().startswith(("backoff_at", "backoff_retries"))
        ]
        path.write_text("".join(kept), encoding="utf-8")
    except OSError as exc:
        return False, f"could not reset backoff: {exc}"
    return True, f"wrote backup to {backup}"
