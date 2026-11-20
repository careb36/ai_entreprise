Added

* **Agents write to the person in the company's language.** With the interface in Spanish, the live view showed the agents and the system in English. Two changes fix that:
  * The task worker, the Architect's brief and QA's review now ask for the person's prose in `Company.doc_language` (`services/contracts/language.py`). Code, paths, commit messages and the markers the platform parses (`APPROVED:`, `NEEDS WORK:`) stay in English.
  * The worker's fixed live-view messages carry a `message_key` and `message_params`, which the live view translates with `en.json` and `es.json`. The English text remains the fallback, and the stored records do not change.
