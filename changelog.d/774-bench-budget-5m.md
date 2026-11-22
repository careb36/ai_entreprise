Changed

* **The nightly bench's default token budget is 5M, up from 300k (issue #774).** The six specs' DeepSeek arm used 2.79M reported tokens on 2026-11-22; a cap below that cuts the run and leaves what it spent ungraded. The repository variable `BENCH_TOKEN_BUDGET` still overrides it.
