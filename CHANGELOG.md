# Changelog

All notable changes to this plugin are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-11-10

### Added

- `hermes wakatime install-cli` bootstrap: resolves the latest official WakaTime CLI release
  (or a pinned tag), maps the current OS/arch to the correct release zip, downloads it from GitHub,
  verifies the release's `checksums_sha256.txt` SHA-256 digest, installs atomically into
  `~/.wakatime`, and verifies the binary with `--version`.
- Explicit consent before downloading the CLI binary: interactive confirmation by default;
  non-interactive use requires `--yes`. `--force`, `--version`, `--destination` and `--json` are
  supported.
- Install hints in `wakatime_status` and `hermes wakatime status` when the CLI is missing.
- Unit tests for platform/asset mapping, checksum parsing, executable extraction from release
  archives, version normalization and installed-version matching.

## [0.1.0] - 2026-11-10

### Added

- Hermes native plugin manifest (`plugin.yaml`) with six tools, a `post_tool_call` hook and a
  `hermes wakatime` CLI command tree.
- `wakatime-cli` integration: discovery (`WAKATIME_CLI`, `~/.wakatime`, `PATH`), version check,
  today's dashboard, offline queue inspection, offline sync, and manual heartbeats.
- Automatic, rate-limited `ai coding` heartbeats for Hermes `write_file`, `patch` and
  `read_file` tool activity, with AI line-change counts from patch text.
- WakaTime REST stats support outside `today` through a personal OAuth token (plugin API keys are
  write-only), plus a clear error when no token is configured.
- API backoff detection from `wakatime-internal.cfg` and an explicit, backed-up
  `reset-backoff` recovery command.
- Account-timezone-aware today reporting, documented because `--today` follows the WakaTime
  account's timezone rather than the PC's.
- Pure-helper unit tests for line changes, heartbeat arguments, summaries, credential masking and
  tool-result gating.
- Report sub-minute durations accurately when wakatime-cli formats the dashboard total as
  "0 secs".
