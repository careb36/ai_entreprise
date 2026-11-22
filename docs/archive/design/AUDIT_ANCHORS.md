# Audit anchors: the chain head recorded outside the factory

Issue #542 · Status: built (webhook, RFC 3161, customer key); designed (signed
git tag, KMS in the customer's account, the documentation pack's extract).

## The problem

Each company's audit log is a hash chain (`backend/app/audit_chain.py`): every
row carries `chain_seq`, `prev_hash` and `chain_hash`, and the hashes are
HMAC-SHA256 under `AUDIT_SIGNING_KEY`. That key is held by the factory, which
is the party the chain records. Whoever holds the key and can write the
database can alter a row and re-sign every row after it, and
`GET /v1/audit/chain` still answers `intact`. Dropping the newest rows is not
visible either. `docs/POSITIONING.md` (rule 6) says a hash or signature is
never accepted from the party being recorded, so an outside auditor cannot rely
on the chain alone.

## What is built

The chain head (a sequence number and its hash) is recorded, periodically and
on demand, somewhere the factory cannot rewrite it. Because each chain hash
commits to every row before it, an anchor at sequence `n` covers rows `1..n`.
Taken from Certificate Transparency (an append-only log whose head is checked
by parties other than its operator, RFC 6962, design only) and RFC 3161
(time-stamp tokens from an authority the relying party chooses).

### The statement

Every anchor, of every kind, covers the same bytes
(`services/platform/audit_anchors.statement`), pinned and versioned:

```json
{"anchored_at":"2026-11-15T12:00:01Z","chain_hash":"hmac-sha256:…","company_id":7,"seq":3,"type":"la-fabrique.audit-anchor","v":1}
```

(`json.dumps` with sorted keys and no spaces; `anchored_at` in UTC to the
second, and stored to the second so the statement can be rebuilt from the row.)

### The kinds

| Kind | Where the head goes | What the backend can check | Strength |
|---|---|---|---|
| `tsa` | An RFC 3161 authority the Owner chose (`tsa_url`), asked for a token over SHA-256 of the statement, with a nonce | The token's signature with the certificate the Owner pinned (`tsa_cert_pem`), its imprint, nonce, `genTime` inside the certificate's validity, the time-stamping EKU, the ESS signing-certificate when present | `verified` |
| `customer_key` | The customer's own job fetches the statement (`GET /v1/audit/anchors/statement`), signs it with an Ed25519 private key the factory never sees, and posts the signature (`POST /v1/audit/anchors/signature`) | The signature with the registered public key; the head must be the chain's row at that number now | `verified` |
| `webhook` | The company's notification webhook (same URL, secret, `X-Fabrique-Signature-256`, SSRF check and outbox retries as `services/integrations/webhooks.py`), event `audit_anchor` | Nothing: the customer's endpoint keeps the copy. The answer it returns is stored as a **claim**, unless it carries `{"signature": "<base64>"}` by a registered customer key, which makes it `verified` | `claim` |
| `git_tag` | A signed tag in the customer's repository | Designed, not built (below) | — |

When they are taken (`services/integrations/anchoring.py`): on the Owner's
cadence (`cadence_minutes`, 15 to 1440, default 60; a background loop that one
replica runs at a time under an advisory lock), when a sprint closes, when a
run or a sprint's integration is published (a `before_flush` listener queues
an `audit_anchor_take` outbox event), and on demand (`POST
/v1/audit/anchors`, Manager). Each anchor is a row plus an outbox event in one
transaction; a head already anchored by a kind is not anchored again by it (a
unique index stops two replicas racing). A delivery that fails its last outbox
attempt marks the anchor `failed`.

### Verification

`services/platform/audit_anchors.coverage`, served by `GET /v1/audit/anchors`
and added as `anchors` to `GET /v1/audit/chain`, re-checks every anchor against
the chain as it is now:

- the chain's row at the anchor's number must exist (`row_missing`: the newest
  rows were dropped) and have the anchored hash (`hash_mismatch`: rewritten);
- a `tsa` token must still verify over the rebuilt statement
  (`token_invalid: …`), a `customer_key` signature too (`signature_invalid`,
  `key_unknown`);
