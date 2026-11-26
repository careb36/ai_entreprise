Changed

* **Agents know their tool budget.** The last tool result of each round now ends with where the agent stands ("Tool round 3 of 15. When you have what you need, stop calling tools and give the complete deliverable."), and warns when two rounds or fewer are left. Before, no agent was told it had a budget: every run of sprints 2 to 5 (Developer, QA and Architect) read until all 15 rounds were spent and then answered from summarized memory. An old result loses its note when its round is compressed.
