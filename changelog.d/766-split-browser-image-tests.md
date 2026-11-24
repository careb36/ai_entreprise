Changed

* **`test_browser_image.py` is under the 600-line limit.** Its backend half (`DELIVERY_BROWSER_IMAGE`, the presence `e2e_run` reports, install state per sandbox mode) moved, unchanged, to `test_browser_image_backend.py`, registered in `contexts.json` and owned like the original. Sprint 6 (2026-11-23) set this split as a task; the Developer rewrote the tests from memory three times instead of moving them.
