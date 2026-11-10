"""Handlers and the post_tool_call hook for the Hermes WakaTime plugin."""

from __future__ import annotations

import difflib
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import wakatime_client as client
from . import wakatime_paths as paths

logger = logging.getLogger(__name__)

TOOLSET = "wakatime"

# Hermes tool names whose result implies file activity. write_file/patch/read_file are the
# built-ins; the rest are tolerated aliases used by other agent surfaces.
_TOOL_KINDS = {
    "write_file": "write",
    "append_file": "write",
    "patch": "edit",
    "apply_patch": "edit",
    "edit_file": "edit",
    "multi_edit": "edit",
    "replace_in_file": "edit",
    "str_replace_editor": "edit",
    "read_file": "read",
}

_last_heartbeat_at: dict[str, float] = {}
_last_lock = threading.Lock()


def _setting(plugin_ctx: Any, key: str, default: Any) -> Any:
    if plugin_ctx is not None:
        try:
            return plugin_ctx.get_config(key, default=default)
        except Exception:
            pass
    return paths.plugin_config_value(key, default)


def _int_setting(plugin_ctx: Any, key: str, default: int) -> int:
    value = _setting(plugin_ctx, key, default)
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _bool_setting(plugin_ctx: Any, key: str, default: bool) -> bool:
    value = _setting(plugin_ctx, key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "on"}:
        return True
    if text in {"false", "0", "no", "off", ""}:
        return False
    return default


def _dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _error(message: str, *, extra: Optional[dict] = None) -> str:
    payload = {"ok": False, "error": message}
    if extra:
        payload.update(extra)
    return _dumps(payload)


# ---- status / today ----


def status_payload(plugin_ctx: Any = None) -> dict:
    cli = paths.find_cli()
    cfg = paths.find_config()
    api_source = paths.api_key_source()
    token_set = bool(paths.oauth_token(plugin_ctx))
    backoff = paths.backoff_state()
    offline = client.offline_count() if cli else None
    version = client.cli_version() if cli else None
    today = None
    today_error = None
    if cli and api_source:
        try:
            today = client.summarize_today(client.today_raw())
        except client.WakaTimeClientError as exc:
            today_error = str(exc)
    warnings = []
    if backoff.get("active"):
        warnings.append(
            f"wakatime-cli API backoff is active until {backoff.get('backoff_at')} "
            f"(retries={backoff.get('retries')}). Heartbeats are queued offline. "
            "Use `hermes wakatime reset-backoff` only after checking connectivity."
        )
    if today_error:
        warnings.append(f"today lookup failed: {today_error}")
    db = paths.offline_db_path()
    return {
        "ok": cli is not None,
        "cli_path": str(cli) if cli else None,
        "cli_version": version,
        "config_path": str(cfg) if cfg else None,
        "api_key_source": api_source,
        "api_key_configured": api_source is not None,
        "api_key_hint": paths.mask_key(paths.api_key_config() or os.environ.get("WAKATIME_API_KEY")),
        "oauth_token_configured": token_set,
        "offline_queue_heartbeats": offline,
        "offline_database": str(db) if db.is_file() else None,
        "offline_database_mb": round(db.stat().st_size / (1024 * 1024), 2) if db.is_file() else None,
        "backoff": backoff,
        "today": today,
        "today_lookup_error": today_error,
        "settings": {
            "fallback_project": _setting(plugin_ctx, "fallback_project", "hermes"),
            "min_heartbeat_interval_seconds": _int_setting(plugin_ctx, "min_heartbeat_interval_seconds", 60),
            "track_file_edits": _bool_setting(plugin_ctx, "track_file_edits", True),
            "heartbeat_sync_limit": _int_setting(plugin_ctx, "heartbeat_sync_limit", 1),
        },
        "warnings": warnings,
    }


def handle_status(args: dict, plugin_ctx: Any = None, **kw: Any) -> str:
    try:
        payload = status_payload(plugin_ctx)
    except Exception as exc:  # noqa: BLE001 -- status must not kill the turn
        logger.debug("wakatime status failed", exc_info=True)
        return _error(f"status check failed: {exc}")
    return _dumps(payload)


def handle_today(args: dict, plugin_ctx: Any = None, **kw: Any) -> str:
    goal = str(args.get("goal") or "").strip() or None
    hide_categories = bool(args.get("hide_categories"))
    cli = paths.find_cli()
    if cli is None:
        return _error("wakatime-cli not found; install it or set WAKATIME_CLI", extra={
            "install_url": "https://github.com/wakatime/wakatime-cli/releases",
        })
    if not paths.api_key_source():
        return _error("no WakaTime API key in ~/.wakatime.cfg or WAKATIME_API_KEY")
    try:
        if goal:
            text = client.today_text(goal=goal, hide_categories=False)
            return _dumps({"ok": True, "goal": goal, "time_text": text, "source": "wakatime-cli"})
        data = client.summarize_today(client.today_raw())
    except client.WakaTimeClientError as exc:
        return _error(str(exc))
    payload = {"ok": True, "source": "wakatime-cli", "hide_categories": hide_categories, **data}
    if not data.get("time_text") and data.get("account_timezone"):
        payload["note"] = (
            "No activity yet in the current WakaTime account-local day. "
            f"The account timezone is {data['account_timezone']}, which may differ from this PC's."
        )
    return _dumps(payload)


