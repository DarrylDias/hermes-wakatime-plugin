"""wakatime-cli invocation and WakaTime REST helpers.

The CLI is the source of truth for heartbeats, today's dashboard, and the offline queue.
REST reads use a personal OAuth token because WakaTime plugin API keys are write-only.
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import wakatime_paths as paths

DEFAULT_SYNC_LIMIT = 1
_MAX_OUTPUT = 4000
_API_BASE = "https://api.wakatime.com/api/v1"
_SUPPORTED_STATS_RANGES = {
    "last_7_days",
    "last_30_days",
    "last_6_months",
    "last_year",
    "all_time",
}


class WakaTimeClientError(RuntimeError):
    """A CLI invocation or API failure the handler should show to the user."""


def _clip(text: Optional[str]) -> str:
    text = text or ""
    if len(text) <= _MAX_OUTPUT:
        return text
    return text[:_MAX_OUTPUT] + "… [truncated]"


def _run(args: list[str], timeout: float = 60.0, clip_output: bool = True) -> dict:
    cli = paths.find_cli()
    if cli is None:
        raise WakaTimeClientError(
            "wakatime-cli not found. Install it from https://github.com/wakatime/wakatime-cli/releases "
            "or set WAKATIME_CLI to its executable."
        )
    command = [str(cli)] + [str(a) for a in args]
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return {
            "exit_code": None,
            "stdout": "",
            "stderr": f"wakatime-cli timed out after {timeout:g}s",
            "timed_out": True,
            "cli": str(cli),
        }
    except OSError as exc:
        raise WakaTimeClientError(f"could not start wakatime-cli at {cli}: {exc}") from exc

    def _fit(text: str) -> str:
        return _clip(text) if clip_output else (text or "")

    return {
        "exit_code": proc.returncode,
        "stdout": _fit(proc.stdout),
        "stderr": _fit(proc.stderr),
        "timed_out": False,
        "cli": str(cli),
    }


def _json_output(text: str) -> Optional[Any]:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        # Some CLI modes print log lines before the JSON payload.
        match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(1))
        except ValueError:
            return None


def cli_version() -> Optional[str]:
    try:
        out = _run(["--version"], timeout=10.0)
    except WakaTimeClientError:
        return None
    text = (out.get("stdout") or "").strip()
    return text.splitlines()[-1].strip() if text else None


def offline_count() -> Optional[int]:
    """Number of heartbeats waiting in the local offline bolt db, or None if unknown."""
    try:
        out = _run(["--offline-count"], timeout=30.0)
    except WakaTimeClientError:
        return None
    text = (out.get("stdout") or "").strip()
    if not text:
        return None
    match = re.search(r"\d+", text)
    return int(match.group(0)) if match else None


def offline_print(limit: int = 100) -> list[dict]:
    limit = max(1, min(int(limit), 800))
    out = _run(["--print-offline-heartbeats", str(limit), "--output", "json"], timeout=90.0, clip_output=False)
    if out.get("timed_out"):
        raise WakaTimeClientError(out.get("stderr") or "offline print timed out")
    payload = _json_output(out.get("stdout") or "")
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if out.get("exit_code") not in (0, None):
        raise WakaTimeClientError(
            out.get("stderr") or f"offline print failed (exit {out.get('exit_code')})"
        )
    return []


def today_raw() -> dict:
    """Raw status_bar/today data from the CLI, including categories/projects/languages."""
    out = _run(["--today", "--output", "raw-json"], timeout=90.0)
    if out.get("timed_out"):
        raise WakaTimeClientError(out.get("stderr") or "today lookup timed out")
    payload = _json_output(out.get("stdout") or "")
    if not isinstance(payload, dict):
        raise WakaTimeClientError(
            out.get("stderr") or "wakatime-cli --today returned no usable JSON"
        )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise WakaTimeClientError("wakatime-cli --today JSON has no data object")
    return payload.get("data") or {}


def today_text(goal: Optional[str] = None, hide_categories: bool = False) -> str:
    if goal:
        args = ["--today-goal", goal, "--output", "json"]
    else:
        args = ["--today"]
        if hide_categories:
            args.append("--today-hide-categories")
        args.extend(["--output", "json"])
    out = _run(args, timeout=90.0)
    if out.get("timed_out"):
        raise WakaTimeClientError(out.get("stderr") or "today lookup timed out")
    payload = _json_output(out.get("stdout") or "")
    if isinstance(payload, dict) and "text" in payload:
        return str(payload.get("text") or "")
    if out.get("exit_code") not in (0, None):
        raise WakaTimeClientError(
            out.get("stderr") or f"today lookup failed (exit {out.get('exit_code')})"
        )
    return ""


def _compact_items(items: Any, limit: int = 10) -> list[dict]:
    if not isinstance(items, list):
        return []
    compacted = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        compacted.append(
            {
                "name": item.get("name"),
                "text": item.get("text"),
                "total_seconds": item.get("total_seconds", 0),
                "percent": item.get("percent"),
            }
        )
    return compacted


def summarize_today(data: dict) -> dict:
    grand = data.get("grand_total") if isinstance(data.get("grand_total"), dict) else {}
    rng = data.get("range") if isinstance(data.get("range"), dict) else {}
    ai_additions = grand.get("ai_additions")
    ai_deletions = grand.get("ai_deletions")
    seconds = grand.get("total_seconds") or 0
    raw_text = grand.get("text") or ""
    # wakatime-cli rounds sub-minute totals to "0 secs"; prefer an exact duration for small values.
    time_text = raw_text
    if not raw_text or (raw_text == "0 secs" and float(seconds or 0) > 0):
        time_text = summarize_duration_seconds(seconds)
    return {
        "range": rng.get("date"),
        "range_text": rng.get("text"),
        "account_timezone": rng.get("timezone"),
        "total_seconds": seconds,
        "time_text": time_text,
        "ai_additions": ai_additions,
        "ai_deletions": ai_deletions,
        "ai_model_total_cost": grand.get("ai_model_total_cost"),
        "categories": _compact_items(data.get("categories"), 10),
        "projects": _compact_items(data.get("projects"), 10),
        "languages": _compact_items(data.get("languages"), 10),
        "editors": _compact_items(data.get("editors"), 10),
    }


def summarize_duration_seconds(seconds: Any) -> str:
    try:
        total = int(float(seconds or 0))
    except (TypeError, ValueError):
        total = 0
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hr")
    if minutes:
        parts.append(f"{minutes} {'min' if minutes == 1 else 'mins'}")
    if hours or minutes:
        return " ".join(parts)
    return f"{secs} {'sec' if secs == 1 else 'secs'}"


def sync_offline(limit: int = 1000, timeout: float = 120.0) -> dict:
    limit = max(0, int(limit))
    timeout = max(10.0, min(float(timeout), 600.0))
    out = _run(
        ["--sync-ai-disabled", "--sync-offline-activity", str(limit)],
        timeout=timeout,
    )
    if out.get("timed_out"):
        raise WakaTimeClientError(out.get("stderr") or f"offline sync timed out after {timeout:g}s")
    if out.get("exit_code") not in (0, None):
        raise WakaTimeClientError(
            out.get("stderr") or f"offline sync failed (exit {out.get('exit_code')})"
        )
    return out


def build_heartbeat_args(
    entity: str,
    *,
    entity_type: str = "file",
    category: str = "ai coding",
    project: Optional[str] = None,
    alternate_project: Optional[str] = None,
    language: Optional[str] = None,
    is_write: bool = False,
    ai_line_changes: Optional[int] = None,
    lines_in_file: Optional[int] = None,
    cursorpos: Optional[int] = None,
    is_unsaved: bool = False,
    sync_limit: int = DEFAULT_SYNC_LIMIT,
    explicit_cli: Optional[str] = None,
) -> list[str]:
    """Build wakatime-cli args for one heartbeat (no subprocess call)."""
    if entity_type == "file":
        expanded = Path(entity).expanduser()
        if not expanded.is_absolute():
            expanded = Path.cwd() / expanded
        entity = str(expanded)
    args = [
        "--entity", entity,
        "--entity-type", entity_type,
        "--category", category,
        "--plugin", paths.plugin_string(),
        "--sync-ai-disabled",
        "--sync-offline-activity", str(max(0, int(sync_limit))),
    ]
    if project:
        args.extend(["--project", project])
    elif alternate_project:
        args.extend(["--alternate-project", alternate_project])
    if language:
        args.extend(["--language", language])
    if is_write:
        args.append("--write")
    if ai_line_changes:
        args.extend(["--ai-line-changes", str(int(ai_line_changes))])
    if lines_in_file:
        args.extend(["--lines-in-file", str(int(lines_in_file))])
    if cursorpos:
        args.extend(["--cursorpos", str(int(cursorpos))])
    if is_unsaved:
        args.append("--is-unsaved-entity")
    return args


def send_heartbeat(
    entity: str,
    *,
    entity_type: str = "file",
    category: str = "ai coding",
    project: Optional[str] = None,
    alternate_project: Optional[str] = None,
    language: Optional[str] = None,
    is_write: bool = False,
    ai_line_changes: Optional[int] = None,
    lines_in_file: Optional[int] = None,
    cursorpos: Optional[int] = None,
    is_unsaved: bool = False,
    sync_limit: int = DEFAULT_SYNC_LIMIT,
    timeout: float = 30.0,
) -> dict:
    args = build_heartbeat_args(
        entity,
        entity_type=entity_type,
        category=category,
        project=project,
        alternate_project=alternate_project,
        language=language,
        is_write=is_write,
        ai_line_changes=ai_line_changes,
        lines_in_file=lines_in_file,
        cursorpos=cursorpos,
        is_unsaved=is_unsaved,
        sync_limit=sync_limit,
    )
    return _run(args, timeout=timeout)


def _api_get(path_segment: str, timeout: float = 30.0, token: Optional[str] = None) -> dict:
    token = token or paths.oauth_token()
    if not token:
        raise WakaTimeClientError(
            "REST stats need a personal WakaTime OAuth access token: set WAKATIME_OAUTH_TOKEN "
            "(get one at https://wakatime.com/oauth) or the plugin setting oauth_token. "
            "WakaTime plugin API keys are write-only."
        )
    url = f"{_API_BASE}/{path_segment.lstrip('/')}"
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": f"{paths.plugin_string()} api-v1",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            charset = (response.headers.get("Content-Type") or "").split("charset=")[-1] or "utf-8"
            body = response.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise WakaTimeClientError(f"WakaTime API HTTP {exc.code}: {body[:300]}") from exc
    except urllib.error.URLError as exc:
        raise WakaTimeClientError(f"WakaTime API request failed: {exc.reason}") from exc
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise WakaTimeClientError(f"WakaTime API returned non-JSON: {body[:200]}") from exc
    if isinstance(payload, dict) and payload.get("error"):
        raise WakaTimeClientError(str(payload["error"]))
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise WakaTimeClientError("WakaTime API response has no data object")
    return data


def stats_api(time_range: str, token: Optional[str] = None) -> dict:
    rng = time_range.strip()
    if rng not in _SUPPORTED_STATS_RANGES and not re.fullmatch(r"\d{4}(-\d{2})?", rng):
        raise WakaTimeClientError(
            f"unsupported stats range {rng!r}; use one of "
            f"{sorted(_SUPPORTED_STATS_RANGES)} or YYYY/YYYY-MM"
        )
    data = _api_get(f"users/current/stats/{urllib.parse.quote(rng, safe='')}", token=token)
    return summarize_stats(data)


def summarize_stats(data: dict) -> dict:
    best_day = data.get("best_day") if isinstance(data.get("best_day"), dict) else None
    daily_average = data.get("daily_average")
    return {
        "range": data.get("range"),
        "total_seconds": data.get("total_seconds") or 0,
        "time_text": summarize_duration_seconds(data.get("total_seconds")),
        "daily_average_seconds": daily_average,
        "daily_average_text": summarize_duration_seconds(daily_average),
        "is_up_to_date": data.get("is_up_to_date"),
        "best_day": {
            "date": best_day.get("date"),
            "text": best_day.get("text"),
            "total_seconds": best_day.get("total_seconds"),
        } if best_day else None,
        "categories": _compact_items(data.get("categories"), 10),
        "projects": _compact_items(data.get("projects"), 10),
        "languages": _compact_items(data.get("languages"), 10),
        "editors": _compact_items(data.get("editors"), 10),
    }
