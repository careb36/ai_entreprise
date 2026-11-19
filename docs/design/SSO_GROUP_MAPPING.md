# SSO group mapping to teams (issue #703)

Status: draft, written before the code by the `threat-model` skill; a person
reviews it before it guides work · Issue: #703 (split from #512) · SDL
practice 3 · Depends on: single sign-on ([`SSO.md`](SSO.md)), teams and the
team membership history ([`TEAMS_AND_USAGE.md`](TEAMS_AND_USAGE.md), the
history added by PR #711, migration `0052`) · Migration: `0053`, after `0051`
(PR #681) and `0052` (PR #711); nothing here is built yet.

This is its own document rather than another amendment to `SSO.md` (which
holds T1–T33) because it changes teams as much as SSO. Its threats are
numbered G1–G18 so they never collide with `SSO.md`'s T numbers or
`TEAMS_AND_USAGE.md`'s.

## What is built

Per SSO connection, the Owner names the ID token claim that carries the
person's groups (`groups` by default) and writes a list of `(group value,
team)` pairs. Each team is either **Owner-managed** (as today: its members are
added and removed by hand) or **SSO-managed** (its members come only from the
mapping). On each completed SSO sign-in, after the identity has been resolved
and the person is a member of the connection's company, the backend reads the
claim from the ID token it has just validated and sets the person's membership
in the SSO-managed teams that the connection maps, and in no other team. Each
change is written to `team_members`, to the membership history (#711) with the
mapping as its actor, and to the audit chain with the claim as observed. No
model takes part in any step.

Not built here: reading groups from Google's Directory API or Microsoft Graph,
SCIM, mapping groups to a company role directly, and applying groups when a
person links an identity from inside a session (`SSO.md`, T25–T33).

## Assets

- The role boundary in a company (Owner > Manager > Viewer): an SSO-managed
  team is a new way for something outside La Fabrique to grant a role.
- Company isolation: a company's IdP must reach only its own teams and members.
- Access that already exists: a missing or broken claim must not remove it.
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
                     ├── team_members add/remove (SSO-managed, mapped teams only)
                     ├── teams.open_membership / close_memberships (#711)
                     └── audit row `sso_group_sync` (signed, chained)
Owner ─B1─ GET/PUT /v1/companies/{id}/sso/group-mappings   (signed-in human Owner)
Owner ─B1─ PATCH /v1/companies/{id}/teams/{team_id} {membership_source}
```

No new boundary. The claim crosses **B8** (`SSO.md`): it is an observation of
what the company's IdP said about this person at the moment it issued the
token, and it is trusted only within the IdP's company, as SSO linking already
is (`SSO.md`, T15). `_claim_sources` in an overage token names an endpoint the
IdP chose. The backend never calls it (G4), so it never becomes a new
boundary.

Design rule 1 (`docs/POSITIONING.md`): the Owner's mapping is the
configuration; the claim is the observation, kept in its own table
(`sso_group_observations`); team membership is derived from the two by a pure
function. None of them shares a field with another.

## Threats

| ID | Boundary | STRIDE | Threat | L | I | Sev | Mitigation (all new unless marked) |
|---|---|---|---|---|---|---|---|
| G1 | B8 | E | **An IdP asserting a group to grant Manager**: a person who can edit groups at the IdP (an IT admin who is only a Viewer here, or an attacker with that right) puts someone in a group mapped to a Manager team | M | H | High | Bounded as SSO linking is: the claim reaches only teams of `conn.company_id` and only people who are already members of it (R4, R5). A team never reaches Owner (existing: `teams.effective_role`). New: `sso_connections.group_role_cap`, `Viewer` by default; a mapping to a team above the cap is refused on save (R16), and a team whose role is raised above the cap afterwards is skipped at sign-in, audited `skipped: role_above_cap` (R6). Raising the cap to `Manager` is an Owner's explicit, audited change, and the page warns that anyone who can edit those groups at the IdP can then make people Managers. Every grant is in the audit chain and in the access review |
| G2 | B8 | E | Cross-tenant: company A's IdP changes the teams of a person who is also in company B | M | H | High | The mapping writes only teams with `company_id == conn.company_id`, checked again at apply time and not only on save (R5); a person who is not a member of that company is never added to a team (R4, existing rule `TEAMS_AND_USAGE.md` T3); company B's teams are never read |
| G3 | B8 | D | **A missing claim removes access**: the IdP stops sending the claim (configuration change, scope, a token from another flow) and every person loses their teams | M | H | High | An absent claim changes nothing (R7); only a claim that is present and an empty list removes, and it removes only from mapped SSO-managed teams (R9). Malformed or overage claims also change nothing (R8, G4) |
| G4 | B8 | D, I | **Entra group overage**: past 200 groups in a JWT, Entra leaves `groups` out and puts `_claim_names: {"groups": "src1"}` and `_claim_sources: {"src1": {"endpoint": …}}` (Graph) in the token. Read as absent, that is safe; read as empty, it would remove everyone. Calling the endpoint would be an SSRF to a URL the IdP chose, on the sign-in path | M | H | High | When `_claim_names` names the configured claim (or `hasgroups` is true), the state is `overage`: no change, observation and audit row record it with the source endpoint's **host** only, never fetched (R8). Graph is never called from the sign-in path; a Graph or Directory adapter would need its own reviewed issue and an entry in `http_adapters` (open question 3). The Owner page shows the last sign-in that hit overage, and the operations runbook names the Entra fix ("groups assigned to the application") |
| G5 | B8 | — | **Google groups are not in the token**: Google's ID token has no groups claim; groups are only in the Admin SDK Directory API | H | L | Low | Mappings are refused on save for the `google` preset (422 `groups_unavailable`, R17), so an Owner does not believe a mapping is active when it can never apply. No Directory API call (open question 3) |
| G6 | B8, B1 | S, T | **Replay or a stale token**: an old ID token, or the slower of two concurrent sign-ins, applies groups that the IdP has since changed | L | M | Medium | Existing: the `state` is single use, the nonce is checked and `exp`/`iat` are validated (`SSO.md` T1, T2), so a token is applied once, inside the flow that asked for it. New: a token whose `iat` is older than the `token_iat` of the last applied observation for this connection and person is recorded with `skipped: stale_token` and changes nothing (R10); the apply runs under an advisory lock per company and person (`services/platform/locks`), so two sign-ins do not interleave |
| G7 | B1 | T | **The mapping touches an Owner-managed team**, removing or adding people the Owner placed by hand | M | M | Medium | Only teams with `membership_source = 'sso'` can be mapped (R16) or changed by the mapping (R5); the Owner's member routes refuse an SSO-managed team (409 `team_managed_by_sso`, R18). A team becomes SSO-managed only while it has no members (R19), so switching never removes anyone placed by hand |
| G8 | B1 | E | A Manager, Viewer or API token writes the mapping, the claim name or the cap, or switches a team to SSO | L | H | High | Existing pattern (`SSO.md` T7, `TEAMS_AND_USAGE.md` T1): a signed-in human Owner only, API tokens refused whatever their role; the routes declared in `docs/architecture/authorization.json`; tests (R15) |
| G9 | B8 | D | A huge or odd claim (thousands of values, very long strings, nested objects, a string instead of a list) exhausts the sign-in or matches by accident | L | M | Low | The claim must be a list of strings, at most 1000 values of at most 256 characters each; anything else is `malformed`, which changes nothing and is audited (R8). Matching is exact string equality on stored values, with no pattern or prefix matching |
| G10 | — | R | **A team change by the mapping leaves no record, or no record of why** | M | M | Medium | One `sso_group_observations` row per sign-in on a connection with mapping on (state, matched values, count, the backend's sha256 of the sorted list, the token's `iat`, what was applied or skipped); one signed, chained `sso_group_sync` audit row listing each change and the observed values that caused it; the change, the observation, the periods and the audit row are one commit (R12, R13) |
| G11 | — | T, R | **The membership history misattributes a mapping-made change** (an Owner shown as actor, or a period with no link to the claim that caused it) | M | M | Medium | Periods are opened and closed through #711's `teams.open_membership` / `close_memberships` with `by = "sso-mapping:<connection_id>"` and the new `started_observation_id` / `ended_observation_id` pointing at the observation (R14). The ORM hook of #711 is widened only to let `ended_observation_id` be written together with `ended_at`, once |
| G12 | B1 | T | Removing a mapping pair, or turning the mapping off, silently leaves or silently strips memberships | M | M | Medium | Removing a pair removes, in the same transaction, the memberships that the person's last applied observation justified only through that pair (R20), audited `sso_group_mapping_updated` with the removals. Adding a pair applies at each person's next sign-in, never from stored data (R21). Turning the mapping off changes no membership (R22) |
| G13 | B1, B8 | E | Access that the IdP has revoked lasts here (the person is removed from the group at the IdP but does not sign in again) | M | M | Medium | Residual, as for SSO itself (`SSO.md`, Residual risks): the mapping acts only at sign-in. An Owner removes the person from the company, or removes the pair (R20); the observation table shows when each person's groups were last seen. SCIM would close this (open question 6) |
| G14 | B8 | I | The observation or audit row stores every group the person is in (up to 200 ids or names, some of them revealing) | M | L | Low | Only the values that match a mapping are stored, with the count and the backend's sha256 of the sorted full list, so a later observation can be compared without keeping the list (R12). Readable only by the company's Owner (open question 4) |
| G15 | B8 | S | Group values that look alike: Entra display names are not unique, and synced groups can be emitted as `sAMAccountName` | M | M | Medium | For the `entra` preset a group value must be an object id (a GUID), lower-cased on save, and the claim's values are compared lower-cased only for that preset (R17); for other presets the Owner writes the value the IdP emits, compared exactly |
| G16 | B1 | D | A mapping error stops people signing in | L | M | Low | The apply is a pure function of (claim, mapping, current memberships), and every refusal inside it is a recorded skip, not an error. Only a database failure aborts, and it rolls back the sign-in with its rows (R13), as a failed audit write does today |
| G17 | B1 | E | The Owner's emergency lever is gone: the Owner cannot take a person out of an SSO-managed Manager team by hand | M | M | Medium | The Owner removes the pair (R20), switches the team to Owner-managed (R19, members kept, then removable by hand), or removes the person from the company (existing, ends their sessions). Whether a manual removal should also be allowed is open question 2 |
| G18 | — | — | Does any step let a model decide? | — | — | — | No: the claim, the mapping and the rules are data and pure functions (POSITIONING rule 5) |

Assume breach. If the company's IdP is compromised, the attacker can put
any member of that company in any SSO-managed team the company has mapped,
up to the cap (`Viewer` by default), never Owner and never in another
company, and each change is recorded with the claim as observed. Today the
same attacker can already sign in as any of those people. If the Owner's
account is taken over, the mapping gives the attacker no power the Owner
did not have.

## Data model (migration `0053`)

- `sso_connections`: `groups_enabled` (bool, default false),
  `groups_claim` (text, default `groups`; 1–64 characters, letters, digits,
  `_`, `-`, `.`, `:` and `/`, so a namespaced claim such as
  `https://example.com/groups` is allowed), `group_role_cap` (`Viewer` |
  `Manager`, default `Viewer`).
- `teams.membership_source` (`owner` | `sso`, default `owner`; existing teams
  stay `owner`).
- `sso_group_mappings`: `id`, `company_id` (FK), `connection_id` (FK,
  cascade), `group_value` (text ≤ 256), `team_id` (FK `teams.id`, cascade),
  `created_by`, `created_at`; unique `(connection_id, group_value, team_id)`;
  at most 200 rows per connection.
- `sso_group_observations` (append-only, no update or delete, the same ORM
  guard as #711's periods): `id`, `company_id`, `connection_id` (no FK, so it
  outlives the connection), `user_id`, `observed_at` (the backend's clock),
  `token_iat`, `claim_name`, `state` (`present` | `empty` | `absent` |
  `overage` | `malformed`), `value_count`, `values_sha256` (computed by the
  backend over the sorted, de-duplicated values; null unless present or
  empty), `matched_values` (JSON list, the mapped values seen), `overage_host`
  (text, null), `applied` (bool: true when the state was `present` or
  `empty` and no whole-observation reason stopped it, whether or not any
  membership changed), `skipped` (JSON list of
  `{team_id, reason}`: `role_above_cap`, `not_sso_managed`, `other_company`,
  and the whole-observation reasons `stale_token`, `not_a_member`,
  `groups_disabled`).
- `team_membership_periods` (from #711): `started_observation_id` and
  `ended_observation_id`, nullable, no FK; null for Owner-made changes.

## Rules (EARS; the code PR tests each one)

**Where it applies**

- **R1.** When an SSO sign-in completes on a connection where
  `groups_enabled` is true, the system shall evaluate the group mapping once,
  after `resolve_identity` and in the same transaction as the sign-in.
- **R2.** The system shall read the groups only from the ID token that
  `oidc.validate_id_token` has just validated, never from the userinfo
  endpoint, an access token, a request parameter or a stored token.
- **R3.** When an identity is linked from inside a session
  (`sso_link.complete`), the system shall not evaluate the group mapping.
- **R4.** If the person is not a member of `conn.company_id` after
  `resolve_identity` (auto-join included), then the system shall change no
  team and shall record the observation with `skipped: not_a_member`.
- **R5.** The system shall add or remove team memberships only for teams that
  have at least one mapping on this connection, whose `company_id` equals
  `conn.company_id` and whose `membership_source` is `sso`; any other team is
  never read for writing.
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
  remove the person from every team under R5 and record the state `empty`.
- **R10.** If the token's `iat` is earlier than the `token_iat` of the latest
  observation for this connection and person whose `applied` is true, then the
  system shall record the observation with `skipped: stale_token` and change no
  membership. The system shall evaluate R10 and apply the changes under the
  advisory lock `sso-groups` keyed by company and person.
- **R11.** While the claim is present, the system shall make the person a
  member of each team under R5 that has a mapping whose `group_value` is in
  the claim, and not a member of each team under R5 that has none. A team
  reached by several values counts once; a person already in the desired
  state is left unchanged and gets no period and no audit entry for that team.

**Records**

- **R12.** On each evaluation, the system shall write one
  `sso_group_observations` row storing the matched values, the value count
  and the backend-computed `values_sha256`, never the unmatched values.
- **R13.** When the evaluation changes at least one membership or records
  `overage`, `malformed`, `stale_token` or `role_above_cap`, the system shall
  write one `sso_group_sync` audit row (actor `sso-mapping:<connection_id>`,
  with the user id, issuer, subject, claim state, matched values, count,
  `values_sha256`, observation id and the list of
  `{team_id, team_name, team_role, action, because}`). The membership rows,
  the periods, the observation and the audit rows shall be committed together
  with the sign-in's `sso_sign_in` row, or none of them.
- **R14.** When the mapping adds a person to a team, the system shall call
  `teams.open_membership(…, by="sso-mapping:<connection_id>")` and set
  `started_observation_id`; when it removes one, `teams.close_memberships`
  with the same `by` and `ended_observation_id`. The system shall never edit
  a closed period (#711's guard).

**Configuration**

- **R15.** The system shall accept `GET`/`PUT /v1/companies/{id}/sso/group-mappings`
  and changes to `groups_enabled`, `groups_claim`, `group_role_cap` and
  `teams.membership_source` only from a signed-in human Owner of the company;
  an API token shall be refused (403) whatever its role.
- **R16.** If a mapping names a team of another company, a team whose
  `membership_source` is `owner`, or a team whose role is above
  `group_role_cap`, then the system shall refuse the save (422) and change
  nothing. If a team that is mapped would have its role raised above the cap,
  then the system shall refuse the `PATCH` (409).
- **R17.** Where the connection's preset is `google`, the system shall refuse
  to save mappings (422 `groups_unavailable`). Where the preset is `entra`, the
  system shall accept only GUID group values, store them lower-cased, and
  compare the claim's values lower-cased.
- **R18.** If an Owner adds or removes a member of a team whose
  `membership_source` is `sso` through the team member routes, then the
  system shall refuse it (409 `team_managed_by_sso`). Removing a person from
  the company shall still drop their SSO-managed team memberships
  (`teams.drop_memberships`, existing).
- **R19.** If an Owner switches a team from `owner` to `sso` while it has
  members, then the system shall refuse it (409). When an Owner switches a team
  from `sso` to `owner`, the system shall keep its members and their open
  periods, refuse it while a mapping targets the team (409), and audit
  `team_updated` with the change.
- **R20.** When an Owner removes a mapping pair, the system shall, in the same
  transaction, remove from that team each person whose latest applied
  observation matched that pair's value and no other value still mapped to the
  same team, with periods closed `by = "sso-mapping:<connection_id>"` and
  `ended_observation_id` null, and one `sso_group_mapping_updated` audit row
  listing the pairs added and removed and the memberships removed.
- **R21.** When an Owner adds a mapping pair, the system shall change no
  membership until each person's next SSO sign-in.
- **R22.** When an Owner sets `groups_enabled` to false, the system shall
  change no membership, and later sign-ins shall record nothing for groups.

## Tests the code PR adds

`backend/tests/test_sso_groups.py` (the pure evaluation: R5–R11, one case per
claim state, Entra overage with `_claim_names`/`_claim_sources` and with
`hasgroups`, an empty list, a malformed claim, more than 1000 values, the
cap, another company's team, an Owner-managed team, stale `iat`) and
`test_sso_group_sign_in.py` (the fake IdP of `test_sso_flow.py`: a sign-in
adds and removes, the periods carry `sso-mapping:<id>` and the observation
id, the audit row holds the matched values and never an unmatched one, a
failed audit write rolls back the memberships and periods with the sign-in, no
HTTP request leaves for an overage token, linking in a session applies
nothing, auto-join then mapping in one sign-in) and `test_sso_group_admin.py`
(R15–R22: the Owner only, API token refused, Google refused, Entra GUIDs,
switching a team with members refused, member routes refused on an
SSO-managed team, removing a pair removes only what it alone justified). Each
needs its row in `docs/TESTING.md` and its context (platform) in
`docs/architecture/contexts.json`.

## Residual risks

- The mapping acts only at sign-in (G13): someone the IdP removed from a
  group keeps the team until they sign in again by SSO, the Owner removes the
  pair or the person, or their session ends (`REFRESH_TOKEN_ABSOLUTE_DAYS`).
  A person who signs in by password (break-glass) or keeps a refreshed session
  is never re-evaluated.
- An Entra tenant whose users are in more than 200 groups never gets a
  mapping applied (G4) until the Owner limits the claim to the groups assigned
  to the application, or a reviewed Graph adapter exists.
- Google Workspace companies cannot use the mapping at all (G5).
- With the cap raised to `Manager`, the IdP's group administrators decide
  who is a Manager here. That is the Owner's explicit choice, and it is audited.
- Adding a pair does not apply to anyone until their next sign-in (R21),
  because the full claim is not stored (G14).

## Open questions for the owner

1. **The cap.** Is `Viewer` the right default for `group_role_cap`, and
   should a `Manager` cap also need something beyond the Owner's say, such as a
   second Owner's confirmation?
2. **Manual removal from an SSO-managed team.** Should the Owner be able to
   remove one person by hand as an emergency lever (G17), knowing that the next
   sign-in puts them back unless the IdP changes? This design refuses it and
   offers removing the pair, switching the team or removing the person.
3. **Graph and the Directory API.** Should a later issue add a reviewed
   adapter (listed in `http_adapters`, run outside the sign-in path, perhaps by
   a periodic job with an application credential) to resolve Entra overage and
   Google groups? This design does neither.
4. **What is stored.** Is storing only the matched values plus a count and a
   hash enough for audit (G14)? Storing the full list would let a new pair
   apply at once (R21), at the cost of keeping every group id of every person.
5. **Converting a team with members.** Should switching an Owner-managed team
   to SSO be allowed while it has members (R19)? It would mean the next
   sign-in removes people the Owner placed by hand.
6. **SCIM.** Should SCIM provisioning (`SSO.md`, open question 3) replace the
   sign-in-time mapping for removals, so IdP changes apply without waiting for
   a sign-in?
7. **Entra app roles.** Should the `roles` claim (Entra app roles assigned to
   the application) be offered as the recommended claim for Entra? It avoids
   the group overage and lets the Entra admin assign only what this app needs.

## What was taken, and from where

From AixleHQ/flow's `docs/design/federated-identity.md` (Apache-2.0, design
only, as `SSO.md` already cites it): an assertion is checked against the
connection it claims to satisfy and grants nothing outside it. From the
public documentation of Microsoft Entra ID (the group overage claim,
`_claim_names`/`_claim_sources`, `hasgroups`, groups assigned to the
application) and of Google Workspace (groups only through the Directory API):
the behaviours this design must survive, not code. The absent-versus-empty
rule, the cap, Owner-managed versus SSO-managed teams, the stale-token check
and the observation table are ours. From the reference agents named in
`CLAUDE.md` (Copilot coding agent, Codex cloud, OpenHands, Devin, Kiro,
Factory): nothing; none of them maps identity-provider groups to teams.
