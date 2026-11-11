# hermes-wakatime-plugin

WakaTime integration for [Hermes Agent](https://hermes-agent.nousresearch.com/docs), built as a
Hermes native plugin. It turns Hermes file activity into WakaTime heartbeats through the installed
`wakatime-cli`, reports today's coding time and AI line totals, inspects and syncs the offline
heartbeat queue, sends manual heartbeats, and optionally reads WakaTime REST stats with a personal
OAuth token.

## What it provides

| Surface | Description |
|---|---|
| `wakatime_status` tool | CLI path/version, config, API-key source, OAuth availability, offline queue, API backoff, today's time. Never exposes the key itself. |
| `wakatime_today` tool | Today's time, categories, projects, languages, editors and AI line totals via `wakatime-cli --today`. |
| `wakatime_heartbeat` tool | Send/queue one heartbeat for a file, URL, domain or app. |
| `wakatime_offline` tool | Queue count and breakdown by project, language, category and entity. |
| `wakatime_sync` tool | Flush the offline queue with before/after counts. |
| `wakatime_stats` tool | `today` via CLI; other ranges via REST with a personal OAuth token. |
| `post_tool_call` hook | Sends rate-limited `ai coding` heartbeats after Hermes `write_file`, `patch` and `read_file` calls (plus common tool aliases). |
| `hermes wakatime` CLI | `status`, `today`, `offline`, `sync`, `send`, `stats`, `reset-backoff`, `install-cli`. |

## Why WakaTime needs a CLI

WakaTime measures coding time from **heartbeats** (file activity events with project/language
metadata). Editor/IDE plugins call `wakatime-cli`, which queues heartbeats locally when offline and
sends them to `https://api.wakatime.com/api/v1/...`. Plugin API keys (`waka_...`) are write-only:
they can send heartbeats but cannot read summaries/stats. REST reads need a personal OAuth access
token. This plugin follows the same model as WakaTime's official OpenCode plugin
([angristan/opencode-wakatime](https://github.com/angristan/opencode-wakatime)):

- one heartbeat per changed/read file, batched through CLI args,
- category `ai coding` for AI-authored work,
- a local rate limit (default 60 s per file),
- `--ai-line-changes` from a patch's added-minus-deleted line count.

## Requirements

- Hermes Agent with plugin support (`hermes plugins ...`).
- `wakatime-cli`, normally at `~/.wakatime/wakatime-cli.exe` (Windows) or `~/.wakatime/wakatime-cli`
  on macOS/Linux. Install it manually from
  https://github.com/wakatime/wakatime-cli/releases, or use the plugin's consented bootstrap:
  `hermes wakatime install-cli` (add `--yes` for non-interactive use).
- `api_key` under `[settings]` in `~/.wakatime.cfg` (or `WAKATIME_HOME/.wakatime.cfg`), from
  https://wakatime.com/api-key.
- Optional for non-today REST stats: a personal OAuth access token, https://wakatime.com/oauth,
  exported as `WAKATIME_OAUTH_TOKEN` or stored in the plugin setting `oauth_token`.

## Bootstrap the CLI (consented)

`install-cli` resolves the latest official release (or `--version v2.28.4`), selects the correct
release asset for this OS/architecture, fetches the release checksum for the plan, downloads the
CLI zip from GitHub after consent, verifies its SHA-256 digest, installs atomically into
`~/.wakatime`, and verifies the binary with `--version`. It never downloads the CLI binary
automatically: the default flow prints the release, asset, destination and existing CLI and asks
for confirmation. Non-interactive runs require `--yes`; `--force` replaces an existing CLI, and
`--destination DIR`, `--version TAG` and `--json` are supported.

```bash
hermes wakatime install-cli            # show plan, then ask [y/N]
hermes wakatime install-cli --yes      # consented non-interactive install
hermes wakatime install-cli --version v2.28.4 --force --yes --destination ~/.wakatime
```

Supported release assets: Windows (amd64/arm64/386), macOS (amd64/arm64), Linux
(amd64/arm64/arm/386/riscv64), FreeBSD/NetBSD/OpenBSD where those assets exist.

## Install

This repository is a plugin source tree: `plugin.yaml`, `__init__.py` and the `wakatime_*.py`
modules must sit together inside a Hermes plugin directory named `wakatime`.

```bash
# Clone to Hermes' plugin directory (name it exactly wakatime)
git clone https://github.com/DarrylDias/hermes-wakatime-plugin.git ~/.hermes/plugins/wakatime

# On Windows, Hermes HOME is usually %LOCALAPPDATA%\hermes
# Clone/copy there too, for example:
#   C:\Users\<user>\AppData\Local\hermes\plugins\wakatime
```

Then enable the plugin and its toolset for the CLI platform:

```bash
hermes plugins enable wakatime
hermes tools enable wakatime --platform cli
hermes wakatime status
```

If you prefer to develop in another directory (for example `D:\src\hermes-wakatime-plugin`), point
the plugin directory at it with a Windows directory junction:

```cmd
mklink /J C:\Users\<user>\AppData\Local\hermes\plugins\wakatime D:\src\hermes-wakatime-plugin
```

Validate the registration without loading it into a live session:

```bash
hermes plugins doctor ~/.hermes/plugins/wakatime --ci
```

## Configuration

Plugin settings live under `plugins.entries.wakatime.settings` in `config.yaml` (or the Desktop
Plugins panel):

| Key | Default | Meaning |
|---|---|---|
| `oauth_token` | `""` | Personal WakaTime OAuth token for REST stats outside `today`. |
| `fallback_project` | `"hermes"` | Project name passed with `--alternate-project` when the CLI cannot detect one. |
| `min_heartbeat_interval_seconds` | `60` | Per-file rate limit for automatic heartbeats. |
| `track_file_edits` | `true` | Enable/disable automatic file-activity heartbeats. |
| `heartbeat_sync_limit` | `1` | Offline queue entries flushed alongside each heartbeat. `1` keeps hook latency low; `0` syncs all (slow). |

Environment overrides: `WAKATIME_CLI` (CLI path), `WAKATIME_OAUTH_TOKEN`, `WAKATIME_API_KEY`,
`WAKATIME_HOME`.

The plugin never writes `~/.wakatime.cfg`. The only CLI state it can modify is the stored API
backoff, and only through the explicit `hermes wakatime reset-backoff` command, which backs up
`~/.wakatime/wakatime-internal.cfg` first.

## Timezone note

`wakatime-cli --today` reports the **WakaTime account's local day**, not this PC's day. If the
account timezone differs (for example `Asia/Kolkata` while the machine is `America/New_York`), the
reported "today" can be empty right after the account-local midnight while the PC is still mid-day.
`wakatime_today` includes `account_timezone` and an explanatory note; `wakatime_stats` accepts
`today` only through that account-local path. For arbitrary date ranges use REST stats with an
OAuth token.

## Automatic tracking

The plugin registers `post_tool_call` and, for file-activity tools only, sends one heartbeat:

| Hermes tool | Kind | Heartbeat |
|---|---|---|
| `write_file` / `append_file` | write | `--write`, category `ai coding` |
| `patch` / `edit_file` / `apply_patch` | edit | category `ai coding`, `--ai-line-changes` computed from old/new text when available |
| `read_file` | read | category `ai coding`, no write flag |

It skips failed tool calls and non-existent files. Files are rate-limited per absolute path. Project
and language are auto-detected by `wakatime-cli`; a fallback project is used when detection fails.
When the API is rate-limited or a CLI backoff is active, the CLI queues heartbeats in
`~/.wakatime/offline_heartbeats.bdb`; `wakatime_offline` and `wakatime_sync` expose that queue.

## CLI examples

```bash
hermes wakatime status
hermes wakatime install-cli            # bootstrap the official CLI, with consent
hermes wakatime today --hide-categories
hermes wakatime offline --limit 50
hermes wakatime sync --limit 0 --timeout 300
hermes wakatime send ./src/main.py --category coding --write
hermes wakatime stats --range last_7_days      # needs OAuth token
hermes wakatime reset-backoff                  # prompts for confirmation
```

## Development

```bash
python -m unittest discover -s tests
hermes plugins doctor . --ci
```

## License

MIT.
