# Approving from Slack or Microsoft Teams (issue #512, phase 4)

Status: draft, written before the code by the `threat-model` skill; a person
reviews it before it guides more work · Issue: #512 · SDL practice 3

## What is built

When a delivery waits for a person's acceptance (the risk hold: QA approved a
high-risk delivery, or every approval at autonomy level 1), La Fabrique posts
a message to the company's Slack channel or Teams channel with the task, the
Verifier's verdict and the failing checks as recorded (observations, never the
agent's claims), links to the run's evidence, and two actions: **Approve** and
**Send back**. A person who has linked their chat account to their La Fabrique
account decides there; the decision goes through the same service function as
the web review (`services/loop/run_review.submit_review`) after the same role
check (`require_company_role(..., "Manager")`), is audited as that person with
`via: slack|teams`, and the chat message is updated with the outcome.

- **Slack:** one Slack app per company (bot token + signing secret). Outbound
  through `chat.postMessage` / `chat.update` / `chat.postEphemeral` with Block
  Kit buttons; inbound interactions and the `/fabrique link` slash command at
  one Request URL, verified with `X-Slack-Signature` (v0 HMAC-SHA256 over
  `v0:{timestamp}:{body}`, timestamp within five minutes, constant-time
  compare).
- **Teams:** an **outgoing webhook** for inbound (Teams signs every request
  with HMAC-SHA256 of the body under a key it shows once, header
  `Authorization: HMAC <base64>`) and an incoming webhook (Workflows) URL for
  outbound Adaptive Cards. A person answers by mentioning the webhook in the
  card's thread: `@Fabrique approve K7Q2M9PX` or `@Fabrique sendback K7Q2M9PX
  <reason>`. Chosen over a Bot Framework bot because it verifies with a shared
  key and no key fetch: a bot needs the Bot Framework's OpenID metadata and
  JWKS fetched and cached, an Azure app registration with a client secret,
  token acquisition for every outbound call and a conversation reference from
  an installation event before it can post. The cost is that Teams has no
  buttons here (incoming-webhook cards cannot submit), no ephemeral replies
  (the outgoing webhook's synchronous answer is a thread reply) and the card is
  answered rather than edited. A bot is the follow-up if buttons are wanted.

**Approval points wired:** the risk hold (`risk_hold` task event, written by
`services/loop/worker/qa_review.py`) — Approve accepts it (`approve` →
`approved_by_person`, task done), Send back returns it to To do with a
`NEEDS WORK:` note. Not wired in this phase: another producer's run waiting
for approval, a task blocked for triage without a hold (only "Send back"
applies, no "Approve"), publishing a branch (needs the daemon, not a click),
and the Owner-only decisions of `docs/WORKFLOWS.md` (not built). Those stay in
the web inbox.

**Identity link.** A person signed in to La Fabrique asks for a one-time code
in Settings → Chat approvals (10 characters, ~50 bits, sha256 stored, 10
minutes, one at a time) and types it in chat (`/fabrique link CODE` in Slack,
`@Fabrique link CODE` in Teams). The signed request names the chat user; the
code names the La Fabrique user; both together create the link, which the
person sees and can remove. Email matching was rejected: it needs the
`users:read.email` scope, a workspace admin can set any address on a Slack
profile, and the SSO design already found that an email asserted by a party
the company controls is not proof across companies (`docs/design/SSO.md`,
T15). The code proves possession of both accounts at once.

## Assets

- The decision itself: accepting a high-risk delivery is the one place the
  loop waits for a person; a forged approval ships unreviewed code.
- The bot token, the Slack signing secret, the Teams HMAC key and the Teams
  incoming webhook URL (itself a bearer credential), encrypted with
  `FERNET_SECRET_KEY`.
- The audit chain: who decided, from where.
- Company isolation: a chat workspace of company A must never decide for B.

## Data flow and trust boundaries

```
Worker ── risk_hold event ──> before_flush ──> outbox row (same transaction)
Outbox processor ──B9──> slack.com/api (bot token)  |  Teams incoming webhook URL
Slack/Teams ──B9──> POST /v1/integrations/{slack|teams}/{public_id}/...
     verify signature → replay store → workspace/tenant check → link → role
     → stale check (head, run version) → claim → submit_review → audit
     → outbox: update message / ephemeral reply
