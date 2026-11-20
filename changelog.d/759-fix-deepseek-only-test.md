Fixed

* **The backend suite collects again on main (follow-up to #758 and #759).** `backend/tests/test_deepseek_only.py` still imported `services.producers.console_assistants`, which #759 removed, so the whole backend suite failed at collection. The import and the no-longer-needed stub are gone.
