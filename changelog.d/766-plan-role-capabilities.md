Fixed

* **The PO plans knowing what each role can do (issue #766).** The planning prompt listed the roles by name, seniority and model only, so a plan could ask the Architect, who has no tools on the project, to run validation scripts. Both the objective plan and the specification plan now say which of the sprint's roles read the project's files, edit them and run its tests, and which can write whole files in their answer but cannot read the project or run anything, with a task that needs the existing code or runs something going to the first kind.
* **A stored alias gets its role's research tools.** `RESEARCH_ROLES` was compared with the raw stored role, so an agent stored as `Security` (the SecOps) never got the research tools; both sides are now read as catalogue roles.
