# Bench

Reference specifications graded by tests a person wrote, not by the model under
test. See issue #316.

- `specs/` — one directory per reference specification, with `spec.md` and its
  acceptance tests.
- `scripts/bench/run.sh` — drives one specification through the factory and
  records a scorecard (JSON) under `bench/scorecards/`.
- `.github/workflows/bench.yml` — runs on demand and nightly on the
  self-hosted runner.

To add a reference specification, copy `specs/01-cli-todo/` and write the
acceptance tests by hand.