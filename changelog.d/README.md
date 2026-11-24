# Changelog fragments

Each pull request records its change here, as one new file, instead of
writing a line into `CHANGELOG.md`. Two pull requests never write the same
file, so they never conflict on the changelog (issue #731). This README also
keeps the folder in git when no fragment is waiting.

## Format

- **Name:** `<issue>-<slug>.md`: the issue number, a hyphen, then lowercase
  words joined by hyphens (`731-changelog-fragments.md`). One pull request may
  add more than one fragment, for example one per section.
- **First line:** the section, exactly one of Keep a Changelog 1.1.0's:
  `Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, `Security`.
- **The rest:** the entry, exactly as it would read under that section of
  `CHANGELOG.md`: a `* **Summary (issue #N).**` bullet with its detail, the
  way the entries under `[Unreleased]` are written. No heading lines (`#`,
  `##`, `###`): the section comes from the first line.

## Describe the diff, not the plan

A fragment says only what the diff of its change shows. Read the diff first
(`git diff --stat` against the base; an agent calls `workspace__git` `changed`
and `diff`), never the plan's or earlier tasks' titles. When La Fabrique's
agents write a fragment, the Verifier's `changelog_matches_diff` check holds
them to it (`docs/contexts/evidence.md`):

- every path you cite in backticks must be a file the diff changed, or a
  folder holding one; to cite unchanged code as context, say so in the same
  sentence with `already`, `existing`, `unchanged` or `untouched`;
- under `Added`, cite only names the diff adds: a flag, function or name that
  already exists on the base must be marked the same way;
- the issue number is the change's own issue.

## Example

`changelog.d/731-changelog-fragments.md`:

```markdown
Changed

* **Changelog fragments: one file per change, collected at release (issue #731).** A pull request records its change as `changelog.d/<issue>-<slug>.md` instead of editing `CHANGELOG.md`.
```

## Commands

```bash
python3 scripts/check_changelog.py              # check every fragment (check.sh and CI run it)
python3 scripts/release_notes.py unreleased     # [Unreleased] with the fragments folded in
python3 scripts/release_notes.py collect        # fold them into CHANGELOG.md and delete them
python3 scripts/release_notes.py release X.Y.Z  # collect, then cut the release (also `cut`)
```

`collect` puts each entry at the top of its section of `[Unreleased]`, in
section order and then by file name, adds a section that is missing, and
refuses (writing nothing) while any fragment has a bad name, an unknown
section, an empty entry or a heading. Running it twice changes nothing.
Contributors never run it: the person cutting a release does, through
`release` (`docs/OPERATIONS.md`, Releasing).
