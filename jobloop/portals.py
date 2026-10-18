"""Job portals, run as plain code. A portal is a name and two commands that print JSON or text: `search` and
`detail`. The loop runs them itself, never inside a harness, with no shell and without your API keys in their
environment. What they print is untrusted data: it is coerced to short text, and a url that isn't http(s)
is dropped. `portals import` finds the portal CLIs of a checkout of github.com/MadsLorentzen/ai-job-search
(MIT); any other command that prints the same JSON is a portal too."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .text import jobkey

LIMITS = {"title": 200, "company": 120, "location": 120, "date": 32, "deadline": 32, "url": 500, "id": 120, "description": 30000}
SECRETS = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", re.I)


@dataclass(frozen=True)
class Job:
    portal: str
    id: str
    title: str
    company: str
    location: str
    date: str
    deadline: str
    url: str
    description: str

    @property
    def key(self) -> str:
        return jobkey(self.company, self.title, self.url)


def normalize(raw: object, portal: str) -> Job | None:
    """A portal's result as a Job, or None if it is not a posting: no title, or no http(s) url."""
    if not isinstance(raw, dict):
        return None
    got = {k: " ".join(str(raw.get(k) or "").split())[:n] if k != "description" else str(raw.get(k) or "")[:n] for k, n in LIMITS.items()}
    if not got["title"] or not re.match(r"https?://", got["url"]):
        return None
    return Job(portal, **got)


def command(argv: list[str], **values: str) -> list[str]:
    """A portal's argv with its placeholders filled, one argument at a time: the values are never parsed."""
    return [re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)), a) for a in argv]


def run(argv: list[str], timeout: int = 90) -> str:
    """A portal command's output. No shell, no secrets in its environment, a time limit and a size limit."""
    env = {k: v for k, v in os.environ.items() if not SECRETS.search(k)}
    done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)
    if done.returncode != 0:
        raise RuntimeError((done.stdout + done.stderr).strip()[:300] or f"exit {done.returncode}")
    return done.stdout[:2_000_000]


def check_query(query: str) -> str:
    query = " ".join(query.split())
    if not query or query.startswith("-") or len(query) > 200:
        raise SystemExit(f"a search query is 1 to 200 characters and doesn't start with '-': {query[:40]!r}")
    return query


def search(portal: dict, query: str, limit: int, location: str = "") -> list[Job]:
    if "{location}" in " ".join(portal["search"]) and not location:
        raise RuntimeError("this portal needs --location")
    data = json.loads(run(command(portal["search"], query=check_query(query), limit=str(limit), location=location)))
    results = data.get("results") if isinstance(data, dict) else data
    return [j for j in (normalize(r, portal["name"]) for r in results) if j][:limit] if isinstance(results, list) else []


def detail(portal: dict, job: Job) -> str:
    """A posting's full text, from the portal's detail command."""
    return run(command(portal["detail"], id=job.id, url=job.url)).strip()


def registry(me: Path | str) -> list[dict]:
    path = Path(me) / "portals.json"
    return json.loads(path.read_text()) if path.exists() else []


def save(me: Path | str, portals: list[dict]) -> None:
    Path(me).mkdir(parents=True, exist_ok=True)
    (Path(me) / "portals.json").write_text(json.dumps(portals, indent=2) + "\n")


def discover(skills: Path | str) -> list[dict]:
    """The portal CLIs in a checkout's .agents/skills folder, all disabled: you opt in to each."""
    found = []
    for cli in sorted(Path(skills).glob("*/cli/src/cli.ts")):
        folder = cli.parent.parent.parent
        sources = "".join(p.read_text(errors="replace") for p in cli.parent.rglob("*.ts"))
        search_argv = ["bun", "run", str(cli.resolve()), "search", "--query", "{query}", "--limit", "{limit}", "--format", "json"]
        found.append({"name": folder.name.removesuffix("-search"), "enabled": False,
                      "search": search_argv + (["--location", "{location}"] if "NO_LOCATION" in sources else []),
                      "detail": ["bun", "run", str(cli.resolve()), "detail", "{id}", "--format", "plain"]})
    return found


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--me", default="me", help="your folder (gitignored)")
    sub = p.add_subparsers(dest="action", required=True)
    sub.add_parser("list", help="the portals you have, and which are enabled")
    s = sub.add_parser("import", help="find the portal CLIs in a checkout of ai-job-search")
    s.add_argument("skills", help="its .agents/skills folder")
    for name in ("enable", "disable"):
        sub.add_parser(name, help=f"{name} a portal").add_argument("name")


def main(args: argparse.Namespace) -> int:
    portals = registry(args.me)
    if args.action == "import":
        have = {p["name"] for p in portals}
        portals += [p for p in discover(args.skills) if p["name"] not in have]
    elif args.action in ("enable", "disable"):
        match = [p for p in portals if p["name"] == args.name]
        if not match:
            raise SystemExit(f"no portal {args.name!r}; `portals list` shows yours")
        match[0]["enabled"] = args.action == "enable"
    if args.action != "list":
        save(args.me, portals)
    for p in portals:
        print(f"{'on ' if p['enabled'] else 'off'} {p['name']}")
    return 0
