"""Run secrets: values that live only for one task run (issue #357)."""

from __future__ import annotations

import logging

import crypto
from models import BacklogItem, RunSecret, Sprint
from sqlmodel import Session, select

_logger = logging.getLogger(__name__)


class RunSecretError(ValueError):
    """A secret that cannot be stored; the message says why."""


def task_company(session: Session, task_id: int) -> int | None:
    """The company that owns the task (through its sprint); ``None`` if unknown."""
    return session.exec(
        select(Sprint.company_id)
        .join(BacklogItem, BacklogItem.sprint_id == Sprint.id)
        .where(BacklogItem.id == task_id)
    ).first()


def create(
    session: Session, company_id: int, task_id: int, name: str, value: str
) -> RunSecret:
    if crypto._configured_key() is None:
        raise RunSecretError(
            "run secrets need FERNET_SECRET_KEY: a run's secret is never stored "
            "in plain text"
        )
    secret = RunSecret(
        company_id=company_id,
        task_id=task_id,
        name=name,
        encrypted_value=crypto.encrypt_value(value),
    )
    session.add(secret)
    session.commit()
    session.refresh(secret)
    return secret


def list_for_task(session: Session, task_id: int, company_id: int) -> list[RunSecret]:
    """The company's secrets for the task; never another company's."""
    return list(
        session.exec(
            select(RunSecret).where(
                RunSecret.task_id == task_id, RunSecret.company_id == company_id
            )
        ).all()
    )


def delete(session: Session, secret_id: int, company_id: int) -> bool:
    secret = session.get(RunSecret, secret_id)
    if secret is None or secret.company_id != company_id:
        return False
    session.delete(secret)
    session.commit()
    return True


def env_for_task(session: Session, task_id: int) -> dict[str, str]:
    """Decrypted values, for the daemon's environment. Never returned by the API.

    Only the secrets of the company that owns the task.
    """
    company_id = task_company(session, task_id)
    if company_id is None:
        return {}
    out: dict[str, str] = {}
    for s in list_for_task(session, task_id, company_id):
        try:
            out[s.name] = crypto.decrypt_value(s.encrypted_value)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "run secret %s (%s) for task %s could not be decrypted: %s",
                s.id,
                s.name,
                task_id,
                type(exc).__name__,
            )
    return out
