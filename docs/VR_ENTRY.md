# Glassroom VR: the competition entry

Le Fabrique verifies the work of AI agents and records the evidence. The web
app shows that on a screen; **Glassroom VR** (`vr/`) shows it in a room: a
seated, hands-first WebXR experience where a person watches the company's
agents work, picks up a task or a run with a pinch, reads the Verifier's
observations beside the Reviewer agent's suggestion, and decides with another
pinch. It is the product's platform integration for the **Meta VR Start
Developer Competition 2026**, submitted in the *Productivity* track as an
*Adapted* entry (a pre-existing project that ships a meaningful new platform
integration during the window).

## What the rules ask for (read 2026-10-21)

Source: the official rules on Devpost
(`start-developer-competition-26.devpost.com/rules`) and Meta's announcement
(`developers.meta.com/blog/meta-connect-2026-vr-start-developer-competition/`).
Check them again before submitting; they win over this summary.

- **Entry period:** September 24, 2026, 10:00 AM PT to **November 18, 2026,
  12:00 PM PT**. Judging to around December 9; winners around December 11.
  The build must stay available until the announcement, unchanged after the
  deadline.
- **Who:** 18+, a Meta account with Developer Access, and a **Meta VR Start
  Program** member by submission time. Residents of Brazil, Quebec and
  sanctioned areas are excluded. One entry per person.
- **What to submit on Devpost:** for a **WebXR (IWSDK) app, a link the
  judges can open** (GitHub Pages or any host is fine; a Unity/Unreal/native
  app would need an APK on the Meta Developer Dashboard instead); a **demo
  video under three minutes**, public on YouTube or Vimeo, filmed on a Quest
  or in the XR Simulator; a **text description** (inspiration, how it was
  built, future plans, the launch date if not on the store yet; for an
  Adapted entry, what the new features are); the **track and division**.
  English, or English subtitles.
- **Must:** be a VR/MR app for Meta devices (Meta VR Glasses, Meta Quest, or
  both), **fully usable with hands end to end** (controllers optional),
  original, built with publicly available tools, within Meta's policies.
  Adapted entries must ship a meaningful new feature, mode or platform
  integration in the window; bug fixes and cosmetics alone do not count.
- **Judging (25% each):** innovation and creativity; experience design;
  technical implementation (at least 60 fps on Quest hardware); polish and
  presentation. Stage one is a viability check.
- **Prizes:** Productivity: $70,000 (winner) and $35,000 (runner-up) for New
  and for Adapted. Special awards of $25,000 each include *Agentic
  Interaction*, *First Five Minutes*, *Accessibility Forward* and *Boldest
  Original Concept*. An entry can win one prize.
- **Design guidance from Meta:** seated-optimized, fast to enter and leave,
  something engaging within the first minutes.

### What the Devpost pages add (rules, FAQ, judging; read 2026-10-21)

- **The Productivity track, in Meta's words:** "apps that make you more
  effective anywhere or are designed around specific daily moments:
  multi-panel workspaces, task management, creative tools, or habits tied to
  a recurring context". The room is a multi-panel workspace for one daily
  moment: deciding on the company's work.
