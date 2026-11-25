Fixed

* **A block made only of SEARCH/REPLACE edits is read as edits without the `edit` flag (issue #701).** An agent that left out `edit` after the path had its edits sent to the daemon as the whole file: `contexts.json` became `<<<<<<< SEARCH…`, failed the JSON check and the delivery was refuted (self-hosting run 9, task 56, whose edit was correct). A block whose body is nothing but well-formed edits is now read as edits, as Aider reads its blocks; text beside the markers keeps it a whole file.
