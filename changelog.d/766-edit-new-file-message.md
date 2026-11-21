Fixed

* **The edit tool says where a new file goes (issue #766).** In phase D's bench half the Developer's and QA's edits tried to create a file with `workspace__edit`, which changes existing files only, and the model retried, spending rounds. The daemon's refusal and the tool's description now say to write a new file whole in the answer, as a fenced block whose opening line carries `path=`.
