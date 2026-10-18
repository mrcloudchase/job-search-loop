"""Harness turns, and the rules every loop here shares: each turn is streamed, costed and logged; the run
stops at its deadline (the turn in flight is cancelled) or its budget (no new turn starts); and a turn's
files are checked by code, with the failures sent back word for word on the same conversation."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable

import httpx

from .term import BOLD, CYAN, DIM, GREEN, OFF, RED, clip, render, say, show_diff


class Api:
    """The calls a loop makes to harness-interface; nothing else knows its URLs."""

    def __init__(self, url: str, transport: httpx.BaseTransport | None = None):
        self.http = httpx.Client(base_url=url.rstrip("/"), timeout=httpx.Timeout(60, read=None), transport=transport)
        self.current: str | None = None                                     # the response in flight

    def turn(self, model: str, text: str, instructions: str, files: dict[str, str] | None = None,
             previous: str | None = None) -> dict:
        """One harness turn, streamed to the terminal as it happens. Returns the finished record."""
        body: dict = {"model": model, "stream": True, "instructions": instructions,
                      "input": [{"type": "input_text", "text": text},
                                *({"type": "input_file", "filename": k, "file_data": v} for k, v in (files or {}).items())]}
        if previous:
            body["previous_response_id"] = previous
        record, event = None, None
        with self.http.stream("POST", "/responses", json=body) as r:
            if r.status_code >= 400:
                r.read()
                raise RuntimeError(f"{model}: HTTP {r.status_code}: {r.text}")
            for line in r.iter_lines():
                if line.startswith("event: "):
                    event = line[7:]
                elif line.startswith("data: "):
                    data = json.loads(line[6:])
                    if event == "response.created":
                        self.current = data["response"]["id"]
                        say(f"{DIM}  {self.current} · {model} · streaming{OFF}")
                    elif event in ("harness.output", "harness.log"):
                        shown = render(event.split(".")[1], data["line"])
                        if shown:
                            say(shown)
                    elif event == "response.completed":
                        record = data["response"]
        self.current = None
        if record is None:
            raise RuntimeError(f"{model}: the stream ended without a record")
        return record

    def file(self, rid: str, path: str) -> str | None:
        r = self.http.get(f"/responses/{rid}/files/{path}")
        return r.text if r.status_code == 200 else None

    def listing(self, rid: str) -> list[str]:
        """Every file in the conversation's workspace as this turn left it, not only what the turn changed."""
        r = self.http.get(f"/responses/{rid}/files")
        return r.json() if r.status_code == 200 else []

    def diff(self, rid: str) -> str:
        r = self.http.get(f"/responses/{rid}/diff")
        return r.text if r.status_code == 200 else ""

    def cancel(self) -> None:
        if self.current:
            self.http.post(f"/responses/{self.current}/cancel")


def answer(record: dict) -> str:
    return "".join(c.get("text", "") for m in record.get("output", []) for c in m.get("content", []))


class Turns:
    """The turns of one run, under one deadline and one budget. Use it as a context manager: it arms the
    deadline, and Ctrl-C cancels the turn in flight and ends the run cleanly."""

    def __init__(self, api: Api, log: Path | str, minutes: float = 20, budget: float = 5.0):
        self.api, self.log = api, Path(log)
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self.budget, self.started = budget, time.time()
        self.deadline = self.started + minutes * 60
        self.spent, self.count, self.unpriced, self.stop = 0.0, 0, 0, ""    # unpriced: turns that reported no cost
        self.unpriced_tokens = 0                                            # what those turns used, as they reported it
        self.timer = threading.Timer(minutes * 60, api.cancel)              # at the deadline, stop the run in flight
        self.timer.daemon = True

    def __enter__(self) -> "Turns":
        self.timer.start()
        return self

    def __exit__(self, kind, error, trace) -> bool:
        self.timer.cancel()
        if kind is KeyboardInterrupt:
            self.api.cancel()
            self.stop = "interrupted"
            return True
        return False

    def stopped(self) -> str:
        return self.stop or ("time" if time.time() >= self.deadline else "budget" if self.spent >= self.budget else "")

    def banner(self, title: str) -> None:
        m, s = divmod(int(time.time() - self.started), 60)
        say(f"\n{BOLD}{CYAN}━━ {title} {'━' * max(3, 90 - len(title))} {m:02d}:{s:02d} · ${self.spent:.2f}{OFF}")

    def turn(self, phase: str, model: str, text: str, instructions: str, files: dict[str, str] | None = None,
             previous: str | None = None) -> dict | None:
        """One harness turn, logged and costed. None if the run is out of time or money, or the turn was cancelled."""
        if self.stopped():
            return None
        self.banner(f"{phase} · {model}")
        record = self.api.turn(model, text, instructions, files, previous)
        h = record["harness"]
        cost = h.get("cost_usd")
        self.spent += cost or 0.0
        self.count += 1
        self.unpriced += cost is None
        usage = record.get("usage") or {}
        self.unpriced_tokens += (usage.get("input_tokens", 0) + usage.get("output_tokens", 0)) if cost is None else 0
        say(f"{BOLD}  {record['status']}{OFF} · {h.get('latency_s')}s" + (f" · ${cost:.3f}" if cost else " · no cost reported")
            + (f" · changed {', '.join(h['files'])}" if h.get("files") else ""))
        diff = self.api.diff(record["id"])
        if diff.strip():
            show_diff(diff)
        with open(self.log, "a") as f:
            f.write(json.dumps({"phase": phase, "model": model, "response": record["id"], "status": record["status"],
                                "cost_usd": cost, "files": h.get("files", [])}) + "\n")
        if record["status"] == "cancelled":
            self.stop = "time"
            return None
        return record

    def files(self, record: dict, *names: str) -> dict[str, str | None]:
        """What a turn left in its workspace, as it left it: None for a file it didn't write."""
        return {n: self.api.file(record["id"], n) for n in names}

    def settle(self, record: dict, validate: Callable[[dict], list[str]], rounds: int, instructions: str,
               label: str = "FIX") -> dict | None:
        """Check a turn with `validate` (failures as messages; none means it passes) and send the failures back,
        word for word, on the same conversation, until it passes or `rounds` run out. Returns the record of the
        turn that passed, else None."""
        for attempt in range(rounds + 1):
            failures = validate(record)
            say(f"{BOLD}  verify:{OFF} " + (f"{GREEN}all checks pass{OFF}" if not failures else f"{RED}{len(failures)} failing{OFF}"))
            for f in failures:
                say(f"{RED}    ✗ {clip(f.splitlines()[0])}{OFF}")
            if not failures:
                return record
            if attempt == rounds:
                return None
            record = self.turn(f"{label} {attempt + 1}/{rounds}", record["model"],
                               "\n\n".join(failures), instructions, previous=record["id"])
            if record is None:
                return None
        return None

    def summary(self) -> str:
        return (f"{self.count} harness turns · ${self.spent:.2f} reported"
                + (f" ({self.unpriced} turns reported no cost, {self.unpriced_tokens:,} tokens)" if self.unpriced else "") + f" · log {self.log}")