- **Four tests the judges apply to every entry:** hands-first ("can someone
  complete the entire experience without ever pairing a controller?"); the
  *airplane seat test* (every interaction within a 2 ft radius, seated); the
  *one bus stop test* (fast cold start, clean pause and resume, satisfying in
  ten minutes or less); the *take it away test* (remove any third-party
  service and there is still a project: the demo company runs with nothing
  else, the live mode is the product's own API).
- **Adapted division:** the entry must include a one-to-two-paragraph summary
  of what was added since the project's original release; a changelog,
  screenshots and demo videos are "strongly encouraged". The changelog is
  kept below. A ported experience counts as Adapted. Last year's winners may
  enter an update of the same project.
- **Judged cold, on a Quest 3 or 3S**, by a panel that "may use AI tools to
  assist"; the submission is frozen at the deadline, and a build changed
  during judging may be disqualified (a bug fix is allowed only until the
  deadline). Backward compatibility with Quest 2 or Pro is not judged.
- **What the judging page says a high score needs:** innovation that
  "uniquely leverages" the device (spatial depth, hands-first, gaze); design
  with "intuitive navigation from the moment of entry, clear onboarding, and
  a rewarding, habit-forming purpose"; technical reliability at 60 fps,
  "solid and bug-free"; polish, including the submission materials. On the
  video: "show real gameplay that honestly represents the experience … don't
  lean on AI-generated video to carry the pitch".
- **Content rules that touch the room:** no brand names, logos or commercial
  artwork in the project (the demo company's producers are made up:
  `pr-agent`, `issue-agent`; a real company shows its own names in live mode,
  which is the person's data, not the entry's), no identifiable person other
  than the entrants, English or subtitles, content for a 10+, 13+ or 18+
  rating, and no Meta funding behind the project.
- **No headset is required to enter**, but the judges use one; the Meta
  Quest Developer Hub and the XR Simulator cover the video and the testing
  for a person without a device.
- **Help on offer (Start membership required):** mentor sessions every week
  until the deadline, with a *Playtest & React* on October 27 and 29 where
  other developers try the build cold, and *Monday Unblock: Building and
  Testing* on October 26 (10:00 AM PT / 3:00 PM CEST).

## The experience

Three panels on an arc at eye height, within reach of a seated person:

1. **Board** — the sprint's tasks by column (to do, in progress, in review,
   done, blocked), each card with its assignee and rework count.
2. **The company at work** — one card per agent with its state and what it
   is doing, and the last events (deliveries, verdicts, approvals, blocks).
3. **Inbox** — what waits for a person: the tasks blocked until someone
   acts, and the runs of other producers awaiting a decision, each with the
   Verifier's verdict and the Reviewer agent's suggestion.

Below them, the **detail panel** shows whatever was picked up, with the
observations beside the claims, and the actions: *Approve* / *Reject* a run,
*Send back to work* a blocked task. The same rules as the web app: a verdict
is an observation, a suggestion is a claim, and only the person's pinch
decides.

Modes:

- **Demo** (default): a sample company bundled in the page, so a judge can
  open the link with nothing else running. Decisions change the room and
  the log, and are kept for the session.
- **Live**: `?api=<backend url>&company=<id>` reads a real company through
  the existing REST API (`/v1/sprints`, `/v1/sprints/{id}/board`,
  `/v1/inbox`, `/v1/agent_runs?exclude_in_house=true` and each pending run's
  verifications), follows the sprint's Glassroom stream (events in the log,
  agents' states on their cards) and reads the board again every 30 s;
  approvals and send-backs call the same endpoints the web app calls, and
  the room says so if the backend refuses one. `&token=<jwt>` when the
  backend requires a login (the stream takes a ticket), `&sprint=<id>` to
  pick a sprint, `&person=<name>` for who the decisions are recorded as.
  The room's origin must be in the backend's `CORS_ORIGINS`. If the backend
  cannot be read, the room keeps the demo company and says so.

## Running it

```bash
cd vr && npm install
npm run dev          # http://localhost:5173, desktop view with mouse orbit
npm run test:run     # the model, the layout and the HTML shell
npm run build        # vr/dist, a static site
```

- **Headset:** WebXR needs HTTPS. Run `npm run dev -- --host` behind a
  tunnel (or build and host `dist/`), open the URL in the Quest browser and
  press *Enter VR*. Hand tracking must be on in the headset settings.
- **Without a headset:** Meta's Immersive Web Emulator (a browser extension)
  simulates a headset and hands; the desktop view also works with the mouse.
- **The branches:** the competition entry lives on `main_meta` and
  `develop_meta`, apart from the web product's `main` and `develop`, which
  carry none of `vr/`. The room's pull requests land on `develop_meta`;
  `develop_meta` is merged into `main_meta` when a state is worth releasing,
  and `main_meta` is what the judges' link serves (Pages deploys from it, so
  it stays untouched after the deadline). Changes the room needs from the
  product (API shapes, the stream) land on `main` and are merged into
  `develop_meta` from there.