Person ─B1─> /v1/companies/{id}/chat (Owner), /chat/links (any member)
```

**B9 (new): backend ↔ chat platforms.** Inbound requests come from the
internet, are authenticated only by a per-company shared secret, and name a
chat user the platform vouches for; the payload's text, user names and any
role-shaped field are data. Outbound calls carry a company's credential to a
fixed host (Slack) or an Owner-chosen URL (Teams). Distinct from B6: B6's
inbound webhook is GitHub's, and its outgoing one carries no credential.

## Threats

| ID | Boundary | STRIDE | Threat | L | I | Sev | Mitigation |
|---|---|---|---|---|---|---|---|
| C1 | B9 | S | Forged interaction (no or wrong signature) approves a hold | M | H | Critical | New: `chat_signatures.verify_slack` / `verify_teams`, HMAC-SHA256, `hmac.compare_digest`, before the body is parsed; secret per connection, looked up by an opaque `public_id` in the URL; 401 with nothing changed. Tests: missing, wrong, other company's secret |
| C2 | B9 | S/T | Replay of a captured, validly signed request | M | H | High | New: Slack timestamp within 300 s of now (both directions); Teams activity `timestamp` (inside the signed body) within 300 s; every interaction's key (Slack `trigger_id`, Teams activity `id`) stored once in `chat_interactions` (unique) before acting. Tests: stale timestamp, second delivery |
| C3 | B9 | E | Payload names a role or a La Fabrique user | M | H | Critical | New: nothing in the payload but the platform's user id is read for identity; the user comes from `chat_identities`, the role from `CompanyMember` through `require_company_role`, as on the web. Test: Viewer refused |
| C4 | B9 | S | Chat user never linked, or linked in another company, decides | M | H | High | New: link looked up by `(company, provider, workspace, chat user)`; the workspace (Slack `team.id`, Teams tenant) must equal the connection's. Tests: unlinked, wrong workspace |
| C5 | B1/B9 | S | Link code stolen (shoulder-surfed, pasted in a public channel) and claimed first | L | M | Medium | New: code shown only to the signed-in person, 10 minutes, single use, one outstanding; the link appears in their settings with the chat user id, removable; audit `chat_account_linked`. Residual: see below |
| C6 | B9 | T | Stale decision: the task changed since the message (a new run, a send-back on the web, already decided) | H | H | High | New: the message stores the hold event's chain hash (a new verdict, a send-back or an acceptance replaces or ends the hold) and the run's version at posting; the Slack button value carries the approval id, a tag of that hash and the version; on action, both are compared with the record now (the current hold and the latest run), and the message is claimed with a conditional update (`posted` → `deciding`). An unrelated note on the task does not make a message stale. Tests: new run version, tampered head, decided on the web → refused, nothing changes |
| C7 | — | R | A person denies a chat decision | L | M | Medium | Existing + new: `submit_review` writes `run_review_<event>` as `human:<name>`, now with `via` and `user_id`; plus `chat_approval_<outcome>` and `chat_approval_refused` rows naming the provider and chat user (`AgentService.audit_interaction`, HMAC-chained) |
| C8 | B1 | I | Secrets returned by the API or logged | M | H | High | New: Fernet at rest, `describe()` returns only `has_*` flags; audit rows name fields changed, never values. Test: GET and PUT responses contain no secret |
| C9 | B9 | I | SSRF through the Teams incoming webhook URL | M | M | Medium | New: https only, host in the Microsoft suffix list, every address public (`webhooks.check_url`, re-checked before each post), no redirects. Slack calls go to `https://slack.com/api/` only |
| C10 | B9 | I | Message reveals more than the channel should see | M | L | Low | New: title, verdict, failing check names and links only; no diff, no artifact text; refusal texts name no other person. Channel membership is the Owner's choice |
| C11 | B9 | D | Flood of inbound requests | M | L | Low | New: rate limit per address (`CHAT_RATE_LIMIT`), body cap 64 KiB, signature checked before any query beyond the connection lookup |
| C12 | B1 | E | Non-Owner or API token changes the connection (points the channel elsewhere, swaps the secret) | L | H | High | New: config endpoints require a signed-in Owner, API tokens refused (as SSO); audited. Test |
| C13 | B5 | T | Agent-written text (title, check names) breaks message formatting or injects mentions | M | L | Low | New: Slack `mrkdwn` escaped (`&`, `<`, `>`), Teams card text in `TextBlock`s (no markdown links from data), lengths capped |
| C14 | — | E | Assume breach: the signing secret leaks | L | H | High | Layered: a forged request still needs a linked chat user id of a Manager and a current approval id and head; rotation documented (`docs/OPERATIONS.md`); every decision audited |

## Tests that prove the mitigations

`backend/tests/test_chat_approvals.py` and `test_chat_inbound.py`: valid,
invalid and missing signatures for both providers, stale timestamps, replayed
interactions, unlinked and wrong-workspace users, a Viewer refused, a Manager
allowed, a changed head refused with the task untouched, the audit rows, the
outbox retry of a failed post, the secret never returned, Owner-only
configuration, the link flow (single use, expiry), a claim left unfinished
taken again only after two minutes, and replay keys older than any valid
request dropped.

## Review after the code (sdl-review)

Three findings from the `sdl-review` pass on the diff, fixed before the
commit: the replay store grew without bound (now keys older than an hour are
dropped on insert; a request that old already fails its timestamp); a claim
left in `deciding` by a process that stopped between the claim and the commit
would block the approval for good (now re-taken after two minutes, and the
record check refuses it if the first decision did commit); and the same hold
posted to both platforms kept its buttons on the other one after a decision
(now marked out of date with the outcome).

## Residual risks

- A stolen link code claimed within ten minutes binds the attacker's chat
  account; the victim sees the link in settings, and every decision names the
  chat user. A two-sided confirmation (confirm in the web after the chat
  claim) is the stronger variant if the owner wants it.
- Teams refusals are thread replies visible to the channel (no ephemeral
  messages for outgoing webhooks); they say what to do, not who someone is.
- Slack `chat.postMessage` is not idempotent: a post that succeeded but whose
  answer was lost is posted again on retry (a duplicate message whose buttons
  act on the same approval once).
- The signing secret of each platform is a symmetric key held by the platform
  and by La Fabrique; a breach of either side forges requests (C14).

## Open questions

- Should approvals in chat require Owner rather than Manager (the web allows a
  Manager)? Kept equal to the web here.
- Should a person's chat link expire, or end when they leave the company? It
  ends with the membership: the role check fails without one.
