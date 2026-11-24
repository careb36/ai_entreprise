# Moving lines and deleting files: a design

> **Status: built, 2026-11-23.** Written for the owner's review. It adds two
> operations to the Developer's edit tool, `move` and `delete`, and carries a
> deletion from the edit session to the delivery, the commit, the undo and the
> Verifier's evidence. Every rule of [`docs/POSITIONING.md`](../POSITIONING.md)
> applies unchanged.

## Why

In sprint 6 (2026-11-23) a Developer had to split a 642-line test file into
two files under the 600-line limit. It could not do it:

1. **It could only retype the code.** The edit tool changes an existing file
   and a delivery writes a file whole. Moving 434 lines meant typing them
   again. The model wrote tests from memory instead: they called functions
   that do not exist (`stack_image.dockerfile`, `services.workspace.docker_image`),
   and 19 tests failed. After three reworks the task was blocked.
2. **It could not remove the original.** No delivery deletes a file. A split
   always left the 642-line original in place.

The same two gaps block any refactor that moves code between files.

## What it does

Two new operations on `workspace.edit`, the daemon's edit session. They
travel on the same subject, so no NATS subject or permission changes.

### `move`

```json
{"op": "move", "path": "tests/test_a.py", "start_line": 40, "end_line": 120,
 "to_path": "tests/test_b.py", "to_line": 31}
```

- Cuts lines `start_line..end_line` of `path` and inserts them, byte for byte,
  before line `to_line` of `to_path`. Without `to_line` they go at the end.
- `to_path` may be a new file. The daemon creates it, with its folders, inside
  the project.
- The move is applied to both files or to neither. Both must still parse
  (`check_syntax`). Otherwise nothing changes and the reply says which file
  would break.
- The same rules as an edit apply to both files: inside the project, never
  VCS metadata, never a file that may hold secrets, a text file of at most
  `EDIT_MAX_BYTES`, at most `EDIT_MAX_FILES` files per session, and a file
  type a delivery writes.
- A move within one file (`to_path` = `path`) is allowed. `to_line` then
  counts in the file as it was before the cut, and a target inside the moved
  range is refused.

The observation the daemon returns, read back from disk:

| Field | What |
|---|---|
| `moved_lines` | How many lines moved |
| `moved_sha256` | sha256 of the moved bytes, as read from the source |
| `source` | `path`, `sha256_before`, `sha256_after`, `lines` |
| `target` | `path`, `sha256_before` (`null` for a new file), `sha256_after`, `lines`, `created` |

The daemon checks, before replying, that the target's bytes at the insertion
point equal the moved bytes. The `moved_sha256` is computed by the daemon,
never taken from the model (rule 6).

### `delete`

```json
{"op": "delete", "path": "tests/test_a.py"}
```

- Deletes a file **only when nothing is left in it**: empty, or whitespace
  only. Its lines must have been moved out first (or removed with edits, which
  are already observed one by one).
- A file with content is refused: *"tests/test_a.py still has 212 lines.
  Move them out first; a file is deleted only once it is empty. A person
  deletes a file that still has content."*
- The observation: `path`, `sha256_before` (the file's original bytes when
  the session started), `deleted: true`.

This rule is the core of the safety argument. Code cannot disappear through
`delete`. Every line that leaves a file either goes somewhere else through a
`move`, with its bytes checked, or goes away through an edit, which is
recorded with its before and after hashes. A deleted test file therefore
shows up in the evidence as moves, with their hashes, followed by the deletion
of an empty file.

### The session

The session already keeps each touched file's original bytes and puts them
back at `workspace.edit.finish`. It now also handles a file the session
created, which is removed at finish, and a file it deleted, which is written
back at finish. For each file, `finish` answers with one of three outcomes:

| Outcome | Fields |
|---|---|
| Changed | `content`, `sha256`, `changed: true` (as today) |
| Created | the same, with `created: true` |
| Deleted | `deleted: true`, `sha256_original`, no `content` |

## From the session to the delivery

1. **Backend, `workspace_edits.with_tool_edits`.** A deleted file becomes a
   delivery entry `{"path", "delete": true, "sha256_expected"}`.
   `sha256_expected` is the `sha256_original` that the daemon itself
   reported, never a value from the model.
2. **Daemon, `write_files`.** A `delete` entry unlinks the file only if:
   - it exists and is a regular file;
   - it is not a file that may hold secrets;
   - it is not a protected existing document;
   - its bytes still hash to `sha256_expected`.

   Its status is then `deleted`, with `sha256_before`. Otherwise the status is
   `rejected_delete`, with the reason.