- **Hosting for the judges:** `.github/workflows/vr-pages.yml` builds with
  `VR_BASE=/<repository name>/` and publishes `vr/dist` to GitHub Pages on a
  push to `main_meta` once the repository variable `VR_PAGES` is `1` and
  Pages deploys from GitHub Actions; any static host works too
  (`VR_BASE=/path/ npm run build`). The link goes into the Devpost entry.
- **Nothing from a CDN in the headset:** the font (`vr/public/fonts`, Roboto,
  Apache 2.0) and the hand models (`vr/public/hands`, WebXR Input Profiles,
  MIT) ship with the room, licences beside them; the text drops glyphs the
  font lacks (the backend's emoji) rather than fetch a fallback, and no
  controller model is drawn (a controller still selects; hands are the brief).
- **The frame rate on the headset:** `?stats=1` shows it under the detail
  panel (amber below 60), the number the competition asks for; the browser's
  own counters are not visible inside a session.
- **Eyes-only pick:** `?gaze=1` makes looking at a card for a moment and
  a half pick it, as a pinch would, with a ring in front of the eyes that
  fills while the gaze is held (nothing is picked by surprise; looking away
  resets it). An alternative for a person who cannot pinch, and the gaze
  interaction the judging page names; off by default so reading a card
  never picks it.
- **Live mode and the token:** `&token=` puts a JWT in the page URL, which
  browser history and a host's logs keep; use a short-lived token, or run
  the backend with `AUTH_REQUIRED=false` for a demo on a private network.

## What only a person can do

- Join the Meta VR Start Program and enable Developer Access on the Meta
  account; register on Devpost for the competition.
- Test on a Meta Quest (frame rate, hand tracking, comfort), and record the
  demo video (under three minutes, English or subtitles).
- Write the Devpost description from this document, choose *Productivity* /
  *Adapted*, and submit before November 18, 2026, 12:00 PM PT.
- Keep the hosted build unchanged until the winners are announced.

## Plan

- [x] The room: panels on an arc, cards picked up with a pinch, the detail
  panel with the actions, the demo company, tests of the model and layout.
- [x] Live mode against a backend, with the Glassroom stream in the log.
- [x] Cards lift under the hand and further once picked up; the detail panel
  reads the Reviewer agent's reason or a block's cause aloud (the browser's
  voice; silent where there is none).
- [x] The company's conversation between roles opens the log in live mode.
- [x] GitHub Pages deploy workflow (`vr-pages.yml`, opt-in with the `VR_PAGES`
  variable) and the Devpost description draft below.
- [x] What the Quest pass needs from the code: an entry hint that trails the
  head for the first seconds of a session until the first pinch (never glued
  to the eyes), flat materials for the panels and cards (the lights are for
  the hands), the smallest labels a size up, and the frame rate in the room
  with `?stats=1` (amber under 60).
- [x] An eyes-only alternative to the pinch (`?gaze=1`, a dwell with a
  filling ring), for a person who cannot pinch and for the *Accessibility
  Forward* award; the pinch stays the default.
- [ ] The Quest 3 pass itself, by a person with the headset: the rate with
  `?stats=1` on the hosted build, hit areas and legibility at 1.5 m, hand
  tracking on the cards and the action cards, comfort over ten minutes.

## The bar

Last year's productivity winner, AnExplorer (a file manager a solo developer
brought to the Quest Store from its Android codebase; Meta tells it as a
"Build Faster, Earn More" story), sets the level: not a demo of a technique
but a tool people use every day, that works the first time it is opened,
earns its place on the headset with things a screen cannot do (a big
visual space, hands) and keeps the user coming back. For this entry that
means:

