Changed

* **A refused edit says where its text was likely meant.** When a SEARCH/REPLACE edit's search text is not in the file, the daemon's refusal now names the file's line closest to the search's first line, with its number (`the closest line is 584: '...'`). The edit is still refused, never applied to a guess; the agent's next attempt can anchor on the real line instead of repeating the miss, which cost three sprint-2 and sprint-4 attempts.
