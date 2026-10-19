"""A person's own job board, built by a harness, run here as a separate program.

A board lives in `<me>/boards/<name>/` (`board.json`, `adapter.py`, `samples/`) and is known to the loop only if
`<me>/boards.json` has an entry for it marked `"external": true` with the digest of the `adapter.py` that was verified
(jobloop/addboard.py writes it, with `"enabled": false`). `ExternalBoard` then has the shape of a built-in adapter
(`NAME`, `HOSTS`, `DEFAULT_ENABLED`, `TERMS`, `search(query, http)`, `detail(ref, http)`), so jobloop/search.py runs it like any other.

What it promises:
- The adapter is NEVER imported and never runs inside a harness. Each call is a subprocess: an argv list (no shell), the
  loop's own interpreter in isolated mode, an empty scratch folder as its working directory, a time limit and an output size
  limit. Its environment holds only PATH, HOME, TMPDIR and LANG: no API key, token or proxy setting of the loop's reaches it.
- An adapter whose file no longer has the digest that was verified is not run.
- Its stdout is untrusted JSON. Every posting goes through `contract.result` (so a url that is not http(s) drops the posting),
  and a posting whose url is not https on one of the board's declared hosts is dropped too. A failure it reports on stderr
  (`NAME: kind: message`, exit 1) becomes a `BoardError` of that kind; anything else (a crash, bad JSON, too much output,
  too long) is a `parse` or `network` error, never a result.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .. import archive
from .contract import KINDS, BoardError, Query, result

BOARDS_DIR = "boards"
NAME = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
MAX_NAME = 40
TIME_LIMIT = 120.0
MAX_OUTPUT = 4_000_000
MAX_STDERR = 4_000
ENVIRONMENT = ("PATH", "LANG")                  # all that is passed on from the loop's own environment


def folder(me: Path | str, name: str) -> Path:
    if not NAME.fullmatch(name) or len(name) > MAX_NAME:
        raise ValueError(f"{name!r} is not a board name: lower-case words joined by hyphens, at most {MAX_NAME} characters")
    return Path(me) / BOARDS_DIR / name


def digest_of(adapter: Path) -> str:
    return hashlib.sha256(adapter.read_bytes()).hexdigest()


def read_registry(me: Path | str) -> dict:
    """The `boards` object of <me>/boards.json ({} if there is no file). Refuses a file that is not a board settings file."""
    file = Path(me) / "boards.json"
    if not file.exists() or not file.read_text(encoding="utf-8").strip():
        return {}
    try:
        boards = json.loads(file.read_text(encoding="utf-8")).get("boards")
    except (ValueError, AttributeError):
        boards = None
    if not isinstance(boards, dict) or not all(isinstance(v, dict) for v in boards.values()):
        raise ValueError(f"{file} is not a board settings file ({{\"boards\": {{name: {{...}}}}}}); move it aside or repair it")
    return boards


def names(me: Path | str) -> tuple[str, ...]:
    """The external boards registered for this person, sorted."""
    return tuple(sorted(n for n, entry in read_registry(me).items() if entry.get("external") is True and NAME.fullmatch(n)))


def register(me: Path | str, name: str, digest: str, verified_on: str) -> None:
    """Record a verified board, switched OFF. Never changes an entry that is already there."""
    file = Path(me) / "boards.json"
    with archive.locked(file):
        boards = read_registry(me)
        if name in boards:
            raise ValueError(f"{name} already has an entry in {file}")
        boards[name] = {"enabled": False, "external": True, "digest": digest, "verified_on": verified_on}
        archive.write_atomically(file, (json.dumps({"boards": boards}, indent=1, sort_keys=True) + "\n").encode())


@dataclass(frozen=True)
class ExternalBoard:
    """A registered external board, with the attributes of a built-in adapter."""
    NAME: str
    HOSTS: tuple[str, ...]
    TERMS: str
    adapter: Path
    digest: str
    DEFAULT_ENABLED: bool = False
    TIMEOUT: float = TIME_LIMIT
    RETRIES: int = 0

    def search(self, query: Query, http=None) -> list[dict]:
        """Run the adapter's `search`. `http` is unused: the adapter makes its own requests."""
        argv = ["search", f"--query={query.text}", f"--location={query.location}", f"--limit={query.limit}"]
        if query.recency_days is not None:
            argv.append(f"--recency-days={query.recency_days}")
        return self._run(argv)

    def detail(self, ref: str, http=None) -> dict:
        if ref.startswith("-") or not ref.strip():
            raise BoardError("invalid", f"a posting reference does not start with '-' or stay empty: {ref[:40]!r}")
        rows = self._run(["detail", ref])
        if not rows:
            raise BoardError("parse", f"{self.NAME} returned no posting for {ref[:80]!r}")
        return rows[0]

    def _run(self, args: list[str]) -> list[dict]:
        if digest_of(self.adapter) != self.digest:
            raise BoardError("invalid", f"{self.adapter} changed after it was verified; it is not run")
        return postings(self.NAME, self.HOSTS, execute([sys.executable, "-I", str(self.adapter), *args]))