- The room must be **useful before it is impressive**: a person reviews
  real work and records real decisions, through the same API the web app
  uses, and nothing in it is a mock-up when a backend is behind it. The demo
  company exists so a judge can open the link; the product is live mode.
- **Reliability over features**: no CDN in the headset, a reload that keeps
  the room, decisions shown only once recorded, a refusal said out loud.
  Every PR runs the tests and a headless render before it is merged.
- **Polish a judge feels in the first minute**: the entry hint, the company
  working by itself while the person looks around, cards that lift under
  the hand, a reason read aloud, "+n more" instead of overflow.
- **A reason to come back**: the inbox is the day's work, not a one-off
  scene; what waits for the person is counted in the title of the panel and
  changes while they watch.
- **The pitch is the product's**: evidence beside claims, a person's pinch
  decides. The Devpost text and the video show a real company's sprint, not
  the demo.

## The video (under three minutes, a shot list)

Judges need not watch past three minutes, and the judging page asks for
real footage that "honestly represents the experience". Record it on a
Quest 3 (the headset's own capture, or the Meta Quest Developer Hub's
casting) in the hosted build; the demo company is enough, a real company
is better. English, or subtitles.

| Time | What is on screen | What is said (voice or caption) |
|---|---|---|
| 0:00–0:15 | The web app's Review page for a second: the inbox as a list. Cut to the headset view, the room appearing, the entry hint trailing the head. | "Le Fabrique runs software sprints with a company of AI agents and keeps the last word for the evidence. On a screen that review is a list. This is the same inbox as a room." |
| 0:15–0:45 | A slow look across the arc: the board, the company at work with cards changing state, the inbox counting four. The hand reaches a run; the card lifts. | "Three panels within reach, seated. The agents keep working while you look. Four things wait for a person." |
| 0:45–1:30 | Pinch the refuted run. The detail panel: the Verifier's observations, the Reviewer agent's suggestion marked as a claim. Pinch *Hear the Reviewer agent*; the reason is read aloud. Pinch *Reject*; the inbox counts three; the log records who decided. | "The Verifier refuted the test run: a refund can be issued twice. The Reviewer agent suggests rejecting, and says why. A suggestion is a claim; the pinch decides, and it is recorded as mine." |
| 1:30–2:00 | Pinch the blocked task; *Hear why it is blocked*; *Send back to work*. It moves to the board's first column. | "A task the quality gate blocked twice. I hear why, and send it back with one more attempt." |
| 2:00–2:30 | Decide the remaining runs quickly; the inbox empties: "nothing waits for you", the detail panel's "That was the last one". A new run arrives from the stream and the inbox counts one. | "When nothing waits, the room says so. The company keeps working; the inbox fills again when something needs a person." |
| 2:30–2:50 | Live mode: the same room on a real company (`?api=…`), the Glassroom stream's events arriving in the log, a decision recorded, then the web app's Review page showing that decision. | "In live mode the room reads a real company through the product's API, and every decision lands in the product." |
| 2:50–3:00 | The title, the track and division (Productivity, Adapted), the link. | "Glassroom VR. Hands only, seated, ten minutes a day." |

Keep the hands visible in every interaction shot: the competition's test is
that a controller is never needed.

## Changelog (the Adapted division's summary of what is new)

Le Fabrique existed before the entry period as a web product: a company of
AI agents that runs software sprints, verifies their work deterministically
and keeps the evidence for a person to decide on. Everything below was built
in the competition window, on the `develop_meta` and `main_meta` branches, and is the new
platform integration the division asks for: the product in a headset, with
hands.

