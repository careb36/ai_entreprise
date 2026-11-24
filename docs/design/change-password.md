# Changing a password: design and threat model

> **Status: built, 2026-11-24, for the owner's review.** No issue: asked by
> the owner directly ("agrega cambiar contraseña en la app"). SDL practice 3;
> the boundaries are the threat-model skill's
> ([`reference.md`](../../.claude/skills/threat-model/reference.md)). A
> claim, like every design document here: a person reviews it.

## What is built

1. `POST /v1/auth/password` (`backend/app/routers/auth.py`, rules in
   `services/platform/password_change.py`): the signed-in person sends
   `current_password` and `new_password`. The current one is checked with
   bcrypt under the sign-in's per-account backoff (`login_throttle`); the new
   one follows `check_new_password` and must not verify against the stored
   hash. Every live session family of the person is revoked, and the change,
   the revocations and the audit rows are one commit.
2. The **Account** page (user menu → Account) gains a "Password" panel:
   current, new, repeat, the minimum length shown (`/v1/auth/me` now returns
   `has_password` and `password_min_length`).
3. A forgotten password: no email is sent, so an operator with a shell on the
   server runs `docker exec -it factory-backend python -m reset_password`
   (`backend/app/reset_password.py`; `scripts/reset_password.py` only wraps
   that command, because `scripts/` is not in the image). Same rule, every
   session revoked, audited as `password_reset_by_operator`.

**Session decision: reissue.** Every family is revoked, the current one
included, and a request authenticated by the session cookie gets a new
family in the answer's cookies (same sign-in method as the old one). Keeping
the current family would also keep any copy of its refresh token that was
stolen with this browser's cookies; reissuing ends that copy too, at the
cost of one new family. A Bearer access token gets no cookies; its own
token lives out its 15 minutes (known gap, SECURITY.md §3.1).

**Audit.** The audit chain is per company. One row per company the person
belongs to (each enters that company's chain), or one signed row outside any
chain for a person in no company. The row has `details.user_id` and
`result.sessions_ended` (and `session_reissued` for the route); never a
password or hash. The person's row is by `human:<email>`, the operator's by
`operator:cli`: the operator is not authenticated in the app, only by their
shell on the server.

## Assets

- The account itself (the password is the key to every company it belongs
  to, up to Owner).
- The stored bcrypt hash; the other sessions of the person.
- The audit chain: the record that a password changed and by whom.

## Data flow and trust boundaries

```
Browser ──B1 (HTTPS, cookie + CSRF)──▶ POST /v1/auth/password ──▶ users, session_families, audit_log
Operator shell on the server ──B17──▶ docker exec reset_password ──▶ same tables
```

**B17 (new): a shell on the backend's computer** (B15 is the owner actions' in-process principal, B16 the evaluation scorer's sandbox). Whoever can `docker exec`
into the backend container already holds the database password and every key
in its environment; the reset adds no power they lack, but it does the
change through the audited path instead of a bare SQL write.

## Threats

| ID | Boundary | STRIDE | Threat | L | I | Sev | Mitigation |
|---|---|---|---|---|---|---|---|
| P1 | B1 | S | A stolen session (an unlocked laptop, a stolen cookie) changes the password and locks the person out | M | H | High | New: the current password is required; checked with bcrypt; a wrong one is 403 and changes nothing (`test_wrong_current_password_is_refused_and_changes_nothing`) |
| P2 | B1 | S | Online guessing of the current password through this route instead of the sign-in | M | H | High | New: the sign-in's per-account backoff applies here and shares its record, plus the sign-in's per-address limit (`LOGIN_RATE_LIMIT`) (`test_the_sign_in_backoff_applies`) |
| P3 | B1 | E | An API token (a script, a CI job) changes a person's password | L | H | High | New: API tokens and owner-action principals get 403 `person_only` (`test_an_api_token_is_refused`) |
| P4 | B1 | T | Cross-site request changes the password | L | H | Medium | Existing: cookie requests need the CSRF header (`auth_deps._from_cookie`); `SameSite=Strict` (`test_the_cookie_session_needs_the_csrf_header`) |
| P5 | B1 | S | An attacker already signed in elsewhere keeps their session after the person changes the password | M | H | High | New: every family revoked; the asking browser gets a new one (`test_success_switches_the_password_and_ends_the_other_sessions`) |
| P6 | B1 | I | The answer or the audit row leaks the password or the hash | L | H | High | New: rows carry ids and counts only; refusals carry a code and a fixed message (`test_the_change_is_audited_without_any_secret`); 422 bodies never echo input (existing `exception_handlers`) |
| P7 | — | R | A password changed with no record (today: a bare SQL write) | H | M | Medium | New: `password_changed` and `password_reset_by_operator` through `AgentService`, in each company's chain, in the same commit as the change |
| P8 | B17 | I | The operator's new password left in shell history, `ps` or the environment | M | M | Medium | New: read only with `getpass`, twice; never argv or env; never printed (`test_the_operator_reset_changes_the_hash_ends_sessions_and_audits`) |
| P9 | B17 | E | The reset gives a password to an SSO-only account, bypassing the identity provider | L | M | Low | New: the reset refuses an account with no password (`no_password`) |
| P10 | B1 | D | A huge body makes bcrypt or the hash path costly | L | L | Low | New: both fields at most 1024 characters (422); bcrypt reads 72 bytes |
| P11 | B1 | I | The answer tells whether an email exists | — | — | — | Not applicable: the route acts only on the caller's own account |

Assume breach: if the session check failed (a forged cookie), P1 still holds,
because the current password is required. If the backoff record is lost (a
restart, several replicas), P2 falls back to the per-address limit, as for
the sign-in.

## Residual risks

- A Bearer access token issued before the change keeps working until it
  expires (15 minutes); API tokens are not revoked, since they are not tied to
  the password. Both are written in SECURITY.md §3.1.
- The backoff is per process, like the sign-in's: the reset CLI cannot clear
  a running lock in the server process.
- A forgotten password needs someone with a shell on the server; there is no
  self-service recovery until email exists.

## Open questions

- Should a password change also notify the person by another channel (the
  personal notifications) once one exists for every account?
- Should an Owner be able to reset another member's password from the app?
  Not built: it would let one compromised Owner take over every member.
