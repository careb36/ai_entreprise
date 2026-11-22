Added

* **The bench compares repeated runs item by item, with a significance test (issue #774).** `scripts/bench/paired.py` pairs the baseline's and the candidate's cards and judges each acceptance criterion with McNemar's test and first-attempt share, blocked share, rework and tokens with the Wilcoxon signed-rank test, with a seeded bootstrap interval. A metric is better or worse only when p < 0.05, so a change can be shown to help, and a single noisy run never decides. The design is MLflow's paired comparison of evaluation runs (Apache-2.0).