- **2026-10-21 · The room** (#160): a WebXR app beside the product; three
  panels on an arc (board, company at work, inbox), cards picked up with a
  pinch, a detail panel with the evidence and the actions, a bundled demo
  company.
- **2026-10-21 · Live mode** (#161): a real company read through the
  product's API, its Glassroom stream in the room, decisions recorded through
  the same endpoints the web app uses.
- **2026-10-21 · Hands and voice** (#162): cards lift under the hand and
  further once picked up; the Reviewer agent's reason and a block's cause can
  be heard aloud; the conversation between roles opens the log; a Pages
  deployment for the judges' link.
- **2026-10-21 · Legibility** (#163): titles wrap from the top and are
  clipped to their card.
- **2026-10-21 · The first minute** (#164): the demo company keeps working
  after the room opens: a delivery, the Verifier's observations, a review
  request, a brief, a new run in the inbox.
- **2026-10-21 · Holding up in a headset** (#165): font and hand models
  bundled (nothing from a CDN), reloads that keep the room, decisions shown
  once recorded, the inbox and the agents capped with "+n more".
- **2026-10-21 · The Quest pass** (#167): an entry hint that trails the head
  until the first pinch, flat materials, larger board labels, the frame rate
  in the room with `?stats=1`.
- **2026-10-21 · Tests that render the room** (#169), **the payoff of the
  last decision** (#171): the inbox says when nothing waits and where new
  work shows up.
- **2026-10-21 · Eyes-only pick** (#173): `?gaze=1`, a dwell with a filling
  ring, an alternative to the pinch for a person who cannot make one.
- **2026-10-21 · Fixes from the second review** (#175): the gaze never
  decides (the action cards opt out), the Reviewer agent's suggestion stays
  above the decision cards, a dot shows where each hand's ray lands, a
  clipped label cannot steal a pinch from the next card, the gaze ring sits
  on the card it looks at, a reload in flight cannot undo a decision, the
  text drops the glyphs the bundled font lacks (no CDN fallback), no
  controller models (hands are the brief; a controller still selects).

## Devpost description (draft)

**Inspiration.** Le Fabrique runs software sprints with a company of AI
agents and keeps the last word for the evidence: what the agents claim is
written beside what the platform observed, and a person decides. On a screen
that review is a list. In a headset it can be a room: the board on one side,
the agents at work in front, the inbox on the other, and the evidence of
whatever you pick up at your fingertips. The hands-first brief fit the
product's rule exactly — only the person's pinch decides.

**What it does.** Glassroom VR is a seated, hands-first room. Three panels on
an arc show the sprint's board, the company at work (one card per agent with
what it is doing, and the last events) and the inbox: the tasks blocked
until a person acts, and the runs of other producers awaiting a decision.
A pinch picks a card up; the detail panel shows the Verifier's observations
beside the Reviewer agent's suggestion, and two more pinches decide:
approve or reject a run, send a blocked task back to work. In live mode the
room reads a real company through the platform's API and records each
decision through it; the demo mode runs with nothing else.

**How we built it.** WebXR with react-three-fiber and `@react-three/xr`
(hand tracking through the WebXR Hand Input API; controllers optional), in
a small package beside the product: the room's model and layout are pure
TypeScript with tests, the scene is a few dozen planes and text labels, and
the live mode is an adapter over the REST and SSE endpoints the web app
already uses. Nothing in the room decides on its own: suggestions are shown
as claims, verdicts as observations, and the platform records who pinched.

**What's new since the product's release (Adapted division).** The room
itself: the product had no headset mode before September 24, 2026. In the
window we built the WebXR room and its demo company, the live mode over the
product's API and stream, hand interaction on every card and action, the
reasons read aloud, the entry hint, and the hardening a headset needs (no
CDN, reloads that keep the room, decisions shown once recorded). The
changelog in the repository lists each step.

**What's next.** A pass with testers on a Quest 3 (comfort over ten minutes,
hit areas, the frame rate on the hosted build), the company's conversation
between roles as a panel of its own, and passthrough for the board on a real
desk.

**Launch.** The room ships with the product (open source); the hosted build
is the entry's link.
