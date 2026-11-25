Fixed

* **`edit` written alone on the first line of a file block counts as the edit flag (issue #701).** An agent that put `edit` on the line after the opening fence, instead of after the path, had its block refused as malformed and then written as the whole file, which failed to parse (self-hosting run 10, task 62).
