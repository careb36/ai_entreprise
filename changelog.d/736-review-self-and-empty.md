Fixed

* **QA no longer reviews its own task, and an approval of a delivery that wrote no file waits for a person.** A task assigned to QA is reviewed by the company's Architect (QA still reviews when no Architect is hired). An approved delivery that declared and wrote no file closes only when a person accepts it: the approval is the reviewer's word alone, with nothing the Verifier could check. A sprint closed a QA task as done when QA approved its own answer saying it could not do the work.
