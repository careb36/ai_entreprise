# Reference specification 01 — CLI todo

## Summary
A small command-line todo list persisted in a JSON file.

## Requirements

### REQ-1 Add a task
- **Criterion REQ-1.1**: given an empty store, when `todo add "buy milk"`, then the task is stored with status todo.

### REQ-2 List tasks
- **Criterion REQ-2.1**: given two tasks, when `todo list`, then both are printed, one per line.

### REQ-3 Complete a task
- **Criterion REQ-3.1**: given a task, when `todo done 1`, then its status becomes done.

## Quality
- NFR-1: the CLI starts in under 200 ms.

## Constraints
- Python 3.11+, no external dependencies.
