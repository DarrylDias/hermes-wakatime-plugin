"""Hermes WakaTime plugin: coding-time analytics integrated through wakatime-cli.

Wiring goes through the public plugin API: ctx.register_tool, ctx.register_hook and
ctx.register_cli_command. No Hermes core or private modules are imported.
"""

from __future__ import annotations

import logging
from functools import partial

from . import wakatime_cli, wakatime_schemas as s, wakatime_tools as t

logger = logging.getLogger(__name__)

TOOLSET = "wakatime"
PLUGIN_VERSION = "0.2.0"


def register(ctx) -> None:
    """Plugin entry point called by the Hermes plugin loader."""
    specs = (
        (s._STATUS, partial(t.handle_status, plugin_ctx=ctx), "⏱️"),
        (s._TODAY, partial(t.handle_today, plugin_ctx=ctx), "📈"),
        (s._HEARTBEAT, partial(t.handle_heartbeat, plugin_ctx=ctx), "💓"),
        (s._OFFLINE, partial(t.handle_offline, plugin_ctx=ctx), "🗄️"),
        (s._SYNC, partial(t.handle_sync, plugin_ctx=ctx), "🔄"),
        (s._STATS, partial(t.handle_stats, plugin_ctx=ctx), "📊"),
    )
    for schema, handler, emoji in specs:
        ctx.register_tool(
            name=schema["name"],
            toolset=TOOLSET,
            schema=schema,
            handler=handler,
            emoji=emoji,
        )

    def _on_post_tool_call(tool_name, args, result, task_id=None, duration_ms=None, **kwargs):
        t.handle_post_tool_call(
            tool_name,
            args,
            result,
            task_id=task_id,
            duration_ms=duration_ms,
            plugin_ctx=ctx,
            **kwargs,
        )

    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_cli_command(
        name="wakatime",
        help="WakaTime coding-time tracking and stats",
        description="Inspect Hermes/WakaTime tracking, sync offline heartbeats, send heartbeats and read stats.",
        setup_fn=wakatime_cli.setup_cli,
        handler_fn=wakatime_cli.handle_cli,
    )
    logger.info("wakatime plugin registered: 6 tools, 1 hook, `hermes wakatime` CLI")


__all__ = ["register"]
