#!/usr/bin/env python3
"""
s20: Comprehensive Agent — all teaching components in one loop.

Run:  python main.py
Need: pip install -r requirements.txt
      + .env with ANTHROPIC_API_KEY and MODEL_ID (see .env.example)

This package is a modular split of the original monolithic s20 agent. The
entry point is main.py; each subsystem lives in its own module (tasks,
skills, tools, teammates, context, cron, mcp, toolpool, agent, ...) sharing
runtime state from config.py.

This final chapter intentionally puts the earlier teaching mechanisms back
together: dispatch, permission, hooks, todo, subagent, skills, compaction,
memory, prompt assembly, error recovery, task graph, background tasks, cron,
teams, protocols, autonomous agents, worktrees, and MCP.
"""

from __future__ import annotations

import ast, json, os, subprocess, time, random, threading, re
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, asdict, field

try:
    import yaml
except ImportError:  # optional: only used to parse SKILL.md frontmatter
    yaml = None

try:
    import readline
    readline.parse_and_bind('set bind-tty-special-chars off')
    READLINE_AVAILABLE = True
except ImportError:
    READLINE_AVAILABLE = False

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None  # type: ignore[assignment]

try:
    from dotenv import load_dotenv
    # Keep API configuration tied to the agent installation when main.py uses
    # --workdir to operate on a different repository. If no local file exists,
    # python-dotenv retains its normal discovery behavior.
    _agent_env = Path(__file__).resolve().with_name(".env")
    load_dotenv(dotenv_path=_agent_env if _agent_env.exists() else None,
                override=True)
except ImportError:  # optional: env vars can be exported directly instead
    load_dotenv = None

if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

WORKDIR = Path.cwd()


def _check_runtime() -> None:
    """Fail fast with actionable messages instead of a mid-import traceback."""
    if Anthropic is None:
        raise SystemExit(
            "Missing required dependency: anthropic\n"
            "Install it with: pip install -r requirements.txt")
    required_env = {
        "ANTHROPIC_API_KEY": "Your Anthropic API key (https://console.anthropic.com/)",
        "MODEL_ID": "Model name, e.g. claude-sonnet-4-5 or claude-3-5-sonnet-20241022",
    }
    missing = [name for name in required_env if not os.getenv(name)]
    if missing:
        raise SystemExit(
            "\n".join(f"Missing env var {name}: {hint}"
                      for name, hint in required_env.items()
                      if name in missing)
            + "\nCopy .env.example to .env and fill it in, "
              "or export the variables before running.")


_check_runtime()

client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.getenv("MODEL_ID", "")
PRIMARY_MODEL = MODEL
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL_ID")

SKILLS_DIR = WORKDIR / "skills"
TRANSCRIPT_DIR = WORKDIR / ".transcripts"
TOOL_RESULTS_DIR = WORKDIR / ".task_outputs" / "tool-results"

# Keep the normal agent response budget comfortably below DeepSeek V4 Flash's
# 384k hard output limit.  A truncated response may retry with a larger budget,
# but the recovery value should still leave ample room for the input context.
DEFAULT_MAX_TOKENS = 32000
ESCALATED_MAX_TOKENS = 128000
MODEL_CONTEXT_TOKENS = 1_000_000
MODEL_MAX_OUTPUT_TOKENS = 384_000
CONTEXT_COMPACT_THRESHOLD = 800_000
CONTEXT_SAFETY_MARGIN = 16_000
MIN_OUTPUT_TOKENS = 8_000
SUMMARY_MAX_TOKENS = 8_000
SUMMARY_KEEP_MESSAGES = 20
MAX_RETRIES = 5
MAX_CONSECUTIVE_529 = 2
MAX_RECOVERY_RETRIES = 2
BASE_DELAY_MS = 1000
# Outputs above 10k chars are offloaded to .task_outputs/tool-results/ by
# tool_result_budget (kept in sync with its max_bytes so the layer can shrink
# oversized single results).
PERSIST_THRESHOLD = 10000
CONTINUATION_PROMPT = "Continue from the previous response. Do not repeat completed work."
PROMPT = "\033[36ms20 >> \033[0m"
CLI_ACTIVE = False


def terminal_print(text: str):
    if threading.current_thread() is threading.main_thread() or not CLI_ACTIVE:
        print(text)
        return
    line = ""
    if READLINE_AVAILABLE:
        try:
            line = readline.get_line_buffer()
        except Exception:
            line = ""
    print(f"\r\033[K{text}")
    print(PROMPT + line, end="", flush=True)
