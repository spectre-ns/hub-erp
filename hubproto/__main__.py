"""``python -m hubproto VERB ...``: the command line, run from the code next to it (in a worktree, the worktree's code;
the venv's ``hubproto`` script runs whichever checkout is installed)."""

import sys

from .cli import main

sys.exit(main())
