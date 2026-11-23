Fixed

* **The browser probe keeps why the browser closed (issue #529).** `probe-browser.js` cut its reason at the first 500 characters, which were Chrome's launch flags, so the arm64 probe of 2026-11-15 failed without its cause. The launch line is now shortened and, past the limit, the start and the end of the reason are both kept.
