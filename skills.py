"""Skill registry and system-prompt assembly."""
from __future__ import annotations

from datetime import datetime

try:
    import yaml
except ImportError:  # optional: only used to parse SKILL.md frontmatter
    yaml = None

from config import SKILLS_DIR, WORKDIR
from mcp import mcp_clients


# ── Skill Loading ──

SKILL_REGISTRY: dict[str, dict] = {}


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    if yaml is None:
        return {}, parts[2].strip()
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        meta = {}
    return meta, parts[2].strip()


def scan_skills():
    SKILL_REGISTRY.clear()
    if not SKILLS_DIR.exists():
        return
    for directory in sorted(SKILLS_DIR.iterdir()):
        if not directory.is_dir():
            continue
        manifest = directory / "SKILL.md"
        if not manifest.exists():
            continue
        raw = manifest.read_text()
        meta, _ = _parse_frontmatter(raw)
        name = meta.get("name", directory.name)
        desc = meta.get("description", raw.split("\n")[0].lstrip("#").strip())
        SKILL_REGISTRY[name] = {
            "name": name,
            "description": desc,
            "content": raw,
        }


def list_skills() -> str:
    if not SKILL_REGISTRY:
        return "(no skills found)"
    return "\n".join(
        f"- {skill['name']}: {skill['description']}"
        for skill in SKILL_REGISTRY.values())


def load_skill(name: str) -> str:
    skill = SKILL_REGISTRY.get(name)
    if not skill:
        available = ", ".join(SKILL_REGISTRY.keys()) or "(none)"
        return f"Skill not found: {name}. Available: {available}"
    return skill["content"]



# ── Prompt Assembly ──

PROMPT_SECTIONS = {
    "identity": "You are a coding agent. Act, don't explain.",
    "tools": ("Primary coding tools: bash, read_file, write_file, edit_file, "
              "glob, load_skill, compact. "
              "Use todo_write only when a plan is genuinely useful. "
              "Advanced tools (task graph, teammate, worktree, cron, MCP) "
              "are available only when the user explicitly needs that "
              "capability. MCP tools are prefixed mcp__{server}__{tool}."),
    "efficiency": ("Minimize round trips: use glob to inspect a folder, "
                   "combine independent tool calls in one "
                   "response, and prefer a single bash command over a chain "
                   "of small ones."),
    "workspace": f"Working directory: {WORKDIR}",
    "memory": "Relevant memories are injected below when available.",
}


def assemble_system_prompt(context: dict) -> str:
    # The system prompt is rebuilt each turn from live context. This is where
    # memory, skill catalog, MCP state, and active teammates become visible.
    sections = [PROMPT_SECTIONS["identity"],
                PROMPT_SECTIONS["tools"],
                PROMPT_SECTIONS["efficiency"],
                PROMPT_SECTIONS["workspace"]]
    sections.append(f"Current date: {datetime.now().date().isoformat()}")
    sections.append("Skills catalog:\n" + list_skills() +
                    "\nUse load_skill(name) when a skill is relevant.")
    if context.get("memories"):
        sections.append(f"Relevant memories:\n{context['memories']}")
    if context.get("working_state"):
        sections.append("Current turn state (informational; do not create or "
                        "update todos unless useful):\n" +
                        context["working_state"])
    mcp_names = list(mcp_clients.keys())
    if mcp_names:
        sections.append(f"Connected MCP servers: {', '.join(mcp_names)}")
    return "\n\n".join(sections)