def load_board(me: Path | str, name: str) -> ExternalBoard:
    """The registered board `name`, from its folder. Refuses a name that is not registered as external, or a damaged board.json."""
    entry = read_registry(me).get(name)
    if not NAME.fullmatch(name) or not entry or entry.get("external") is not True:
        raise ValueError(f"no built-in board {name!r} and no external board of that name is registered")
    base = folder(me, name)
    try:
        spec = json.loads((base / "board.json").read_text(encoding="utf-8"))
        hosts, terms = tuple(spec["hosts"]), spec["terms"]
        valid = spec["name"] == name and hosts and all(isinstance(h, str) for h in hosts) and isinstance(terms, str)
    except (OSError, ValueError, KeyError, TypeError):
        valid = False
    if not valid:
        raise ValueError(f"{base / 'board.json'} is missing or damaged; add the board again")
    if not isinstance(entry.get("digest"), str):
        raise ValueError(f"{name} has no recorded digest of its verified adapter; add the board again")
    return ExternalBoard(name, hosts, terms, base / "adapter.py", entry["digest"])


def execute(argv: list[str], *, limit: float = TIME_LIMIT, max_output: int = MAX_OUTPUT) -> str:
    """Run an adapter's argv as a subprocess with a scrubbed environment and limits; its stdout, or a BoardError."""
    with tempfile.TemporaryDirectory(prefix="board-") as scratch, tempfile.TemporaryFile() as errors:
        env = {k: os.environ[k] for k in ENVIRONMENT if k in os.environ}
        env.update(HOME=scratch, TMPDIR=scratch)
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors, env=env, cwd=scratch)
        late = threading.Event()
        timer = threading.Timer(limit, lambda: (late.set(), proc.kill()))
        timer.start()
        try:
            out = proc.stdout.read(max_output + 1)
            if len(out) > max_output:
                proc.kill()
            code = proc.wait()
        finally:
            timer.cancel()
            proc.kill()
            proc.stdout.close()
        errors.seek(0)
        said = errors.read(MAX_STDERR).decode("utf-8", "replace").strip()
    if len(out) > max_output:
        raise BoardError("parse", f"the adapter printed more than {max_output} bytes")
    if late.is_set():
        raise BoardError("network", f"the adapter did not finish within {limit:g} seconds")
    if code != 0:
        raise failure(said, code)
    return out.decode("utf-8", "replace")


def failure(said: str, code: int) -> BoardError:
    """The BoardError an adapter's stderr line (`NAME: kind: message`) stands for."""
    last = said.splitlines()[-1] if said else ""
    found = re.match(r"[\w-]+: (\w+): (.*)", last)
    if code == 1 and found and found.group(1) in KINDS:
        return BoardError(found.group(1), found.group(2)[:300])
    return BoardError("parse", f"the adapter failed (exit {code}): {last[:300] or 'no message'}")


def postings(name: str, hosts: tuple[str, ...], text: str) -> list[dict]:
    """The postings in an adapter's untrusted stdout: contract shape, https, declared hosts only; the rest are dropped."""
    try:
        body = json.loads(text)
        rows = body["results"]
    except (ValueError, KeyError, TypeError):
        raise BoardError("parse", f"{name} did not print the contract's JSON") from None
    if not isinstance(rows, list):
        raise BoardError("parse", f"{name}: results is not a list")
    found = []
    for raw in rows:
        row = result(raw) if isinstance(raw, dict) else None
        if row and on_hosts(row["url"], hosts):
            found.append(row)
    return found


def on_hosts(url: str, hosts: tuple[str, ...]) -> bool:
    try:
        parts = urlparse(url)
        return (parts.scheme == "https" and (parts.hostname or "").lower() in hosts and not parts.username
                and not parts.password and parts.port in (None, 443))
    except ValueError:
        return False
