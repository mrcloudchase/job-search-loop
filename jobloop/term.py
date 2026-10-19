"""The terminal: colours, and how a harness's own stream and a turn's diff are shown to a person watching."""
from __future__ import annotations

import json

BOLD, DIM, RED, GREEN, YELLOW, CYAN, OFF = (f"\033[{c}m" for c in (1, 2, 31, 32, 33, 36, 0))


def say(text: str = "", color: str = "") -> None:
    print(f"{color}{text}{OFF}" if color else text, flush=True)


def clip(text: str, width: int = 110) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[: width - 1] + "…"


def render(kind: str, line: str) -> str | None:
    """One line of a harness's own stream: the text it writes and the tools it calls, from the CLIs'
    JSON events; a line that isn't JSON as printed. Everything else is in `hi output`."""
    if kind == "log":
        return f"{DIM}  ! {clip(line)}{OFF}" if line.strip() else None
    try:
        event = json.loads(line)
    except ValueError:
        return f"  {clip(line)}" if line.strip() else None
    if not isinstance(event, dict):
        return None
    parts = []
    for block in (event.get("message") or {}).get("content") or []:         # Claude Code and kin
        if block.get("type") == "text" and block.get("text", "").strip():
            parts.append(f"  {clip(block['text'])}")
        elif block.get("type") == "tool_use":
            arg = block.get("input", {})
            parts.append(f"{YELLOW}  ⚙ {block.get('name')}{OFF} {clip(arg.get('command') or arg.get('file_path') or arg.get('pattern') or '')}")
    item = event.get("item") or {}                                          # Codex
    if item.get("type") == "agent_message" and event.get("type") == "item.completed":
        parts.append(f"  {clip(item.get('text', ''))}")
    elif item.get("type") == "command_execution" and event.get("type") == "item.started":
        parts.append(f"{YELLOW}  ⚙ shell{OFF} {clip(item.get('command', ''))}")
    return "\n".join(parts) or None


def show_diff(diff: str) -> None:
    for line in diff.splitlines()[:60]:
        color = (BOLD if line.startswith(("diff --git", "+++", "---")) else GREEN if line.startswith("+")
                 else RED if line.startswith("-") else CYAN if line.startswith("@@") else DIM)
        say(f"  {line}"[:140], color)