3. **Git (`git_workspace.deliver`).** Deleted paths are staged with
   `git rm --cached --ignore-unmatch`, so the task's commit holds the
   deletion. (`git add` of a path Git never tracked, such as an ignored file,
   fails and would fail the whole delivery.)
4. **Undo without Git (`delivery_snapshot`).** The snapshot already keeps the
   bytes of every path it is asked to write. A deleted file is recorded as
   changed with `after_sha256: null`, and undo writes it back if the path is
   still empty.
5. **"ok"** means every file is `written` or `deleted`.

## The evidence and the Verifier

- `tool_calls` records each `move` and `delete` with the fields above, like
  every edit (`read_only: false`).
- `workspace_writes` carries the delivery's `deleted` entries.
  `protected_check.observed_paths` counts a deletion as a change. Deleting a
  protected path (a test the evidence context owns, a check script, a
  workflow) is therefore held for a person, exactly like changing it.
- `rewritten_by_delivery` (a check the delivery rewrote is not evidence) also
  counts a deleted check script as rewritten.
- QA's acceptance test files are checked before the tests run
  (`delivery_checks.guard_test_files`): one that is missing is restored and the
  delivery refuted, so deleting one gains nothing.
- **Tests removed (`tests_removed`, 2026-11-24).** On 2026-11-24 (sprint 6,
  task 27) a Developer went around "only an empty file is deleted" without
  `delete` at all: it delivered `backend/tests/test_browser_image.py` whole
  with empty content (`additions 0, deletions 642`) and rewrote two other test
  files from memory, the old tests gone. The Verifier now counts test
  definitions in every test file the delivery wrote or deleted, from the
  daemon's diffs (`services/evidence/tests_removed.py`). A deleted file's
  report carries the diff of the bytes the daemon removed, so the lines that
  left with an edit are counted too. The names are summed over the whole
  delivery: a test moved to another test file of the delivery is not lost. A
  name removed and not added back anywhere holds the task for a person
  (`risk_tier` high, area `tests`), as a protected path does; a test file
  modified or deleted with no diff to count is unverifiable.
- **Residual risk.** Still not caught: a test hollowed out under its own name
  (`assert True`), a removed test replaced by a new one under the same name,
  tests removed by a console producer (its report has no diffs), test files
  outside the name patterns (`test_*.py`, `*_test.py`, `*.test.*`,
  `*.spec.*`), and tests generated at run time (parametrisation, loops). A
  renamed test counts as removed: a person decides. A count of collected
  tests per delivery would close more of the gap. Not built.

## Older daemons

The daemon announces the new operations as features (`PROTOCOL.md` §5.1):
`"workspace.edit": ["move", "delete"]` and `"workspace.generate": ["delete"]`.
The backend offers the `workspace__move` and `workspace__delete` tools only
when every connected daemon announces both features. An older daemon never
sees an operation it would misread.

## Threat model (STRIDE, the agent as the adversary)

| Threat | Example | Mitigation |
|---|---|---|
| Tampering: make failing tests vanish | Delete `test_x.py` | `delete` only removes an empty file. Emptying a file needs moves (bytes kept elsewhere, hash checked) or edits (each recorded). A deleted protected path is held for a person; a missing acceptance test is restored and refutes the delivery. A test file a delivery wrote or deleted has its test definitions counted from the daemon's diffs; a name lost from the whole delivery holds the task for a person (`tests_removed`). Residual: a test hollowed out under its own name, or removed by a console producer. |
| Tampering: hollow out a check | Delete `scripts/check_file_sizes.py` | Same as above. A check script the delivery rewrote or deleted is not evidence about it (`rewritten_by_delivery`). |
| Tampering: write outside the project | `to_path: ../../etc/x` | The daemon's resolver, as for every edit: no `..`, no symlink escape, no VCS metadata. |
| Information disclosure | Move lines from `.env` into a tracked file | `is_sensitive` refuses both ends of a move, as for read and edit. |
| Spoofing a hash | The model claims `moved_sha256` | Every hash is computed by the daemon from disk. The delivery's `sha256_expected` comes from the daemon's own `finish` reply. |
| Denial of service | A move that copies a huge range | `EDIT_MAX_BYTES` on both files, `EDIT_MAX_FILES` per session. |
| Repudiation | A deletion nobody can trace | The tool call, the delivery's `deleted` status, the commit and the undo snapshot all record it. |
| A deletion that wipes a person's work | The file changed after the session | The delivery deletes only bytes that still hash to `sha256_expected`. Otherwise `rejected_delete`. |

## Not built

- Moving a whole file (a rename). A move of every line plus a delete of the
  emptied file does the same, with every byte observed.
- Deleting a folder. Git keeps no empty folders, and a folder the session
  created is removed at finish when empty.
