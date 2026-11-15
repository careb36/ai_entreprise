# Claude Code skills

Project skills for Claude Code. Each folder holds a `SKILL.md` (YAML
frontmatter `name` and `description`, then the instructions) and, where the
material is long, a `reference.md` beside it. Claude Code loads a skill when a
task matches its description; a person can also ask for one by name
(`/sdl-review`).

| Skill | Use it for | Based on |
|---|---|---|
| [`sdl-review`](sdl-review/SKILL.md) | Reviewing a diff, PR or plan before opening a pull request that touches auth, secrets, cryptography, the daemon or sandbox, NATS, dependencies, CI or agent-executed code; findings with severity, file:line, practice and fix | Microsoft SDL practices 2–9 |
| [`threat-model`](threat-model/SKILL.md) | A short STRIDE threat model of a new feature or design over this system's trust boundaries, saved in `docs/design/` or the PR | Microsoft SDL practice 3 |
| [`sdl-program`](sdl-program/SKILL.md) | The quarterly or pre-release review of `SECURITY.md`, the bug bar, exceptions, security metrics, the incident plan and training notes | Microsoft SDL practices 1, 9, 10 |

The practices, their sources and the map of what La Fabrique already has for
each are in [`sdl-review/reference.md`](sdl-review/reference.md). Source:
https://www.microsoft.com/en-us/securityengineering/sdl/practices. The skills
paraphrase Microsoft's guidance in this project's own words.

What a skill produces is a model's output: a claim for a person to verify. The
skills do not replace a human security review.

`.gitignore` keeps the rest of `.claude/` (local settings, worktrees) out of
the repository; only `.claude/skills/` is versioned.
