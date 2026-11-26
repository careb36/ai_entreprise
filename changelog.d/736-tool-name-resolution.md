Changed

* **A tool called by a name it plainly meant reaches that tool.** Agents often drop the server prefix (`read` for `workspace__read`) or use a common alias (`grep` for `workspace__search`); each such call cost a round on "unknown tool". When exactly one offered tool has that name, the call now goes to it; an ambiguous or unknown name still gets the "unknown tool" reply with the offered names.
