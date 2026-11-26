Fixed

* **A file block holding only an `edit` marker no longer wipes the file.** An agent wrote `{"edit": true}` as the whole body of `en.json` and `es.json`, then put its SEARCH/REPLACE edits in the next block without a path; the 5,400-line files were replaced by that marker. A body that is only an `edit` marker is now read as an edit block whose edits are those of the pathless block right after it, and with no edits after it the daemon refuses it with the reason. It is never written as the file's content.
