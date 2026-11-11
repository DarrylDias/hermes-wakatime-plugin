"""Tool schemas exposed by the Hermes WakaTime plugin.

Descriptions are what the model reads to decide when a tool is useful. Keep them specific and
honest about what each handler can and cannot verify.
"""

from __future__ import annotations

WA_ENTITY_TYPES = ["file", "domain", "url", "app"]

WA_CATEGORIES = [
    "coding",
    "ai coding",
    "building",
    "indexing",
    "debugging",
    "learning",
    "notes",
    "meeting",
    "planning",
    "researching",
    "communicating",
    "supporting",
    "advising",
    "running tests",
    "writing tests",
    "manual testing",
    "writing docs",
    "code reviewing",
    "browsing",
    "translating",
    "designing",
]

_STATUS = {
    "name": "wakatime_status",
    "description": (
        "Report the local WakaTime setup used by Hermes: wakatime-cli path and version, "
        "configured API key source (never the key itself), config paths, offline heartbeat "
        "count, an active API backoff, whether a personal OAuth token is available, and today's "
        "coding time. Use this before other wakatime tools when diagnosing tracking. If the CLI "
        "is missing, `hermes wakatime install-cli --yes` bootstraps the official release."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

_TODAY = {
    "name": "wakatime_today",
    "description": (
        "Get the WakaTime coding time recorded today, with categories, projects, languages and "
        "editors plus AI line-change totals. Uses `wakatime-cli --today`, which follows the "
        "WakaTime account's own timezone (not necessarily this PC's timezone). Optional goal id "
        "returns progress for a WakaTime goal instead."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": "Optional WakaTime goal id (from wakatime.com/api/v1/users/current/goals).",
            },
            "hide_categories": {
                "type": "boolean",
                "description": "When true, report total coding time without per-category totals.",
            },
        },
        "required": [],
    },
}

_HEARTBEAT = {
    "name": "wakatime_heartbeat",
    "description": (
        "Send or queue a WakaTime heartbeat for one entity (file, URL, domain or app) through "
        "wakatime-cli. Project and language are auto-detected from a file path unless given. "
        "WakaTime counts at most 2 minutes between heartbeats; when the API is rate-limited or the "
        "CLI is backing off, the heartbeat is saved in the local offline queue instead."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "entity": {
                "type": "string",
                "description": "Absolute file path (or URL/domain/app when entity_type is not file).",
            },
            "entity_type": {
                "type": "string",
                "enum": WA_ENTITY_TYPES,
                "description": "Kind of entity. Default: file.",
            },
            "category": {
                "type": "string",
                "enum": WA_CATEGORIES,
                "description": "WakaTime category. Default: ai coding (Hermes is AI-authored work).",
            },
            "project": {
                "type": "string",
                "description": "Override project name. Omit to let wakatime-cli detect it.",
            },
            "language": {
                "type": "string",
                "description": "Override language name. Omit to let wakatime-cli detect it.",
            },
            "is_write": {
                "type": "boolean",
                "description": "Mark the entity as written/modified (passed as --write).",
            },
            "ai_line_changes": {
                "type": "integer",
                "description": "AI line changes since the last heartbeat (added minus deleted).",
            },
            "lines_in_file": {
                "type": "integer",
                "description": "Optional total line count for the entity.",
            },
            "is_unsaved": {
                "type": "boolean",
                "description": "Allow a file that does not exist on disk (--is-unsaved-entity).",
            },
            "sync_limit": {
                "type": "integer",
                "description": "Offline heartbeats synced alongside this heartbeat. Default 1; 0 syncs all.",
            },
            "timeout": {
                "type": "integer",
                "description": "Seconds to wait for wakatime-cli. Default 30.",
            },
        },
        "required": ["entity"],
    },
}

_OFFLINE = {
    "name": "wakatime_offline",
    "description": (
        "Inspect the local wakatime-cli offline heartbeat queue (~/.wakatime/offline_heartbeats.bdb): "
        "number queued, oldest/newest timestamp, and counts by project, language, category and "
        "entity. Use this when today's dashboard is missing recent tracking."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Number of queued heartbeats to read for the breakdown (1-800). Default 100.",
            },
        },
        "required": [],
    },
}

_SYNC = {
    "name": "wakatime_sync",
    "description": (
        "Flush queued offline WakaTime heartbeats to the WakaTime API with "
        "`wakatime-cli --sync-offline-activity`. Returns queued counts before and after so the "
        "result is verifiable. limit 0 syncs the whole queue (can take minutes); lower values sync "
        "that many oldest heartbeats."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "How many offline heartbeats to sync. 0 = all; default 1000.",
            },
            "timeout": {
                "type": "integer",
                "description": "Seconds to wait for the sync. Default 120, max 600.",
            },
        },
        "required": [],
    },
}

_STATS = {
    "name": "wakatime_stats",
    "description": (
        "Get WakaTime coding stats for a range. `today` uses wakatime-cli (no OAuth needed, "
        "account-local day). Other ranges use the WakaTime REST API and therefore need a personal "
        "OAuth access token (WAKATIME_OAUTH_TOKEN or the plugin's oauth_token setting); plugin API "
        "keys are write-only. Ranges: today, last_7_days, last_30_days, last_6_months, last_year, "
        "all_time, or a YYYY / YYYY-MM range supported by the API."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "range": {
                "type": "string",
                "description": "today (default), last_7_days, last_30_days, last_6_months, last_year, all_time, YYYY or YYYY-MM.",
            },
        },
        "required": [],
    },
}
