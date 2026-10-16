import json
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from crypto import CredentialCryptoError, decrypt_value, encrypt_value
from database import get_session
from models.github import GitHubRepoBinding
from services.agent_run_service import ArtifactTooLargeError, ingest_agent_run
from services import github_adapter

router = APIRouter(prefix="/v1/integrations/github", tags=["GitHub"])


class GitHubBindingRequest(BaseModel):
    company_id: int
    owner: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    webhook_secret: str = Field(min_length=1)
    inclusion_rule: Literal["all", "bots_only", "non_bots"] = "all"


class GitHubBackfillRequest(BaseModel):
    company_id: int
    owner: str
    repo: str
    since: Optional[datetime] = None


def _repository_binding(
    session: Session, owner: str, repo: str
) -> Optional[GitHubRepoBinding]:
    return session.exec(
        select(GitHubRepoBinding).where(
            GitHubRepoBinding.owner == owner.casefold(),
            GitHubRepoBinding.repo == repo.casefold(),
        )
    ).first()


def _ingest_pr(
    session: Session, binding: GitHubRepoBinding, pr: dict, token: Optional[str] = None
):
    user = pr.get("user") or {}
    commits = github_adapter.fetch_pr_commits(
        binding.owner, binding.repo, pr["number"], token=token
    )
    producer, _ = github_adapter.infer_producer(
        user.get("login") or "unknown",
        user.get("type"),
        [commit["commit"]["message"] for commit in commits],
    )
    if not github_adapter.should_include(producer, binding.inclusion_rule):
        return None, False
    diff_text = github_adapter.fetch_pr_diff(
        binding.owner, binding.repo, pr["number"], token=token
    )
    payload = github_adapter.pr_to_agent_run_payload(
        company_id=binding.company_id,
        owner=binding.owner,
        repo=binding.repo,
        pr=pr,
        commits=commits,
        diff_text=diff_text,
    )
    return ingest_agent_run(session, payload)


@router.post("/bindings", status_code=201)
def create_github_binding(
    payload: GitHubBindingRequest, session: Session = Depends(get_session)
):
    owner, repo = payload.owner.casefold(), payload.repo.casefold()
    if _repository_binding(session, owner, repo) is not None:
        raise HTTPException(status_code=409, detail="Repository is already bound")
    binding = GitHubRepoBinding(
        company_id=payload.company_id,
        owner=owner,
        repo=repo,
        webhook_secret_encrypted=encrypt_value(payload.webhook_secret),
        inclusion_rule=payload.inclusion_rule,
    )
    session.add(binding)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409, detail="Repository is already bound"
        ) from exc
    session.refresh(binding)
    return {
        "id": binding.id,
        "company_id": binding.company_id,
        "owner": binding.owner,
        "repo": binding.repo,
        "inclusion_rule": binding.inclusion_rule,
    }


@router.post("/webhook", status_code=202)
async def github_webhook(request: Request, session: Session = Depends(get_session)):
    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    if not signature:
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    bindings = session.exec(select(GitHubRepoBinding)).all()
    has_valid_signature = False
    for candidate in bindings:
        try:
            secret = decrypt_value(candidate.webhook_secret_encrypted)
        except CredentialCryptoError:
            continue
        if github_adapter.verify_webhook_signature(secret, raw_body, signature):
            has_valid_signature = True
            break
    if not has_valid_signature:
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        body = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    repository = body.get("repository")
    full_name = repository.get("full_name", "") if isinstance(repository, dict) else ""
    if not isinstance(full_name, str) or "/" not in full_name:
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    owner, repo = full_name.split("/", 1)
    binding = _repository_binding(session, owner, repo)
    if binding is None:
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    try:
        bound_secret = decrypt_value(binding.webhook_secret_encrypted)
    except CredentialCryptoError as exc:
        raise HTTPException(status_code=401, detail="Invalid webhook signature") from exc
    if not github_adapter.verify_webhook_signature(bound_secret, raw_body, signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    if request.headers.get("X-GitHub-Event") != "pull_request":
        return Response(status_code=204)
    action = body.get("action")
    if action not in {"opened", "synchronize", "reopened", "closed"}:
        return Response(status_code=204)
    pr = body.get("pull_request")
    if not isinstance(pr, dict) or "number" not in pr:
        return Response(status_code=204)

    try:
        run, created = _ingest_pr(session, binding, pr)
    except ArtifactTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    if run is None:
        return Response(status_code=204)
    return {
        "agent_run_id": run.id,
        "version": run.version,
        "created": created,
    }


@router.post("/backfill")
def backfill_github_repository(
    payload: GitHubBackfillRequest, session: Session = Depends(get_session)
):
    binding = _repository_binding(session, payload.owner, payload.repo)
    if binding is None or binding.company_id != payload.company_id:
        raise HTTPException(status_code=404, detail="Repository binding not found")

    try:
        pull_requests = github_adapter.fetch_pull_requests(
            binding.owner,
            binding.repo,
            token=None,
            state="all",
            since=payload.since.isoformat() if payload.since else None,
        )
    except github_adapter.GitHubRateLimited as exc:
        raise HTTPException(
            status_code=503,
            detail={"message": "GitHub API rate limited", "reset_at": exc.reset_at},
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="GitHub API request failed") from exc

    ingested = 0
    skipped = 0
    errors = []
    for pr in pull_requests:
        try:
            run, created = _ingest_pr(session, binding, pr)
            if run is None or not created:
                skipped += 1
            else:
                ingested += 1
        except github_adapter.GitHubRateLimited as exc:
            raise HTTPException(
                status_code=503,
                detail={"message": "GitHub API rate limited", "reset_at": exc.reset_at},
            ) from exc
        except ArtifactTooLargeError:
            errors.append(
                {"number": pr.get("number"), "error": "artifact exceeds the 2 MiB limit"}
            )
        except Exception:
            errors.append(
                {"number": pr.get("number"), "error": "unable to ingest pull request"}
            )
    return {"ingested": ingested, "skipped": skipped, "errors": errors}
