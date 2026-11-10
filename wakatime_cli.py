"""`hermes wakatime` terminal subcommands."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

from . import wakatime_client as client
from . import wakatime_paths as paths
from . import wakatime_tools as tools


def setup_cli(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="wakatime_command")

    status = sub.add_parser("status", help="Show CLI, config, credentials, queue and today's time")
    status.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    today = sub.add_parser("today", help="Show coding time for today (WakaTime account-local day)")
    today.add_argument("--goal", help="WakaTime goal id to report instead of total time")
    today.add_argument("--hide-categories", action="store_true", help="Skip category breakdown")
    today.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    offline = sub.add_parser("offline", help="Inspect the offline heartbeat queue")
    offline.add_argument("--limit", type=int, default=100, help="Queued heartbeats to preview (default 100)")
    offline.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    sync = sub.add_parser("sync", help="Flush queued offline heartbeats to WakaTime")
    sync.add_argument("--limit", type=int, default=1000, help="Heartbeats to sync; 0 = all")
    sync.add_argument("--timeout", type=float, default=120, help="Seconds to wait (default 120)")
    sync.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    send = sub.add_parser("send", help="Send a heartbeat for one entity")
    send.add_argument("entity", help="Absolute file path, or URL/domain/app for other entity types")
    send.add_argument("--entity-type", choices=["file", "domain", "url", "app"], default="file")
    send.add_argument("--category", default="ai coding", help="WakaTime category (default: ai coding)")
    send.add_argument("--project", help="Override project name")
    send.add_argument("--language", help="Override language name")
    send.add_argument("--ai-line-changes", type=int, help="AI line changes since the last heartbeat")
    send.add_argument("--write", action="store_true", help="Mark as written/modified")
    send.add_argument("--sync-limit", type=int, default=1, help="Offline heartbeats synced alongside; 0 = all")
    send.add_argument("--timeout", type=float, default=30, help="Seconds to wait (default 30)")
    send.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    stats = sub.add_parser("stats", help="Coding stats by range (today needs no OAuth token)")
    stats.add_argument("--range", default="today", help="today, last_7_days, last_30_days, last_year, all_time, YYYY")
    stats.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    reset = sub.add_parser(
        "reset-backoff",
        help="Clear the CLI's stored API backoff after backing up wakatime-internal.cfg",
    )
    reset.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _require_cli() -> Optional[Path]:
    cli = paths.find_cli()
    if cli is None:
        print("wakatime-cli not found.")
        print("Install it from https://github.com/wakatime/wakatime-cli/releases, or set WAKATIME_CLI.")
        return None
    return cli


def _top(items: list[dict], label: str) -> str:
    if not items:
        return f"  {label}: none"
    parts = [f"{item.get('name')} ({item.get('text') or item.get('total_seconds')})" for item in items[:5]]
    return f"  {label}: " + ", ".join(parts)


def cmd_status(as_json: bool = False) -> int:
    payload = tools.status_payload(None)
    if as_json:
        _print_json(payload)
        return 0 if payload.get("ok") else 1
    print(f"Hermes WakaTime plugin v{paths.PLUGIN_VERSION}")
    print(f"CLI: {payload.get('cli_path') or 'NOT FOUND'}"
          + (f" ({payload.get('cli_version')})" if payload.get("cli_version") else ""))
    print(f"Config: {payload.get('config_path') or 'NOT FOUND'}")
    key = payload.get("api_key_source")
    print(f"API key: {paths.mask_key(paths.api_key_config()) if key else 'not configured'}"
          + (f" [{key}]" if key else " — add api_key under [settings] in ~/.wakatime.cfg"))
    print(f"OAuth token: {'configured' if payload.get('oauth_token_configured') else 'not configured'}")
    queued = payload.get("offline_queue_heartbeats")
    print(f"Offline queue: {queued if queued is not None else 'unreadable'} heartbeat(s)")
    if payload.get("offline_database_mb"):
        print(f"Offline db: {payload['offline_database_mb']} MB at {payload['offline_database']}")
    backoff = payload.get("backoff") or {}
    if backoff.get("active"):
        print(f"API backoff: ACTIVE until {backoff.get('backoff_at')} (retries {backoff.get('retries')})")
        print("  Heartbeats will queue offline. After checking connectivity: hermes wakatime reset-backoff")
    else:
        print("API backoff: none")
    today = payload.get("today") or {}
    if today.get("time_text"):
        print(f"Today ({today.get('account_timezone')}): {today['time_text']}")
        if today.get("projects"):
            print(_top(today["projects"], "Projects"))
        if today.get("languages"):
            print(_top(today["languages"], "Languages"))
    elif payload.get("today_lookup_error"):
        print(f"Today: unavailable ({payload['today_lookup_error']})")
    else:
        print("Today: no activity recorded yet for the WakaTime account-local day")
        if today.get("account_timezone"):
            print(f"  Account timezone: {today['account_timezone']} (may differ from this PC)")
    settings = payload.get("settings") or {}
    print("Settings: " + ", ".join(f"{k}={v}" for k, v in settings.items()))
    for warning in payload.get("warnings") or []:
        print(f"Warning: {warning}")
    return 0 if payload.get("ok") else 1


def cmd_today(args: argparse.Namespace) -> int:
    if not _require_cli():
        return 1
    if not paths.api_key_source():
        print("No WakaTime API key found in ~/.wakatime.cfg or WAKATIME_API_KEY.")
        return 1
    try:
        if args.goal:
            text = client.today_text(goal=args.goal)
            payload = {"goal": args.goal, "time_text": text}
        else:
            payload = client.summarize_today(client.today_raw())
    except client.WakaTimeClientError as exc:
        print(str(exc))
        return 1
    if args.json:
        _print_json(payload)
        return 0
    if args.goal:
        print(f"Goal {args.goal}: {payload.get('time_text') or 'no data'}")
        return 0
    if payload.get("time_text"):
        print(f"Today ({payload.get('account_timezone')}): {payload['time_text']}")
    else:
        print("Today: no activity recorded yet for the WakaTime account-local day")
        if payload.get("account_timezone"):
            print(f"Account timezone: {payload['account_timezone']} (may differ from this PC)")
    if not args.hide_categories:
        print(_top(payload.get("categories") or [], "Categories"))
    print(_top(payload.get("projects") or [], "Projects"))
    print(_top(payload.get("languages") or [], "Languages"))
    print(_top(payload.get("editors") or [], "Editors"))
    if payload.get("ai_additions") or payload.get("ai_deletions"):
        print(f"AI lines: +{payload.get('ai_additions') or 0} / -{payload.get('ai_deletions') or 0}")
    return 0


def cmd_offline(args: argparse.Namespace) -> int:
    if not _require_cli():
        return 1
    try:
        count = client.offline_count()
        entries = client.offline_print(max(1, min(args.limit, 800))) if count else []
    except client.WakaTimeClientError as exc:
        print(str(exc))
        return 1
    if args.json:
        payload = json.loads(tools.handle_offline({"limit": args.limit}))
        _print_json(payload)
        return 0 if payload.get("ok") else 1
    print(f"Queued offline heartbeats: {count if count is not None else 'unreadable'}")
    if entries:
        from collections import Counter

        projects = Counter(str(e.get("project") or "unknown") for e in entries)
        languages = Counter(str(e.get("language") or "unknown") for e in entries)
        categories = Counter(str(e.get("category") or "unknown") for e in entries)
        print(f"Previewed {len(entries)}: top projects " + ", ".join(f"{k} ({v})" for k, v in projects.most_common(5)))
        print("  Languages: " + ", ".join(f"{k} ({v})" for k, v in languages.most_common(5)))
        print("  Categories: " + ", ".join(f"{k} ({v})" for k, v in categories.most_common(5)))
        times = [float(e["time"]) for e in entries if isinstance(e.get("time"), (int, float))]
        if times:
            print(f"  Preview timestamps: {tools._iso(min(times))} .. {tools._iso(max(times))}")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    if not _require_cli():
        return 1
    before = client.offline_count()
    try:
        out = client.sync_offline(limit=args.limit, timeout=args.timeout)
    except client.WakaTimeClientError as exc:
        print(str(exc))
        return 1
    after = client.offline_count()
    payload = {
        "queued_before": before,
        "queued_after": after,
        "synced_estimate": max((before or 0) - (after or 0), 0) if isinstance(before, int) and isinstance(after, int) else None,
        "limit": args.limit,
        "exit_code": out.get("exit_code"),
    }
    if args.json:
        _print_json(payload)
    else:
        print(f"Offline queue before: {before}; after: {after}")
        if payload["synced_estimate"] is not None:
            print(f"Synced ~{payload['synced_estimate']} heartbeat(s) ({args.limit if args.limit else 'all'} requested)")
        if out.get("stderr"):
            print(f"stderr: {out['stderr']}")
    return 0 if out.get("exit_code") == 0 else 1


def cmd_send(args: argparse.Namespace) -> int:
    if not _require_cli():
        return 1
    entity = Path(args.entity).expanduser()
    if args.entity_type == "file" and not entity.is_absolute():
        entity = Path.cwd() / entity
    if args.entity_type == "file" and not entity.is_file():
        print(f"File entity does not exist: {entity}")
        return 1
    try:
        out = client.send_heartbeat(
            str(entity),
            entity_type=args.entity_type,
            category=args.category,
            project=args.project,
            alternate_project=args.project or str(paths.plugin_config_value("fallback_project", "hermes") or ""),
            language=args.language,
            is_write=args.write,
            ai_line_changes=args.ai_line_changes,
            sync_limit=max(0, args.sync_limit),
            timeout=args.timeout,
        )
    except client.WakaTimeClientError as exc:
        print(str(exc))
        return 1
    payload = {
        "entity": str(entity),
        "category": args.category,
        "exit_code": out.get("exit_code"),
        "delivery": "sent_or_queued_offline" if out.get("exit_code") == 0 else "failed",
        "stderr": out.get("stderr") or None,
    }
    if args.json:
        _print_json(payload)
    else:
        print(f"Heartbeat {payload['entity']} ({args.category}): {payload['delivery']}")
        if out.get("stderr"):
            print(f"stderr: {out['stderr']}")
    return 0 if out.get("exit_code") == 0 else 1


def cmd_stats(args: argparse.Namespace) -> int:
    if not _require_cli():
        return 1
    time_range = str(args.range or "today").strip().lower()
    try:
        if time_range in {"today", ""}:
            payload = {"source": "wakatime-cli", "range": "today", **client.summarize_today(client.today_raw())}
        else:
            token = paths.oauth_token(None)
            if not token:
                print("Stats outside today need a personal WakaTime OAuth access token.")
                print("Set WAKATIME_OAUTH_TOKEN (get one at https://wakatime.com/oauth) or the plugin oauth_token setting.")
                print("WakaTime plugin API keys are write-only.")
                return 1
            payload = {"source": "wakatime-api", "range": time_range, **client.stats_api(time_range, token=token)}
    except client.WakaTimeClientError as exc:
        print(str(exc))
        return 1
    if args.json:
        _print_json(payload)
        return 0
    total = payload.get("time_text") or client.summarize_duration_seconds(payload.get("total_seconds"))
    print(f"{time_range}: {total}")
    if payload.get("range") and isinstance(payload.get("range"), dict):
        print(f"  range: {payload['range'].get('text') or payload.get('range')}")
    print(_top(payload.get("categories") or [], "Categories"))
    print(_top(payload.get("projects") or [], "Projects"))
    print(_top(payload.get("languages") or [], "Languages"))
    return 0


def cmd_reset_backoff(args: argparse.Namespace) -> int:
    before = paths.backoff_state()
    if not before.get("active"):
        print("No active API backoff in wakatime-internal.cfg; nothing to reset.")
        return 0
    print(f"Active backoff until {before.get('backoff_at')} (retries {before.get('retries')}).")
    if not args.yes:
        answer = input("Reset it and back up the internal config first? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Left unchanged.")
            return 1
    changed, detail = paths.reset_backoff()
    print(("Backoff cleared. " if changed else "Not cleared. ") + detail)
    return 0 if changed else 1


def handle_cli(args: argparse.Namespace) -> int:
    command = getattr(args, "wakatime_command", None)
    if command == "today":
        return cmd_today(args)
    if command == "offline":
        return cmd_offline(args)
    if command == "sync":
        return cmd_sync(args)
    if command == "send":
        return cmd_send(args)
    if command == "stats":
        return cmd_stats(args)
    if command == "reset-backoff":
        return cmd_reset_backoff(args)
    return cmd_status(getattr(args, "json", False))