- `pending` and `undelivered` anchors cover nothing.

It reports `segments` (the range each anchored number newly covers, and
whether that is `verified` or only a `claim`), `verified_through`,
`covered_through`, the `uncovered` tail and the `failures`, with a status of
`empty`, `no_anchors`, `uncovered_tail`, `covered` or `anchor_mismatch`.

The measure of the issue is a test
(`backend/tests/test_audit_anchors.py::test_a_rewrite_resigned_with_the_factory_key_passes_the_chain_and_fails_the_anchors`):
a row is altered and every later row re-signed with the factory's key; the
chain verifies, the anchor check fails. Rewriting the anchor rows to match
breaks their tokens and signatures (`test_rewriting_the_anchor_too_breaks_its_proof`).
After a cadence run, anchors cover the whole chain up to its head
(`test_the_cadence_covers_the_whole_chain_up_to_its_last_run`).

### Verifying outside the factory

The backend's check is a convenience; the point is that someone else can do
it. `GET /v1/audit/anchors/{id}/export` returns the statement and its proof:

- **tsa**: save `statement` (no trailing newline) and `token` (base64-decoded,
  DER), then
  `openssl ts -verify -data statement.json -in token.der -token_in -CAfile tsa.pem`,
  with the authority's certificate taken from the authority itself, not from
  the export. The backend's DER reader is checked against `openssl ts` in both
  directions (`backend/tests/test_rfc3161.py`).
- **customer_key**: `openssl pkeyutl -verify -pubin -inkey customer.pub -rawin
  -in statement.json -sigfile sig.bin`, with the public key from the
  customer's own records.
- **webhook**: compare the statement with the copy the endpoint stored.

Then compare `chain_hash` at `seq` with the chain export (`docs/OPERATIONS.md`,
Archiving the audit chain).

### The customer's key

The Owner registers an Ed25519 public key (PEM or base64 of the 32 bytes;
`PUT /v1/audit/anchors/settings` with `add_public_key`). Up to five keys are
active; `retire_fingerprint` retires one: it signs nothing new and still
verifies what it signed. Rotation: add the new key, move the signing job to
it, retire the old one. The private key lives in the customer's own secret
store or KMS; a minimal job is

```sh
curl -s -H "Authorization: Bearer $TOKEN" "$API/v1/audit/anchors/statement?company_id=$ID" > head.json
jq -j .statement head.json > statement.json
openssl pkeyutl -sign -inkey customer.pem -rawin -in statement.json | base64 -w0 > sig.b64
jq -n --slurpfile h head.json --rawfile s sig.b64 \
  '{seq:$h[0].seq, chain_hash:$h[0].chain_hash, anchored_at:$h[0].anchored_at, signature:$s}' |
  curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d @- "$API/v1/audit/anchors/signature?company_id=$ID"
```

with a Viewer API token. The job should keep `head.json` itself: its own copy
is the evidence, the factory's row is a convenience.

### Settings

Owner only, by a person, not an API token; each change is an audit row
(`audit_anchor_settings_changed`, with the fingerprints and the certificate's
SHA-256, never more). A Manager reads them. The TSA URL may be http or https
(the token is signed by the authority, so TLS adds nothing to it) and passes
the webhook's address check (`WEBHOOK_ALLOW_PRIVATE_HOSTS`); the certificate
must carry the time-stamping EKU.

## Threat model

SDL practice 3, after `.claude/skills/threat-model/SKILL.md`. Reviewed by: not
yet (a claim until a person reviews it).

**Assets.** The integrity of each company's audit chain (the product,
`docs/POSITIONING.md`); `AUDIT_SIGNING_KEY`; the webhook secret; the Owner's
choice of authority and keys; availability of the outbox.

**Flow and boundaries.**

```
 Customer job ──B1── Backend ──B6── Company webhook (customer)
   (Ed25519 key)       │  └──B11── RFC 3161 authority (Owner's choice)
                       └── Postgres: audit_log, audit_anchors, audit_anchor_settings
```

B11 is new: the backend to a timestamping authority. It is a boundary because
the authority's answer is trusted to prove a time, so the answer is checked
against a certificate the Owner pinned, never against what the answer says.