# ---- heartbeat / offline / sync ----


def _entity_exists(entity: str, entity_type: str) -> bool:
    if entity_type != "file":
        return True
    try:
        return Path(entity).expanduser().is_file()
    except OSError:
        return False


def handle_heartbeat(args: dict, plugin_ctx: Any = None, **kw: Any) -> str:
    entity = str(args.get("entity") or "").strip()
    if not entity:
        return _error("entity is required")
    entity_type = str(args.get("entity_type") or "file").strip().lower()
    if entity_type not in {"file", "domain", "url", "app"}:
        return _error(f"unsupported entity_type {entity_type!r}")
    category = str(args.get("category") or "ai coding").strip()
    try:
        sync_limit = int(args.get("sync_limit", paths.plugin_config_value("heartbeat_sync_limit", 1)))
        timeout = int(args.get("timeout", 30))
    except (TypeError, ValueError):
        sync_limit, timeout = 1, 30
    sync_limit = max(0, sync_limit)
    timeout = max(5, min(timeout, 120))
    is_unsaved = bool(args.get("is_unsaved"))
    if entity_type == "file" and not is_unsaved and not _entity_exists(entity, entity_type):
        return _error(
            f"file entity does not exist: {entity}. Pass is_unsaved=true to track a file that "
            "has not been created on disk yet."
        )
    project = str(args.get("project") or "").strip() or None
    language = str(args.get("language") or "").strip() or None
    line_changes = args.get("ai_line_changes")
    try:
        line_changes = int(line_changes) if line_changes not in (None, "") else None
    except (TypeError, ValueError):
        line_changes = None
    try:
        result = client.send_heartbeat(
            entity,
            entity_type=entity_type,
            category=category,
            project=project,
            alternate_project=project if project else str(_setting(plugin_ctx, "fallback_project", "hermes") or ""),
            language=language,
            is_write=bool(args.get("is_write")),
            ai_line_changes=line_changes,
            lines_in_file=args.get("lines_in_file"),
            cursorpos=args.get("cursorpos"),
            is_unsaved=is_unsaved,
            sync_limit=sync_limit,
            timeout=timeout,
        )
    except client.WakaTimeClientError as exc:
        return _error(str(exc))
    return _dumps(
        {
            "ok": result.get("exit_code") == 0,
            "delivery": "sent_or_queued_offline" if result.get("exit_code") == 0 else "failed",
            "entity": entity,
            "entity_type": entity_type,
            "category": category,
            "exit_code": result.get("exit_code"),
            "cli": result.get("cli"),
            "stderr": result.get("stderr") or None,
        }
    )


def _aggregate(items: list[dict], key: str, limit: int = 10) -> list[dict]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return [{"name": name, "count": count} for name, count in sorted(counts.items(), key=lambda kv: -kv[1])[:limit]]


def handle_offline(args: dict, plugin_ctx: Any = None, **kw: Any) -> str:
    cli = paths.find_cli()
    if cli is None:
        return _error("wakatime-cli not found; install it or set WAKATIME_CLI")
    try:
        limit = max(1, min(int(args.get("limit", 100) or 100), 800))
    except (TypeError, ValueError):
        limit = 100
    count = client.offline_count()
    if count is None:
        return _error("could not read the offline heartbeat queue")
    entries = []
    if count:
        try:
            entries = client.offline_print(limit)
        except client.WakaTimeClientError as exc:
            entries = []
            logger.debug("offline preview failed: %s", exc)
    times = [float(item["time"]) for item in entries if isinstance(item.get("time"), (int, float))]
    payload = {
        "ok": True,
        "heartbeats_queued": count,
        "previewed": len(entries),
        "oldest_previewed_utc": _iso(times and min(times)),
        "newest_previewed_utc": _iso(times and max(times)),
        "projects": _aggregate(entries, "project"),
        "languages": _aggregate(entries, "language"),
        "categories": _aggregate(entries, "category"),
        "entities": _aggregate(entries, "entity"),
        "sample": [
            {
                "entity": item.get("entity"),
                "project": item.get("project"),
                "language": item.get("language"),
                "category": item.get("category"),
                "time": _iso(item.get("time")),
            }
            for item in entries[:10]
        ],
    }
    if count:
        payload["note"] = (
            "The queue holds heartbeats the API has not accepted yet (offline or backoff state). "
            "Use wakatime_sync to flush them."
        )
    return _dumps(payload)


