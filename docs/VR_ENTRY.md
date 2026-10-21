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
- **Hosting for the judges:** `VR_BASE=/la-fabrique/vr/ npm run build` and
  publish `vr/dist` (GitHub Pages or any static host). The link goes into the
  Devpost entry.

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
- [ ] Comfort and polish: head-locked hint on first entry, haptics-free
  feedback (card lift and colour), larger hit areas, text legibility pass
  at 1.5 m, 60 fps check on a Quest 3 (few draw calls, instanced cards).
- [ ] Agentic interaction: ask the Reviewer agent for its reason aloud (text
  to speech) and show the company's conversation between roles in the room.
- [ ] GitHub Pages deploy workflow and the Devpost description draft.
