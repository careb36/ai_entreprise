Added

* **The self-hosting company gets La Fabrique's own rules as company procedures (issue #701).** The harness's `company` step writes them through `POST /v1/procedures` (adopted at once, measured like any procedure, a 409 left alone), so they reach the QA and Developer prompts from the first attempt. The first rule: a new backend test file is delivered with its `tests.files` entry in `docs/architecture/contexts.json`; in self-hosting runs 6 to 8 every acceptance task spent its first rework learning it from the architecture check.
