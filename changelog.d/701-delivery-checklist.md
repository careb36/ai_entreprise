Added

* **The task prompt ends with a "Before you deliver" list of the company's procedures for the role.** The procedures open the prompt (cached at the providers' prefix rate), and a model had forgotten them by the time it wrote its delivery: in self-hosting run 10 every rule was in the prompt, uncut, and the first delivery still broke one. Their titles now come again as the last words before the answer (section `delivery_checklist`, cut last, can be turned off with `PROMPT_SECTIONS_OFF`).
