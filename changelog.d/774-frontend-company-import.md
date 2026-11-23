Added

* **Start a company from an exported file, in the app (issue #774).** The New company page has a "Start from an exported file" panel: pick the file, keep or change the name, import. It checks the file is JSON with a content section before sending it, then shows what was created, what the file held that was not applied and why, what is left to set up (provider keys, credentials, environment variables), that routines arrived turned off, and a warning when the file's content does not match the digest it claims. "Open the company" switches to it.
