"""Run secrets: values that live only for one task run (issue #357)."""

from __future__ import annotations

from crypto import decrypt_value, encrypt_value
from models import RunSecret
from sqlmodel import Session, select


def create(
    session: Session, company_id: int, task_id: int, name: str, value: str
) -> RunSecret:
    secret = RunSecret(
        company_id=company_id,
        task_id=task_id,
        name=name,
        encrypted_value=encrypt_value(value),
    )
    session.add(secret)
    session.commit()
    session.refresh(secret)
    return secret


def list_for_task(session: Session, task_id: int) -> list[RunSecret]:
    return list(
        session.exec(select(RunSecret).where(RunSecret.task_id == task_id)).all()
    )


def delete(session: Session, secret_id: int, company_id: int) -> bool:
    secret = session.get(RunSecret, secret_id)
    if secret is None or secret.company_id != company_id:
        return False
    session.delete(secret)
    session.commit()
    return True


def env_for_task(session: Session, task_id: int) -> dict[str, str]:
    """Decrypted values, for the daemon's environment. Never returned by the API."""
    out: dict[str, str] = {}
    for s in list_for_task(session, task_id):
        try:
            out[s.name] = decrypt_value(s.encrypted_value)
        except Exception:  # noqa: BLE001, S112
            continue
    return out
