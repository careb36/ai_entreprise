"""Set a new password for a person who forgot theirs, from the server.

La Fabrique sends no email, so a forgotten password is reset by an operator
with a shell where the backend runs (``docs/OPERATIONS.md``, "A person forgot
their password"):

    docker exec -it factory-backend python -m reset_password

It asks for the email, then for the new password twice with ``getpass``
(never from the command line or the environment, so it is not left in the
shell history or in ``ps``). The password follows the same rule as in the
app (``check_new_password``), every session of the person ends, and the
reset is audited as ``password_reset_by_operator`` through ``AgentService``,
in the hash chain of each of the person's companies. Nothing printed names
the password or its hash.
"""

from __future__ import annotations

import getpass
import sys

from services.platform.password_change import PasswordChangeRefused, reset_by_operator


def run(session, ask=input, secret=getpass.getpass, out=print) -> int:
    """Ask, reset and report; the exit status (0 done, 1 refused)."""
    email = ask("Email of the account: ").strip()
    if not email:
        out("No email given: nothing changed.")
        return 1
    first = secret("New password: ")
    second = secret("Repeat the new password: ")
    if first != second:
        out("The two passwords differ: nothing changed.")
        return 1
    try:
        changed = reset_by_operator(session, email, first)
    except PasswordChangeRefused as exc:
        out(f"Refused ({exc.code}): {exc.message} Nothing changed.")
        return 1
    out(
        f"Password reset for user {changed.user_id}; "
        f"{changed.sessions_ended} session(s) ended; "
        f"{changed.audit_rows} audit row(s) written."
    )
    return 0


def main() -> int:
    from database import engine
    from sqlmodel import Session

    with Session(engine) as session:
        return run(session)


if __name__ == "__main__":
    sys.exit(main())
