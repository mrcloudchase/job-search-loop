"""Register the role harnesses and the research sandbox with a running harness-interface server.

A role is a copy of a shipped harness under a new name, with its own CLI settings (an `artifacts` file in the harness
folder) and its own network (a sandbox). The copies are derived from the installed harness at registration time, so
nothing of harness-interface is copied into this repository:

- `claude-reviewer`: Claude Code that can't write or edit files and can't use the web. It reads the drafts and answers.
- `claude-research`: Claude Code that can't write or edit files, on the `research` sandbox, which reaches only its provider
  until the loop widens it to one company's hosts for one research turn (jobloop/research.py does that).
- `claude-portal`: Claude Code that MAY write files (it writes a job-board adapter and its samples) and can fetch pages, on
  the same `research` sandbox, which jobloop/addboard.py widens to exactly one board's hosts for the build turn.

    python tools/register_harnesses.py [--server URL] [--provider NAME]

`--provider` points every derived copy at a provider that serves the same API shape (e.g. `deepseek`, from
harness-interface's providers docs), which is how Claude Code runs where there is no Anthropic key.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import httpx

ROLES_DIR = Path(__file__).resolve().parent.parent / "roles"
ROLES = {                                       # name: (shipped harness it derives from, settings file, sandbox)
    "claude-reviewer": ("claude-code", "claude-reviewer.settings.json", "default"),
    "claude-research": ("claude-code", "claude-research.settings.json", "research"),
    "claude-portal": ("claude-code", "claude-portal.settings.json", "research"),
}
SETTINGS_DEST = ".claude/settings.json"         # where Claude Code reads its settings, under the harness's $HOME


def default_tag(default_dockerfile: str) -> str:
    """The tag harness-interface gives the default sandbox's image: a hash of its Dockerfile."""
    return hashlib.sha256(default_dockerfile.encode()).hexdigest()[:12]


def derive(base: dict, name: str, settings: str, sandbox: str, provider: str | None) -> dict:
    """A harness body (as PUT /harnesses/{name} takes it) that is `base` with its own name, sandbox and settings."""
    scripts = dict(base.get("scripts", {}))
    scripts["settings.json"] = settings
    artifacts = scripts.get("artifacts", "").rstrip("\n")
    scripts["artifacts"] = (artifacts + "\n" if artifacts else "") + f"settings.json {SETTINGS_DEST}\n"
    return {**base, "name": name, "sandbox": sandbox, "scripts": scripts, **({"provider": provider} if provider else {})}


def register(api: httpx.Client, provider: str | None = None) -> list[str]:
    done = []
    default = api.get("/sandboxes/default").raise_for_status().json()
    api.put("/sandboxes/research", json={"name": "research", "egress": [],
                                         "dockerfile": f"FROM harness-interface/default:{default_tag(default['dockerfile'])}\n"}).raise_for_status()
    done.append("sandbox research (egress: its provider only)")
    for name, (base_name, settings_file, sandbox) in ROLES.items():
        base = api.get(f"/harnesses/{base_name}").raise_for_status().json()
        body = derive(base, name, (ROLES_DIR / settings_file).read_text(), sandbox, provider)
        api.put(f"/harnesses/{name}", json=body).raise_for_status()
        done.append(f"harness {name} (from {base_name}, provider {body['provider']}, sandbox {sandbox})")
    return done


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--server", default="http://127.0.0.1:8000")
    p.add_argument("--provider", help="point the derived harnesses at this provider instead of the base harness's")
    args = p.parse_args()
    with httpx.Client(base_url=args.server.rstrip("/"), timeout=30) as api:
        for line in register(api, args.provider):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
