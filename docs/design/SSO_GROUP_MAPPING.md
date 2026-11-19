# SSO group mapping to teams (issue #703)

Status: draft, written before the code by the `threat-model` skill; a person
reviews it before it guides work · Issue: #703 (split from #512) · SDL
practice 3 · Depends on: single sign-on ([`SSO.md`](SSO.md)), teams and the
team membership history ([`TEAMS_AND_USAGE.md`](TEAMS_AND_USAGE.md), the
history added by PR #711, migration `0052`), segregation of duties and the
access review ([`SEGREGATION_OF_DUTIES.md`](SEGREGATION_OF_DUTIES.md), #543) ·
Migration: `0053`, after `0051` (PR #681) and `0052` (PR #711); nothing here
is built yet · Amended 2026-11-19 with the owner's decisions on the seven open
questions, for a corporate-scale product (see "Decisions (2026-11-19)"; G19–G22
added).

This is its own document rather than another amendment to `SSO.md` (which
holds T1–T33) because it changes teams as much as SSO. Its threats are
numbered G1–G22 so they never collide with `SSO.md`'s T numbers or
`TEAMS_AND_USAGE.md`'s.

## What is built

Per SSO connection, the Owner names the ID token claim that carries the
person's groups (`groups` by default; for Entra the page proposes `roles`, the
application's app roles, and `groups` stays as the fallback) and writes a list
of `(group value, team)` pairs. Each team is either **Owner-managed** (as
today: its members are added and removed by hand) or **SSO-managed** (its
members come from the mapping, apart from the visible manual exceptions kept
when the team was converted). On each completed SSO sign-in, after the
identity has been resolved and the person is a member of the connection's
company, the backend reads the claim from the ID token it has just validated
and sets the person's membership in the SSO-managed teams that the connection
maps, and in no other team, honouring the Owner's pinned exclusions and manual
exceptions. Each change is written to `team_members`, to the membership history
(#711) with the mapping as its actor, and to the audit chain with the claim as
observed. A pair that would grant Manager takes effect only once a second
Owner approves it (four-eyes). No model takes part in any step.

Not built here, and each its own issue: a reviewed Microsoft Graph and Google
Directory group sync off the sign-in path (#716) and SCIM 2.0 provisioning,
deprovisioning first (#717). Also not built: mapping groups to a company role
directly, and applying groups when a person links an identity from inside a
session (`SSO.md`, T25–T33). The sign-in path never calls #716 or #717.

## Assets

- The role boundary in a company (Owner > Manager > Viewer): an SSO-managed
  team is a new way for something outside La Fabrique to grant a role.
- Company isolation: a company's IdP must reach only its own teams and members.
- Access that already exists: a missing or broken claim must not remove it.
- The Owner's control in an emergency: the break-glass exclusion must take a
  person out at once and stay in force against the IdP.
- Segregation of duties: a grant of Manager through the IdP is decided by two
  Owners, not one (#543).
- The membership history and the audit chain: who was in which team, when,
  and why. The usage report reads the history (#702), so a wrong period moves
  past usage to the wrong team.
- Availability of sign-in: the mapping must never stop a valid sign-in, apart
  from a failed write, which rolls back the whole sign-in (R13).

## Data flow and trust boundaries

```
IdP ──302──> Backend /v1/auth/sso/callback            (B1, browser-carried)
               ├─B8─ token endpoint → ID token (validated: SSO.md T1, T2)
               ├── resolve_identity → user, member of conn.company_id?
               └── groups.apply(conn, user, claims)   ← new, same transaction
                     ├── read claim `conn.groups_claim` from the ID token only
                     ├── classify: absent | overage | malformed | empty | present
                     ├── stale check: token iat vs the last applied observation
                     ├── sso_group_observations row (append-only)
                     ├── exclusions and manual exceptions read (never written)
                     ├── team_members add/remove (SSO-managed, mapped teams only)
                     ├── teams.open_membership / close_memberships (#711)
                     └── audit row `sso_group_sync` (signed, chained)
Owner ─B1─ GET/PUT /v1/companies/{id}/sso/group-mappings   (signed-in human Owner)
Owner ─B1─ POST …/sso/group-mappings/approvals/{id}/approve | reject
Owner ─B1─ POST …/sso/group-mappings/approvals/{id}/confirm-single-owner
Owner ─B1─ POST|DELETE /v1/companies/{id}/teams/{team_id}/exclusions[/{id}]
Owner ─B1─ POST …/teams/{team_id}/membership-source-preview
Owner ─B1─ PATCH /v1/companies/{id}/teams/{team_id} {membership_source, preview_id}
#716 / #717 (off the sign-in path) ──> sso_group_observations (their own issues)
```

No new boundary. The claim crosses **B8** (`SSO.md`): it is an observation of
what the company's IdP said about this person at the moment it issued the
token, and it is trusted only within the IdP's company, as SSO linking already
is (`SSO.md`, T15). `_claim_sources` in an overage token names an endpoint the
IdP chose. The backend never calls it (G4), so it never becomes a new
boundary. The Graph and Directory sync (#716) and SCIM (#717) cross their own
boundaries and are threat-modelled in their issues; this design only reserves
the observation's `source` column for them.

Design rule 1 (`docs/POSITIONING.md`): the Owner's mapping, exclusions and
manual exceptions are the configuration; the claim is the observation, kept in
its own table (`sso_group_observations`); a second Owner's approval is a
decision recorded by the backend from that Owner's session, never a claim
carried by the request. Team membership is derived from these by a pure
function. None of them shares a field with another.

## Threats

| ID | Boundary | STRIDE | Threat | L | I | Sev | Mitigation (all new unless marked) |
|---|---|---|---|---|---|---|---|
| G1 | B8 | E | **An IdP asserting a group to grant Manager**: a person who can edit groups at the IdP (an IT admin who is only a Viewer here, or an attacker with that right) puts someone in a group mapped to a Manager team | M | H | High | Bounded as SSO linking is: the claim reaches only teams of `conn.company_id` and only people who are already members of it (R4, R5). A team never reaches Owner (existing: `teams.effective_role`), and the cap can never be `Owner`. New: `sso_connections.group_role_cap`, `Viewer` by default; a mapping to a team above the cap is refused on save (R16), and a team whose role is raised above the cap afterwards is skipped at sign-in, audited `skipped: role_above_cap` (R6). Raising the cap to `Manager` is an Owner's explicit, audited change, and the page warns that anyone who can edit those groups at the IdP can then make people Managers. Even then, each pair to a Manager team is only a pending proposal until a second Owner approves it (R23–R25, G19); a mapped team's role cannot be raised to Manager in place (R16). Every grant is in the audit chain and in the access review |
| G2 | B8 | E | Cross-tenant: company A's IdP changes the teams of a person who is also in company B | M | H | High | The mapping writes only teams with `company_id == conn.company_id`, checked again at apply time and not only on save (R5); a person who is not a member of that company is never added to a team (R4, existing rule `TEAMS_AND_USAGE.md` T3); company B's teams are never read |
| G3 | B8 | D | **A missing claim removes access**: the IdP stops sending the claim (configuration change, scope, a token from another flow) and every person loses their teams | M | H | High | An absent claim changes nothing (R7); only a claim that is present and an empty list removes, and it removes only from mapped SSO-managed teams and never a manual exception (R9, R11). Malformed or overage claims also change nothing (R8, G4) |
| G4 | B8 | D, I | **Entra group overage**: past 200 groups in a JWT, Entra leaves `groups` out and puts `_claim_names: {"groups": "src1"}` and `_claim_sources: {"src1": {"endpoint": …}}` (Graph) in the token. Read as absent, that is safe; read as empty, it would remove everyone. Calling the endpoint would be an SSRF to a URL the IdP chose, on the sign-in path | M | H | High | When `_claim_names` names the configured claim (or `hasgroups` is true), the state is `overage`: no change, observation and audit row record it with the source endpoint's **host** only, never fetched (R8). Graph is never called from the sign-in path: the reviewed group sync of #716 runs off it, with its own entry in `http_adapters`. For Entra the recommended claim is `roles` (app roles), which has no overage (R17). The Owner page shows the last sign-in that hit overage, and the operations runbook names the Entra fixes (app roles, or "groups assigned to the application") |
| G5 | B8 | — | **Google groups are not in the token**: Google's ID token has no groups claim; groups are only in the Admin SDK Directory API | H | L | Low | Mappings are refused on save for the `google` preset (422 `groups_unavailable`, R17) until the Directory sync of #716 is enabled for the connection; even then the sign-in path reads nothing for Google and the sync alone applies them. No Directory API call from sign-in |
| G6 | B8, B1 | S, T | **Replay or a stale token**: an old ID token, or the slower of two concurrent sign-ins, applies groups that the IdP has since changed | L | M | Medium | Existing: the `state` is single use, the nonce is checked and `exp`/`iat` are validated (`SSO.md` T1, T2), so a token is applied once, inside the flow that asked for it. New: a token whose `iat` is older than the `token_iat` of the last applied sign-in observation for this connection and person is recorded with `skipped: stale_token` and changes nothing (R10); the apply runs under an advisory lock per company and person (`services/platform/locks`), so two sign-ins do not interleave |
| G7 | B1 | T | **The mapping touches people the Owner placed by hand**: an Owner-managed team, or the members of a team converted to SSO | M | M | Medium | Only teams with `membership_source = 'sso'` can be mapped (R16) or changed by the mapping (R5); the Owner's member routes refuse to add to an SSO-managed team (409 `team_managed_by_sso`, R18). A team with members becomes SSO-managed only after a preview and an explicit confirmation (R19, G22), and every member it had becomes a **manual exception** that the mapping never removes (R11, R26) |
| G8 | B1 | E | A Manager, Viewer or API token writes the mapping, the claim name or the cap, approves a pair, creates or lifts an exclusion, or switches a team to SSO | L | H | High | Existing pattern (`SSO.md` T7, `TEAMS_AND_USAGE.md` T1): a signed-in human Owner only, API tokens refused whatever their role; the routes declared in `docs/architecture/authorization.json`; tests (R15) |
| G9 | B8 | D | A huge or odd claim (thousands of values, very long strings, nested objects, a string instead of a list) exhausts the sign-in or matches by accident | L | M | Low | The claim must be a list of strings, at most 1000 values of at most 256 characters each; anything else is `malformed`, which changes nothing and is audited (R8). Matching is exact string equality on stored values, with no pattern or prefix matching |
| G10 | — | R | **A team change by the mapping leaves no record, or no record of why** | M | M | Medium | One `sso_group_observations` row per sign-in on a connection with mapping on (state, matched values, count, the backend's sha256 of the sorted list, the token's `iat`, what was applied or skipped); one signed, chained `sso_group_sync` audit row listing each change and the observed values that caused it; the change, the observation, the periods and the audit row are one commit (R12, R13) |
| G11 | — | T, R | **The membership history misattributes a mapping-made change** (an Owner shown as actor, or a period with no link to the claim that caused it) | M | M | Medium | Periods are opened and closed through #711's `teams.open_membership` / `close_memberships` with `by = "sso-mapping:<connection_id>"` and the new `started_observation_id` / `ended_observation_id` pointing at the observation (R14); a break-glass removal closes with `by = "owner:<user_id>"` and the exclusion's id (R27). The ORM hook of #711 is widened only to let `ended_observation_id` or `ended_exclusion_id` be written together with `ended_at`, once |
| G12 | B1 | T | Removing a mapping pair, or turning the mapping off, silently leaves or silently strips memberships | M | M | Medium | Removing a pair removes, in the same transaction, the memberships that the person's last applied observation justified only through that pair, never a manual exception (R20), audited `sso_group_mapping_updated` with the removals. Adding a pair applies at each person's next sign-in, or at the next run of the syncs of #716 and #717, never from stored group lists (R21). Turning the mapping off changes no membership (R22) |
| G13 | B1, B8 | E | Access that the IdP has revoked lasts here (the person is removed from the group at the IdP but does not sign in again) | M | M | Medium | Until #717: residual, as for SSO itself (`SSO.md`, Residual risks), since the mapping acts only at sign-in. An Owner uses the break-glass exclusion (R27), removes the person from the company, or removes the pair (R20); the observation table shows when each person's groups were last seen. SCIM deprovisioning (#717, deprovisioning first) and the group sync (#716) close it |
| G14 | B8 | I | The observation or audit row stores every group the person is in (up to 200 ids or names, some of them revealing) | M | L | Low | Data minimisation, decided: only the values that match a mapping are stored, with the count and the backend's sha256 of the sorted full list, so a later observation can be compared without keeping the list (R12). Readable only by the company's Owners. New pairs reach people through the syncs of #716 and #717, not through stored lists (R21) |
| G15 | B8 | S | Group values that look alike: Entra display names are not unique, and synced groups can be emitted as `sAMAccountName` | M | M | Medium | For the `entra` preset with the `groups` claim a value must be an object id (a GUID), lower-cased on save, and the claim's values are compared lower-cased (R17). With the recommended `roles` claim the values are the application's own app role values, which the Entra admin defines once per application, compared exactly; for other presets the Owner writes the value the IdP emits, compared exactly |
| G16 | B1 | D | A mapping error stops people signing in | L | M | Low | The apply is a pure function of (claim, mapping, exclusions, manual exceptions, current memberships), and every refusal inside it is a recorded skip, not an error. Only a database failure aborts, and it rolls back the sign-in with its rows (R13), as a failed audit write does today |
| G17 | B1 | E | The Owner's emergency lever is gone: the Owner cannot take a person out of an SSO-managed Manager team by hand | M | M | Medium | Decided: the break-glass exclusion (R27–R29). An Owner removes the person at once; a pinned exclusion stops the mapping adding them back until an Owner lifts it; audited, the other Owners notified, listed in the access review. Removing the pair (R20), switching the team (R19) and removing the person from the company (existing) remain |
| G18 | — | — | Does any step let a model decide? | — | — | — | No: the claim, the mapping, the approvals, the exclusions and the rules are data and pure functions (POSITIONING rule 5) |
| G19 | B1 | S, E, R | **A second-Owner approval forged or self-approved**: the proposer approves their own pair (a second tab, an API token, a second account), sends an approver id in the body, replays an old approval onto a changed proposal, or manufactures the single-Owner path by demoting or removing the other Owner first | M | H | High | The approver is the signed-in human Owner of the session, never a field of the request; API tokens refused (R15). The approver's account id must differ from the proposer's, matched as `sod.actor_of` matches (account id, else email), else 403 and a `sod_refused` row (R24). The approval binds to `proposal_sha256`, computed by the backend at proposal time over the connection, group value, team, team role and cap; it is refused if any of them has changed since, or after `expires_at` (7 days) (R24). The single-Owner confirmation is honoured only while the company has exactly one Owner, never when an Owner was demoted or removed in the previous 30 days (read from the audit chain), needs a typed confirmation and a reason of 10–500 characters, is audited `sso_manager_pair_single_owner`, and stays flagged in the access review until a second Owner, once there is one, confirms or removes it (R25). Residual: two colluding Owners |
| G20 | B1 | D, R | **A break-glass exclusion abused to lock someone out** of a team the IdP grants (an Owner settling a dispute, an attacker holding an Owner account), quietly and for good | M | M | Medium | It is an Owner's act on an Owner's power: it reaches only SSO-managed teams of the company, never the company membership or role (an Owner can already remove a person from the company). Visible by design: a reason of 10–500 characters, a signed audit row `sso_team_exclusion_created`, every other Owner notified in the same transaction (outbox), the open exclusion shown on the team and on the person, and listed with its age in every access review until lifted (R27–R29). Any Owner may lift it, with a reason, audited (R28). Lifting re-adds nobody by itself: the next sign-in or sync decides (R28) |
| G21 | B1 | E | **Manual exceptions as a lasting back door**: people kept by hand in an SSO-managed team outlive their removal at the IdP | M | M | Medium | Created only by the confirmed conversion (R19, R26), never added later by hand (R18). Visible on the team page, recorded in `sso_manual_exceptions`, and listed in every periodic access review (`services/platform/access_review`), where an Owner confirms or ends each one; an Owner may end one at any time through the member routes (R26). The mapping never removes them, and never re-adds them once ended unless the claim matches |
| G22 | B1 | T | **A conversion preview that no longer matches**: members are added or removed between the preview and the confirmation, so the Owner confirms something other than what they saw | L | M | Low | The preview is stored with the backend's hash of the team's member set and role, expires after 15 minutes, and is single use; the switch is refused (409 `preview_stale`) if the hash has changed, and the audit row `team_updated` records the preview id and the manual exceptions created (R19) |

Assume breach. If the company's IdP is compromised, the attacker can put any
member of that company in any SSO-managed team the company has mapped, up to
the cap (`Viewer` by default), and into a Manager team only through a pair two
Owners approved; never Owner, never in another company, never past a pinned
exclusion, and each change is recorded with the claim as observed. Today the
same attacker can already sign in as any of those people. If one Owner's
account is taken over, the mapping gives the attacker no power the Owner did
not have, and a new Manager pair still needs a second Owner (G19); in a
single-Owner company the confirmation is flagged in the access review.

## Data model (migration `0053`)

- `sso_connections`: `groups_enabled` (bool, default false),
  `groups_claim` (text, default `groups`, proposed `roles` for the `entra`
  preset; 1–64 characters, letters, digits, `_`, `-`, `.`, `:` and `/`, so a
  namespaced claim such as `https://example.com/groups` is allowed),
  `group_role_cap` (`Viewer` | `Manager`, default `Viewer`; a check
  constraint keeps `Owner` out).
- `teams.membership_source` (`owner` | `sso`, default `owner`; existing teams
  stay `owner`).
- `sso_group_mappings` (active pairs only): `id`, `company_id` (FK),
  `connection_id` (FK, cascade), `group_value` (text ≤ 256), `team_id` (FK
  `teams.id`, cascade), `created_by`, `created_at`, `approval_id` (null
  unless the team's role was Manager when the pair was made); unique
  `(connection_id, group_value, team_id)`; at most 200 rows per connection.
- `sso_manager_approvals` (pending Manager pairs; a row is never deleted, and
  only its decision fields are written, once): `id`, `company_id`,
  `connection_id`, `group_value`, `team_id`, `team_role` and `group_role_cap`
  at proposal time, `proposal_sha256` (computed by the backend), `requested_by`
  (the proposer's account id, from the session), `requested_at`, `expires_at`,
  `state` (`pending` | `approved` | `rejected` | `withdrawn` | `expired` |
  `confirmed_single_owner`), `decided_by` (from the session), `decided_at`,
  `reason` (text, required for `confirmed_single_owner`), `reviewed_in` (the
  `access_reviews` id in which a second Owner confirmed a single-Owner pair,
  null until then).
- `sso_team_exclusions` (pinned break-glass exclusions): `id`, `company_id`,
  `team_id` (FK, cascade), `user_id`, `created_by`, `created_at`, `reason`,
  `lifted_by`, `lifted_at`, `lift_reason` (all three null while in force);
  at most one row in force per `(team_id, user_id)` (a partial unique index).
- `sso_manual_exceptions`: `id`, `company_id`, `team_id` (FK, cascade),
  `user_id`, `created_by` (the Owner who confirmed the conversion),
  `created_at`, `preview_id`, `ended_by`, `ended_at` (null while in force);
  at most one row in force per `(team_id, user_id)`.
- `team_membership_source_previews`: `id`, `company_id`, `team_id`,
  `created_by`, `created_at`, `expires_at`, `members_sha256` (the backend's
  hash of the sorted member ids and the team's role), `members` (JSON list of
  user ids that would become manual exceptions), `used_at`.
- `sso_group_observations` (append-only, no update or delete, the same ORM
  guard as #711's periods): `id`, `company_id`, `connection_id` (no FK, so it
  outlives the connection), `user_id`, `source` (`sign_in`; `directory_sync`
  and `scim` reserved for #716 and #717), `observed_at` (the backend's clock),
  `token_iat` (null unless `sign_in`), `claim_name`, `state` (`present` |
  `empty` | `absent` | `overage` | `malformed`), `value_count`,
  `values_sha256` (computed by the backend over the sorted, de-duplicated
  values; null unless present or empty), `matched_values` (JSON list, the
  mapped values seen), `overage_host` (text, null), `applied` (bool: true when
  the state was `present` or `empty` and no whole-observation reason stopped
  it, whether or not any membership changed), `skipped` (JSON list of
  `{team_id, reason}`: `role_above_cap`, `not_sso_managed`, `other_company`,
  `excluded`, `manual_exception`, and the whole-observation reasons
  `stale_token`, `not_a_member`, `groups_disabled`).
- `team_membership_periods` (from #711): `started_observation_id`,
  `ended_observation_id` and `ended_exclusion_id`, nullable, no FK; null for
  other Owner-made changes.

## Rules (EARS; the code PR tests each one)

**Where it applies**

- **R1.** When an SSO sign-in completes on a connection where
  `groups_enabled` is true, the system shall evaluate the group mapping once,
  after `resolve_identity` and in the same transaction as the sign-in.
- **R2.** The system shall read the groups only from the ID token that
  `oidc.validate_id_token` has just validated, never from the userinfo
  endpoint, an access token, a request parameter, a stored token or the
  syncs of #716 and #717; the sign-in path shall make no request to Graph or
  the Directory API.
- **R3.** When an identity is linked from inside a session
  (`sso_link.complete`), the system shall not evaluate the group mapping.
- **R4.** If the person is not a member of `conn.company_id` after
  `resolve_identity` (auto-join included), then the system shall change no
  team and shall record the observation with `skipped: not_a_member`.
- **R5.** The system shall add or remove team memberships only for teams that
  have at least one active mapping on this connection, whose `company_id`
  equals `conn.company_id` and whose `membership_source` is `sso`; any other
  team is never read for writing. A pending approval is not a mapping.
- **R6.** If a mapped team's role is above `conn.group_role_cap`, then the
  system shall not add anyone to it, and shall record
  `{team_id, reason: role_above_cap}`. Removals from it still apply.

**Reading the claim**

- **R7.** If the configured claim is absent from the ID token and the token
  has no overage marker, then the system shall change no membership and shall
  record the state `absent`.
- **R8.** If `_claim_names` names the configured claim, or `hasgroups` is
  true, then the system shall record the state `overage` with the host of the
  matching `_claim_sources` endpoint (if any) and change no membership; the
  system shall not make any network request for it. If the claim is not a
  list of strings, has more than 1000 values or a value longer than 256
  characters, then the system shall record `malformed` and change no
  membership.
- **R9.** If the claim is present and an empty list, then the system shall
  remove the person from every team under R5, except where R11 keeps them,
  and record the state `empty`.
- **R10.** If the token's `iat` is earlier than the `token_iat` of the latest
  `sign_in` observation for this connection and person whose `applied` is
  true, then the system shall record the observation with
  `skipped: stale_token` and change no membership. The system shall evaluate
  R10 and apply the changes under the advisory lock `sso-groups` keyed by
  company and person.
- **R11.** While the claim is present, the system shall make the person a
  member of each team under R5 that has a mapping whose `group_value` is in
  the claim, and not a member of each team under R5 that has none, with two
  exceptions: while an exclusion is in force for the person and team, the
  system shall not add them and shall record `excluded`; while a manual
  exception is in force for them, the system shall not remove them and shall
  record `manual_exception`. A team reached by several values counts once; a
  person already in the desired state is left unchanged and gets no period and
  no audit entry for that team.

**Records**

- **R12.** On each evaluation, the system shall write one
  `sso_group_observations` row with `source = sign_in`, storing the matched
  values, the value count and the backend-computed `values_sha256`, never the
  unmatched values.
- **R13.** When the evaluation changes at least one membership or records
  `overage`, `malformed`, `stale_token`, `role_above_cap` or `excluded`, the
  system shall write one `sso_group_sync` audit row (actor
  `sso-mapping:<connection_id>`, with the user id, issuer, subject, claim
  state, matched values, count, `values_sha256`, observation id and the list
  of `{team_id, team_name, team_role, action, because}`). The membership rows,
  the periods, the observation and the audit rows shall be committed together
  with the sign-in's `sso_sign_in` row, or none of them.
- **R14.** When the mapping adds a person to a team, the system shall call
  `teams.open_membership(…, by="sso-mapping:<connection_id>")` and set
  `started_observation_id`; when it removes one, `teams.close_memberships`
  with the same `by` and `ended_observation_id`. The system shall never edit
  a closed period (#711's guard).

**Configuration**

- **R15.** The system shall accept `GET`/`PUT /v1/companies/{id}/sso/group-mappings`,
  the approval, rejection and single-Owner confirmation routes, the exclusion
  routes, the conversion preview, and changes to `groups_enabled`,
  `groups_claim`, `group_role_cap` and `teams.membership_source` only from a
  signed-in human Owner of the company; an API token shall be refused (403)
  whatever its role. The acting Owner shall be read from the session only.
- **R16.** If a mapping names a team of another company, a team whose
  `membership_source` is `owner`, or a team whose role is above
  `group_role_cap`, then the system shall refuse the save (422) and change
  nothing. If a team that is mapped would have its role raised (to Manager, or
  above the cap), then the system shall refuse the `PATCH` (409
  `team_mapped`); the Owner removes its pairs first.
- **R17.** Where the connection's preset is `google` and the Directory sync of
  #716 is not enabled for it, the system shall refuse to save mappings (422
  `groups_unavailable`). Where the preset is `entra`, the system shall propose
  `roles` as the claim; where the configured claim is `groups`, it shall
  accept only GUID group values, store them lower-cased and compare the
  claim's values lower-cased; where it is `roles`, it shall compare exactly.
- **R18.** If an Owner adds a member to a team whose `membership_source` is
  `sso` through the team member routes, then the system shall refuse it (409
  `team_managed_by_sso`). If an Owner removes such a member through those
  routes, the system shall remove a manual exception (R26) and shall refuse
  any other with 409 `use_break_glass`, naming R27's route. Removing a person
  from the company shall still drop their SSO-managed team memberships
  (`teams.drop_memberships`, existing) and end their exclusions and manual
  exceptions in that company.
- **R19.** When an Owner asks to switch a team from `owner` to `sso`, the
  system shall first require a preview (`POST …/membership-source-preview`)
  listing every current member and that each will be kept as a manual
  exception, and shall switch only on a `PATCH` that carries that unexpired,
  unused `preview_id` and `confirm: true` while the team's `members_sha256`
  is unchanged (else 409 `preview_stale`); a team with no members needs no
  preview. When an Owner switches a team from `sso` to `owner`, the system
  shall keep its members and their open periods, end its manual exceptions
  and lift its exclusions (`by = "owner:<user_id>"`, reason `team switched to
  owner`), refuse it while a mapping or a pending approval targets the team
  (409), and audit `team_updated` with the change and the preview id.
- **R20.** When an Owner removes a mapping pair, the system shall, in the same
  transaction, remove from that team each person whose latest applied
  observation matched that pair's value and no other value still mapped to the
  same team, unless a manual exception keeps them, with periods closed
  `by = "sso-mapping:<connection_id>"` and `ended_observation_id` null, and
  one `sso_group_mapping_updated` audit row listing the pairs added and
  removed and the memberships removed.
- **R21.** When an Owner adds a mapping pair (or a Manager pair is approved),
  the system shall change no membership until each person's next SSO sign-in
  or the next observation of them by the syncs of #716 or #717; it shall
  never apply a pair from stored group lists.
- **R22.** When an Owner sets `groups_enabled` to false, the system shall
  change no membership, and later sign-ins shall record nothing for groups.

**Manager pairs: four-eyes (decision 1, #543)**

- **R23.** When a `PUT` adds a pair whose team's role is Manager, the system
  shall not create the pair; it shall create a `pending`
  `sso_manager_approvals` row with the backend's `proposal_sha256`, expiring
  7 days later, audit `sso_manager_pair_proposed`, and return it as pending.
  The pair shall take effect only when R24 or R25 decides it.
- **R24.** When an Owner approves a pending pair, the system shall create the
  pair with its `approval_id` and audit `sso_manager_pair_approved` only if
  the approver's account differs from `requested_by` (matched as
  `sod.actor_of` matches), the row is unexpired and `pending`, and the
  `proposal_sha256` recomputed now equals the stored one. If the approver is
  the proposer, the system shall refuse it (403) and write a `sod_refused`
  row; if the proposal has expired or changed, refuse it (409) and mark it
  `expired` or leave it for the proposer to withdraw. Any Owner may reject a
  pending pair, and the proposer may withdraw it, audited.
- **R25.** Where the company has exactly one Owner, and no Owner was demoted or
  removed in the previous 30 days, the system shall let that Owner confirm
  their own pending pair with a typed confirmation and a reason of 10–500
  characters, state `confirmed_single_owner`, audited
  `sso_manager_pair_single_owner`. While such a pair has no `reviewed_in`, the
  access review shall list it as flagged; once a second Owner exists, the
  system shall ask that Owner to confirm it (setting `reviewed_in`) or remove
  it in the next review.

**Manual exceptions (decision 5)**

- **R26.** When a team is switched to `sso` under R19, the system shall create
  one `sso_manual_exceptions` row per member listed in the preview. The
  mapping shall never remove a person while their manual exception is in
  force (R9, R11, R20). An Owner may end one through the member routes
  (R18), which removes the person from the team (period closed
  `by = "owner:<user_id>"`) and audits `sso_manual_exception_ended`; the
  access review lists every one in force for an Owner to confirm or end.

**Break-glass exclusion (decision 2)**

- **R27.** When an Owner creates an exclusion for a person in an SSO-managed
  team (`POST …/teams/{team_id}/exclusions` with `user_id` and a reason of
  10–500 characters), the system shall, in one transaction, record it, remove
  the person from the team if they are a member (period closed
  `by = "owner:<user_id>"` with `ended_exclusion_id`), write a signed
  `sso_team_exclusion_created` audit row and queue a notification to every
  other Owner of the company in the outbox.
- **R28.** While an exclusion is in force, the mapping shall not add the person
  to that team (R11). When any Owner lifts it (`DELETE …/exclusions/{id}`
  with a reason), the system shall record `lifted_by`, `lifted_at` and
  `lift_reason`, audit `sso_team_exclusion_lifted`, notify the other Owners,
  and add nobody until the next sign-in or sync observation.
- **R29.** The access review shall list every exclusion in force, with its
  creator, reason and age, and every manual exception (R26).

## Tests the code PR adds

`backend/tests/test_sso_groups.py` (the pure evaluation: R5–R11, one case per
claim state, Entra overage with `_claim_names`/`_claim_sources` and with
`hasgroups`, an empty list, a malformed claim, more than 1000 values, the
cap, another company's team, an Owner-managed team, stale `iat`, an exclusion
in force never re-added, a manual exception never removed by an empty or
non-matching claim, a pending approval not applied) and
`test_sso_group_sign_in.py` (the fake IdP of `test_sso_flow.py`: a sign-in
adds and removes, the periods carry `sso-mapping:<id>` and the observation
id, the audit row holds the matched values and never an unmatched one, a
failed audit write rolls back the memberships and periods with the sign-in, no
HTTP request leaves for an overage token or any sign-in, linking in a session
applies nothing, auto-join then mapping in one sign-in) and
`test_sso_group_admin.py` (R15–R22, R26: the Owner only, API token refused,
Google refused without #716, Entra GUIDs for `groups` and exact `roles`,
raising a mapped team's role refused, the conversion preview, confirmation and
`preview_stale`, manual exceptions created and ended, member routes refused on
an SSO-managed team, removing a pair removes only what it alone justified) and
`test_sso_group_governance.py` (R23–R25, R27–R29: a Manager pair stays
pending, self-approval refused with a `sod_refused` row, an approver id in the
body ignored, an API token refused, a changed or expired proposal refused, the
single-Owner confirmation honoured only with one Owner and refused after a
recent demotion, flagged in the access review; an exclusion removes at once,
notifies the other Owners, survives sign-ins and is lifted by another Owner;
both listed in the access review). Each needs its row in `docs/TESTING.md`
and its context (platform) in `docs/architecture/contexts.json`.

## Residual risks

- Until #717 and #716, the mapping acts only at sign-in (G13): someone the IdP
  removed from a group keeps the team until they sign in again by SSO, an
  Owner uses the break-glass exclusion, removes the pair or the person, or
  their session ends (`REFRESH_TOKEN_ABSOLUTE_DAYS`). A person who signs in by
  password (break-glass) or keeps a refreshed session is never re-evaluated.
- An Entra tenant using the `groups` claim whose users are in more than 200
  groups never gets a mapping applied at sign-in (G4) until the Owner moves to
  the `roles` claim, limits the claim to the groups assigned to the
  application, or enables the sync of #716.
- Google Workspace companies can use the mapping only through #716 (G5).
- Two colluding Owners can approve a Manager pair, and a single-Owner company
  can confirm its own; the second is flagged in the access review (G19).
- With an approved Manager pair, the IdP's group administrators decide who is
  a Manager here for that team. Two Owners chose that, and it is audited.
- Manual exceptions and exclusions last until an Owner ends them; the access
  review is what brings each one back in front of an Owner (G20, G21).

## Decisions (2026-11-19)

The owner answered the seven open questions of the first draft, for a
corporate-scale product:

1. **The cap.** The default stays `Viewer`, and the cap is never `Owner`. A
   mapping that grants Manager needs a second Owner's approval (four-eyes,
   consistent with segregation of duties, #543,
   [`SEGREGATION_OF_DUTIES.md`](SEGREGATION_OF_DUTIES.md)); a company with a
   single Owner may still do it with explicit confirmation, audited and
   flagged in the access review (`services/platform/access_review`). R23–R25,
   G1, G19.
2. **Emergency removal.** Allowed as a break-glass action: an Owner removes a
   person from an SSO-managed team at once; this creates a pinned exclusion
   that the mapping honours until an Owner lifts it; audited; the other
   Owners are notified. R27–R29, G17, G20.
3. **Graph and the Directory API.** A reviewed Microsoft Graph and Google
   Directory group sync, off the sign-in path, is issue #716. The sign-in path
   never calls it. R2, R17, G4, G5.
4. **What is stored.** Only the matched values, the count and a
   backend-computed hash (data minimisation). New pairs apply without waiting
   for a sign-in through the syncs of #716 and #717, not by storing full group
   lists. R12, R21, G14.
5. **Converting a team with members.** Allowed only after a preview of who
   would change and an explicit confirmation; members an Owner added by hand
   become visible manual exceptions that the mapping never removes and that
   appear in the periodic access review. R19, R26, G7, G21, G22.
6. **SCIM.** SCIM 2.0 provisioning, deprovisioning first, is issue #717. G13.
7. **Entra app roles.** For Entra, app roles (the `roles` claim) are the
   recommended claim; groups remain supported as the fallback. R17, G4, G15.

## What was taken, and from where

From AixleHQ/flow's `docs/design/federated-identity.md` (Apache-2.0, design
only, as `SSO.md` already cites it): an assertion is checked against the
connection it claims to satisfy and grants nothing outside it. From the
public documentation of Microsoft Entra ID (the group overage claim,
`_claim_names`/`_claim_sources`, `hasgroups`, groups assigned to the
application, app roles in the `roles` claim) and of Google Workspace (groups
only through the Directory API): the behaviours this design must survive, not
code. The four-eyes rule and the single-Owner exception follow this
repository's own segregation of duties (#543). The absent-versus-empty rule,
the cap, Owner-managed versus SSO-managed teams, the stale-token check, the
observation table, the pinned exclusion and the manual exceptions are ours.
From the reference agents named in `CLAUDE.md` (Copilot coding agent, Codex
cloud, OpenHands, Devin, Kiro, Factory): nothing; none of them maps
identity-provider groups to teams.
