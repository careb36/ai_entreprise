#!/usr/bin/env python3
"""Reset a person's password, from the computer that runs the backend container.

The repository's ``scripts/`` folder is not in the backend image (it is built
from ``backend/`` alone), so the reset itself lives in the image as
``backend/app/reset_password.py``. This wrapper only runs it inside the
container, interactively:

    python3 scripts/reset_password.py                 # container factory-backend
    python3 scripts/reset_password.py --container NAME

which is the same as ``docker exec -it factory-backend python -m reset_password``.
The email and the new password are asked inside the container (the password
twice, with ``getpass``); nothing secret passes through this script, its
arguments or the environment. ``docs/OPERATIONS.md``, "A person forgot their
password".
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys

_NAME = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


def command(container: str) -> list[str]:
    if not _NAME.match(container):
        raise ValueError(f"not a container name: {container!r}")
    return ["docker", "exec", "-it", container, "python", "-m", "reset_password"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--container", default="factory-backend")
    args = parser.parse_args(argv)
    docker = shutil.which("docker")
    if docker is None:
        print("docker is not on PATH: run this where the backend container runs.")
        return 1
    argv_ = command(args.container)
    os.execv(docker, [docker, *argv_[1:]])
    return 0  # not reached


if __name__ == "__main__":
    sys.exit(main())