| ID | Boundary | STRIDE | Threat | L | I | Severity | Mitigation |
|---|---|---|---|---|---|---|---|
| T1 | Postgres | T | An insider with the factory key rewrites a row and re-signs the rest | M | H | High | New: external anchors; `coverage` reports `hash_mismatch` (test above) |
| T2 | Postgres | T | The newest rows are dropped | M | H | High | New: `row_missing` for any anchor past the head |
| T3 | Postgres | T, R | The insider also rewrites the anchor rows | M | H | High | New: tokens and signatures are over the rebuilt statement; the factory holds neither the TSA's nor the customer's private key (`test_rewriting_the_anchor_too_breaks_its_proof`) |
| T4 | Postgres | T | The insider replaces the pinned TSA certificate or registers its own customer key, then forges anchors | L | H | Medium | Settings are Owner-only, by a person, audited with fingerprints; the auditor verifies with the authority's published certificate and the customer's own public key, not the export's. Residual: the backend's own check trusts its database (below) |
| T5 | B11 | S | A fake authority, or a man in the middle on http | M | M | Medium | Pinned certificate; signature, imprint, nonce, EKU and validity checked (`test_rfc3161.py`); http allowed only because the token authenticates itself |
| T6 | B11 | T, D | A malformed or huge answer exploits the parser | M | M | Medium | Answer capped at 64 KiB in the adapter and the reader; DER reader refuses indefinite lengths, high tags, trailing bytes and truncation (parametrised test); crypto by `cryptography` |
| T7 | B11, B6 | I | SSRF through `tsa_url` or the webhook URL | M | M | Medium | Existing: `webhooks.check_url` at save and before each request, no redirects |
| T8 | B6 | S, R | The endpoint's receipt is taken as proof | M | M | Medium | Stored as a claim (`state: claim`, `strength: claim`); verified only if signed by a registered customer key |
| T9 | B1 | S, E | A Viewer forges a customer anchor | L | L | Low | The signature must verify with an active Owner-registered key and the head must match the chain; audited |
| T10 | B1 | E | A Manager or a token changes who verifies the chain | L | H | Medium | Owner role and a person (tokens refused, as for SSO); audited |
| T11 | Outbox | D | A down authority or endpoint holds up sprints | M | L | Low | Delivery is its own outbox row with backoff; failure marks the anchor `failed`; the change that triggered it was already committed |
| T12 | All | R | A missing anchor is read as "fine" | M | M | Medium | `uncovered` and `no_anchors` are explicit statuses; pending and failed cover nothing (rule 7: unknown is not absent) |

**Residual risk.** The backend's own coverage check reads the anchors, the
pinned certificate and the customer keys from the database it is protecting,
so an insider who controls the database and the deployment can make that
report say anything; what they cannot do is make the authority's token or the
customer's signature verify with keys taken from outside the factory. The
external verification above is the control; the in-product report is an aid.
A webhook anchor's evidence is only as good as the customer's copy. The owner
accepts these.

## Designed, not built

- **Signed git tag** (`git_tag`): at sprint close, the daemon creates an
  annotated tag `fabrique/audit/<seq>` whose message is the statement, signed
  with a key on the customer's machine (the daemon's git config), in the
  customer's repository. The factory reads it back by hash; the customer's
  remote is the copy. Needs a daemon request type and the customer's signing
  setup, so it waits for the daemon work.
- **KMS in the customer's account**: the same Ed25519 (or ECDSA P-256)
  verification, with the customer's job calling their KMS `Sign`. Only the key
  type would change; ECDSA is a small addition to `parse_public_key`.
- **Documentation pack extract** (`docs/archive/design/PROJECT_DOCUMENTATION.md`,
  later phases): the audit-trail extract embeds `coverage` for the range it
  shows and lists every anchor with its export, so an auditor can verify it
  without access to the product.
- **Chain checkpoints**: archiving old rows (`docs/OPERATIONS.md`) could start
  the live chain from a verified anchor.
- **Several authorities** for one company, and a monitor that alerts when the
  coverage status leaves `covered`.