def _iso(epoch: Any) -> Optional[str]:
    if epoch in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def handle_sync(args: dict, plugin_ctx: Any = None, **kw: Any) -> str:
    try:
        limit = int(args.get("limit", 1000))
        timeout = float(args.get("timeout", 120))
    except (TypeError, ValueError):
        limit, timeout = 1000, 120.0
    limit = max(0, limit)
    timeout = max(10.0, min(timeout, 600.0))
    before = client.offline_count()
    try:
        out = client.sync_offline(limit=limit, timeout=timeout)
    except client.WakaTimeClientError as exc:
        return _error(str(exc), extra={"queued_before": before})
    after = client.offline_count()
    synced = before - after if isinstance(before, int) and isinstance(after, int) else None
    return _dumps(
        {
            "ok": out.get("exit_code") == 0,
            "limit": limit,
            "limit_note": "0 syncs all queued heartbeats",
            "queued_before": before,
            "queued_after": after,
            "synced_estimate": max(synced, 0) if synced is not None else None,
            "exit_code": out.get("exit_code"),
            "stderr": out.get("stderr") or None,
        }
    )


def handle_stats(args: dict, plugin_ctx: Any = None, **kw: Any) -> str:
    time_range = str(args.get("range") or "today").strip().lower()
    if time_range in {"today", ""}:
        try:
            data = client.summarize_today(client.today_raw())
        except client.WakaTimeClientError as exc:
            return _error(str(exc))
        return _dumps({"ok": True, "source": "wakatime-cli", "range": "today", **data})
    token = paths.oauth_token(plugin_ctx)
    if not token:
        return _error(
            "REST stats outside today need a personal WakaTime OAuth access token "
            "(WAKATIME_OAUTH_TOKEN or the plugin oauth_token setting); plugin API keys only write "
            "heartbeats. Get a personal token at https://wakatime.com/oauth.",
            extra={"range": time_range, "local_option": "wakatime_today"},
        )
    try:
        data = client.stats_api(time_range, token=token)
    except client.WakaTimeClientError as exc:
        return _error(str(exc))
    return _dumps({"ok": True, "source": "wakatime-api", "range": time_range, **data})


# ---- automatic file-activity hook ----


def _line_delta(old_text: str, new_text: str) -> int:
    """Approximate AI line changes as additions minus deletions for a patch."""
    old_lines = (old_text or "").splitlines()
    new_lines = (new_text or "").splitlines()
    added = removed = 0
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "insert":
            added += j2 - j1
        elif tag == "delete":
            removed += i2 - i1
        elif tag == "replace":
            removed += i2 - i1
            added += j2 - j1
    return added - removed


def _path_from_args(args: dict) -> Optional[str]:
    for key in ("path", "file_path", "filePath", "file", "filename"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _tool_succeeded(result: Any) -> bool:
    if not isinstance(result, str):
        return result is not None
    text = result.strip()
    if not text:
        return True
    try:
        parsed = json.loads(text)
    except ValueError:
        return "error" not in text.lower()[:200]
    if isinstance(parsed, dict):
        if parsed.get("success") is False or parsed.get("ok") is False:
            return False
        if parsed.get("error"):
            return False
    return True


def handle_post_tool_call(
    tool_name: str,
    args: dict,
    result: Any,
    task_id: Optional[str] = None,
    duration_ms: Optional[int] = None,
    plugin_ctx: Any = None,
    **kwargs: Any,
) -> None:
    """Send a rate-limited heartbeat for file activity in a completed Hermes tool call."""
    kind = _TOOL_KINDS.get(str(tool_name))
    if kind is None:
        return
    if not _bool_setting(plugin_ctx, "track_file_edits", True):
        return
    if not isinstance(args, dict) or not _tool_succeeded(result):
        return
    entity = _path_from_args(args)
    if not entity:
        return
    expanded = str(Path(entity).expanduser())
    if not Path(expanded).is_file():
        return
    interval = max(0, _int_setting(plugin_ctx, "min_heartbeat_interval_seconds", 60))
    now = time.monotonic()
    key = os.path.normcase(os.path.abspath(expanded))
    with _last_lock:
        previous = _last_heartbeat_at.get(key)
        if interval and previous is not None and now - previous < interval:
            return
        _last_heartbeat_at[key] = now
    line_changes: Optional[int] = None
    old_text = args.get("old_string") or args.get("old_text") or args.get("old")
    new_text = args.get("new_string") or args.get("new_text") or args.get("new")
    if kind == "edit" and isinstance(old_text, str) and isinstance(new_text, str):
        line_changes = _line_delta(old_text, new_text)
    sync_limit = max(0, _int_setting(plugin_ctx, "heartbeat_sync_limit", 1))
    try:
        client.send_heartbeat(
            expanded,
            entity_type="file",
            category="ai coding",
            alternate_project=str(_setting(plugin_ctx, "fallback_project", "hermes") or "") or None,
            is_write=kind != "read",
            ai_line_changes=line_changes,
            sync_limit=sync_limit,
            timeout=20.0,
        )
    except client.WakaTimeClientError as exc:
        logger.debug("wakatime hook heartbeat failed for %s: %s", expanded, exc)
    except Exception:  # noqa: BLE001 -- hooks must never break tool calls
        logger.debug("wakatime hook unexpected failure", exc_info=True)
