#!/usr/bin/env python3
"""Take a candidate from a CV to applications ready to click apply.

    python loop.py --cv my_cv.pdf              # first time: builds your profile (you confirm it), then runs the loop
    python loop.py                             # later: continue; ready applications are listed at the end

The loop searches, ranks, researches, drafts, checks and packages. It stops for you at three points only: confirming your
profile, approving a posting it will not trust on its own, and clicking apply. See README.md and docs/loop-spec.md."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from jobloop import cli, driver, text
from jobloop.engine import config as configuration


def main(argv: list[str] | None = None) -> int:
    text.utf8_io()
    p = argparse.ArgumentParser(prog="loop.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--me", default="me", help="your private folder (default me/, gitignored)")
    p.add_argument("--cv", help="your CV (pdf, docx, txt): builds the profile the first time")
    p.add_argument("--docs", help="a folder of your documents instead of one CV")
    p.add_argument("--target", type=int, default=3, help="stop when this many applications are ready (default 3)")
    p.add_argument("--max-passes", type=int, default=6)
    p.add_argument("--budget", type=float, default=40.0, help="total dollar ceiling for this run (default 40)")
    p.add_argument("--no-ask", action="store_true", help="never ask about held postings; list them at the end")
    args = p.parse_args(argv)
    me = Path(args.me)

    if not (me / "profile.md").is_file():
        setup = ["setup", "--me", str(me)] + (["--cv", args.cv] if args.cv else ["--docs", args.docs] if args.docs else [])
        if not (args.cv or args.docs):
            print("No profile yet: pass --cv FILE (or --docs FOLDER), or answer the interview.")
        if cli.main(setup) != 0 or not (me / "profile.md").is_file():
            print("The loop needs your confirmed profile before it can do anything else.")
            return 2
    config = configuration.load(me).validate()
    from jobloop.stages import all_stages
    stages = all_stages(strict=True)
    interactive = sys.stdin.isatty() and not args.no_ask
    summary = driver.drive(me, lambda: cli.one_pass(me, stages, config), target=args.target, max_passes=args.max_passes,
                           budget_usd=args.budget, ask=input if interactive else None)
    driver.report(summary)
    return 0 if summary.ready else 1


if __name__ == "__main__":
    sys.exit(main())
