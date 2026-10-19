"""python -m jobloop <command>: one entry point; each command module owns its arguments and its main()."""
from __future__ import annotations

import argparse
import importlib
import sys

COMMANDS = {"profile": "build the profile", "queries": "plan search queries", "search": "search the boards",
            "rank": "fetch, score and rank postings", "fit": "score one posting against the profile",
            "apply": "draft and verify a CV and cover letter", "forms": "draft application-form answers",
            "tracker": "the tracker (drafted, applied, ...)", "followup": "draft follow-ups and thank-yous",
            "stale": "sweep applications with no news", "calibrate": "check the fit weights against outcomes",
            "report": "the static HTML report", "interview": "an interview pack or a mock",
            "upskill": "a skills-gap report", "salary": "a salary benchmark from your own data",
            "enrich": "add public GitHub/portfolio facts, source-tagged", "addboard": "add a job board"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobloop", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    modules = {}
    for name, help_ in COMMANDS.items():
        module = importlib.import_module(f"jobloop.{name}")
        modules[name] = module
        module.add_arguments(sub.add_parser(name, help=help_))
    args = parser.parse_args(argv)
    return modules[args.command].main(args) or 0


if __name__ == "__main__":
    sys.exit(main())
