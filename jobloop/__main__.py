"""`python -m jobloop COMMAND`: the steps of a job search, each a module that can also run on its own."""
from __future__ import annotations

import argparse
import importlib
import sys

COMMANDS = {
    "profile": "build your profile from a CV and notes",
    "portals": "list, import and enable job portals",
    "add-portal": "have a harness write an adapter for a new job portal",
    "search": "search the portals, drop what you've seen, rank what's left by fit",
    "fit": "score one posting against your profile: go or no-go",
    "apply": "draft, check, review and revise a CV and cover letter",
    "interview": "prepare for an interview from the posting and your submitted documents",
    "tracker": "list applications, or say where one stands",
    "gaps": "the skills your best-fit postings ask for that your profile lacks",
}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m jobloop", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    for name, help_ in COMMANDS.items():
        module = importlib.import_module("jobloop." + name.replace("-", ""))
        s = sub.add_parser(name, help=help_)
        s.set_defaults(run=module.main)
        if getattr(module, "HARNESS", False):                               # commands that drive harnesses share these
            s.add_argument("--server", default="http://127.0.0.1:8000", help="the harness-interface server")
            s.add_argument("--minutes", type=float, default=20)
            s.add_argument("--budget", type=float, default=5.0, help="dollars, as the harnesses report them; a turn that reports none counts as 0")
        module.add_arguments(s)
    args = p.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
