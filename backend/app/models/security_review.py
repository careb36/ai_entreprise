"""A person's recorded review of one OWASP WSTG test for a company's project (issue #539).

The project's security test plan (``services/planning/wstg_plan.py``) marks
some tests ``manual``: only a person can settle them. A Manager records the
review here (``POST /v1/companies/{id}/security/wstg/reviews``): who reviewed,
on which date, the artifact the review rests on (a report, a ticket, a path)
and the result. With a verification's observation, this is the only thing
that sets a row of the WSTG checklist to observed (``project_docs.wstg_status``).

``record_sha256`` is computed by the backend over the stored fields, never
taken from the request. The reviewer's name stays in the database: the pack
written to the repository shows the review's id, date, artifact and the
recorder's company role (rule 6 of ``docs/design/PROJECT_DOCUMENTATION.md``).
"""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel

REVIEW_RESULTS = ("pass", "fail", "not_applicable")


class WstgReview(SQLModel, table=True):
    __tablename__ = "wstg_reviews"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    # The catalogue's versioned id (``WSTG-v42-ATHN-02``).
    wstg_id: str
    result: str
    reviewer_name: str
    # The recorder's company role when it was recorded (Owner, Manager).
    reviewer_role: str = "Manager"
    # The date of the review, ISO (YYYY-MM-DD), as the person gave it.
    reviewed_on: str
    # What the review rests on: a report's path or URL, a ticket, a document id.
    artifact: str
    note: str | None = None
    recorded_by: str | None = None
    record_sha256: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
