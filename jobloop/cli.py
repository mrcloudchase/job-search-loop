"""The person's surface: setup, run, status, queue, tracker, stage, proposals, playbook, config, harnesses.

Everything else the product does is the loop's work (`jobloop run`). These commands are the few things a person does: build the
profile, start or schedule the loop, read what it left, mark what they did, decide what it proposes.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

from . import archive, profile as profiles, tracker
from .engine import config as configuration, digest, playbook, policy, proposals, queue, runner, snapshot
from .engine.store import Store
from .term import BOLD, DIM, GREEN, OFF, RED, say
from .text import utf8_io

ROOT = Path(__file__).resolve().parent.parent


def _me(args) -> Path:
    return Path(args.me)


def _every(text: str) -> float:
    """'90m', '2h', '1d' or a bare number of minutes -> seconds."""
    text = text.strip().lower()
    scale = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if text[-1:] in scale:
        return float(text[:-1]) * scale[text[-1]]
    return float(text) * 60


# ── run ──
def cmd_run(args) -> int:
    from .stages import all_stages
    me = _me(args)
    config = configuration.load(me)
    if args.minutes:
        config.pass_minutes = args.minutes
    if args.budget:
        config.pass_budget_usd = args.budget
    config.validate()
    stages = all_stages(strict=True)
    if args.stage:
        unknown = sorted(set(args.stage) - {s.spec.name for s in stages})
        if unknown:
            say(f"{RED}unknown stage: {', '.join(unknown)}{OFF}")
            return 2
        stages = [s for s in stages if s.spec.name in args.stage]
    if args.dry_run:
        return _dry_run(me, stages, config)
    code = 0
    while True:
        result = runner.run_pass(me, stages, config=config, mode="watch" if args.watch else "once", playbook_loader=playbook.load)
        code = 2 if result.terminal == "blocked" else 0
        if not args.watch:
            return code
        say(f"{DIM}next pass in {args.every}{OFF}")
        try:
            time.sleep(_every(args.every))
        except KeyboardInterrupt:
            return code


def _dry_run(me: Path, stages, config) -> int:
    """What the next pass would consider, in the order the policy would take it. No model call, no network request."""
    with Store(me) as store:
        snap = snapshot.take(me, store, config)
        found = policy.candidates(snap, stages)
        found.sort(key=lambda p: (policy.urgency(p[0], snap.today), -p[1].rank, -p[0].priority, p[0].key))
        say(f"{BOLD}A pass would consider {len(found)} actions{OFF} (the first goes first):")
        for action, spec in found[:40]:
            say(f"  {spec.name:10} {action.key[:50]:50} {action.reason[:70]}")
        for problem in snap.problems:
            say(f"{RED}  problem: {problem}{OFF}")
    return 0


# ── status, queue, proposals, playbook, config ──
def cmd_status(args) -> int:
    me = _me(args)
    config = configuration.load(me)
    with Store(me) as store:
        say(digest.status(me, store, snapshot.take(me, store, config)))
    return 0


def cmd_queue(args) -> int:
    me = _me(args)
    with Store(me) as store:
        if args.action == "list":
            say(queue.list_text(store))
        elif args.action == "show":
            say(queue.show_text(me, store, args.key))
        else:
            say(queue.done(store, args.key))
    return 0


def _describe(p: dict, full: bool = False) -> str:
    line = f"#{p['id']} [{p['kind']}] {p['summary']}"
    if p.get("note"):
        line += f"\n    last refusal: {p['note']}"
    if full:
        for k, v in sorted(p["payload"].items()):
            if k != "summary":
                line += f"\n    {k}: {v}"
    return line


def cmd_proposals(args) -> int:
    me = _me(args)
    now = datetime.now(timezone.utc)
    with Store(me) as store:
        pending = proposals.list_pending(store)
        if args.action == "list":
            say("\n".join(_describe(p) for p in pending) if pending else "No proposals waiting.")
            return 0
        mine = next((p for p in pending if p["id"] == args.id), None)
        if args.action == "show":
            say(_describe(mine, full=True) if mine else f"no pending proposal #{args.id}")
            return 0 if mine else 2
        if args.action == "reject":
            result = proposals.reject(store, args.id, now, args.note)
        elif mine and mine["kind"] == "stale_resolution":
            from .stages import maintain
            choice = args.choice or "keep"
            try:
                maintain.accept_stale(me, store, mine["payload"]["key"], choice, now.date(), proposal_id=args.id, now=now)
                result = proposals.Result(True, f"{mine['payload']['key']}: {choice}")
            except (ValueError, LookupError) as error:
                result = proposals.Result(False, str(error))
        else:
            result = proposals.accept(me, store, args.id, now)
        say((GREEN if result.ok else RED) + (result.reason or ("done" if result.ok else "refused")) + OFF)
        if result.applied:
            say(f"added to your profile: {result.applied}")
        return 0 if result.ok else 2


def cmd_playbook(args) -> int:
    me = _me(args)
    with Store(me) as store:
        if args.action == "show":
            pb = playbook.load(me, store)
            say(f"Playbook v{pb.version}\nweights: {pb.weights}\nstyle rules:\n{pb.style_addendum() or '  (none)'}\n"
                f"extra queries: {pb.extra_queries or '(none)'}\ndropped queries: {pb.dropped_queries or '(none)'}\nstrategy: {pb.strategy}")
            for v in store.playbook_versions():
                say(f"  v{v['version']} {v['status']:11} {v['note'] or ''}")
        else:
            back = playbook.rollback(me, store, args.reason or "rolled back by you", datetime.now(timezone.utc))
            say(f"Playbook v{back.version} is active again." if back else "Already on the first version.")
    return 0


def cmd_config(args) -> int:
    config = configuration.load(_me(args))
    say(config.as_text())
    return 0


def cmd_stage(args) -> int:
    """Log an interview stage for an application, so the loop can prepare for it and learn from it."""
    on = tracker.parse_date(args.date) if args.date else date.today()
    if on is None:
        say("--date must be YYYY-MM-DD")
        return 2
    try:
        archive.record_stage(_me(args), args.key, on=on, stage=args.stage, format=args.format, people=tuple(args.people or ()),
                             feedback=args.feedback, outcome=args.outcome)
    except (ValueError, archive.ArchiveRefused, OSError) as error:
        say(str(error))
        return 2
    say(f"{args.key}: stage {args.stage} logged")
    return 0


def cmd_setup(args) -> int:
    return profiles.main(args)


def cmd_harnesses(args) -> int:
    script = ROOT / "tools" / "register_harnesses.py"
    if not script.exists():
        say("tools/register_harnesses.py is part of the repository checkout; run this from it")
        return 2
    cmd = [sys.executable, str(script), "--server", args.server] + (["--provider", args.provider] if args.provider else [])
    return subprocess.run(cmd).returncode


# ── parser ──
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobloop", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    me = lambda p: p.add_argument("--me", default="me", help="your gitignored folder (default: me)")

    p = sub.add_parser("setup", help="build and confirm your profile (the one thing the loop waits for)")
    profiles.add_arguments(p)
    p.set_defaults(fn=cmd_setup)

    p = sub.add_parser("run", help="run the loop: one pass, or passes on a cadence")
    me(p)
    p.add_argument("--watch", action="store_true", help="keep running passes")
    p.add_argument("--every", default="60m", help="with --watch: time between passes (e.g. 30m, 2h)")
    p.add_argument("--dry-run", action="store_true", help="show what a pass would consider; no model call, no network request")
    p.add_argument("--stage", action="append", help="run only this stage (repeatable)")
    p.add_argument("--minutes", type=float, help="override the pass deadline in loop.json")
    p.add_argument("--budget", type=float, help="override the pass dollar ceiling in loop.json")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("status", help="what the loop sees and what is waiting for you")
    me(p)
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("queue", help="the applications ready for you to submit")
    me(p)
    qs = p.add_subparsers(dest="action", required=True)
    qs.add_parser("list")
    qs.add_parser("show").add_argument("key")
    qs.add_parser("done", help="you handled it (this does NOT mark it applied: use tracker set)").add_argument("key")
    p.set_defaults(fn=cmd_queue)

    p = sub.add_parser("tracker", help="your side of the tracker: mark what you did (applied, interview, offer, ...)")
    tracker.add_arguments(p)
    p.set_defaults(fn=tracker.main)

    p = sub.add_parser("stage", help="log an interview stage for an application")
    me(p)
    p.add_argument("key")
    p.add_argument("stage", help="e.g. phone_screen, technical, final_round")
    p.add_argument("--date")
    p.add_argument("--format", default="", help="phone, video or onsite")
    p.add_argument("--people", action="append")
    p.add_argument("--feedback", default="")
    p.add_argument("--outcome", default="pending", choices=archive.STAGE_OUTCOMES)
    p.set_defaults(fn=cmd_stage)

    p = sub.add_parser("proposals", help="what the loop proposes and only you can decide")
    me(p)
    ps = p.add_subparsers(dest="action", required=True)
    ps.add_parser("list")
    ps.add_parser("show").add_argument("id", type=int)
    ac = ps.add_parser("accept")
    ac.add_argument("id", type=int)
    ac.add_argument("--choice", help="for a stale application: no_response, withdrawn or keep")
    r = ps.add_parser("reject")
    r.add_argument("id", type=int)
    r.add_argument("--note", default="")
    p.set_defaults(fn=cmd_proposals)

    p = sub.add_parser("playbook", help="what the loop has learned about your search")
    me(p)
    pb = p.add_subparsers(dest="action", required=True)
    pb.add_parser("show")
    rb = pb.add_parser("rollback")
    rb.add_argument("--reason", default="")
    p.set_defaults(fn=cmd_playbook)

    p = sub.add_parser("config", help="show the standing authorisation in loop.json")
    me(p)
    p.set_defaults(fn=cmd_config)

    p = sub.add_parser("harnesses", help="register the role harnesses and the research sandbox with a running server")
    p.add_argument("--server", default="http://127.0.0.1:8000")
    p.add_argument("--provider")
    p.set_defaults(fn=cmd_harnesses)
    return parser


def main(argv: list[str] | None = None) -> int:
    utf8_io()
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args) or 0
    except configuration.ConfigError as error:
        say(f"{RED}{error}{OFF}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
