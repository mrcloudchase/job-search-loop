"""python -m jobloop <command>: the person's surface (see jobloop/cli.py). The work itself is `python -m jobloop run`."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
