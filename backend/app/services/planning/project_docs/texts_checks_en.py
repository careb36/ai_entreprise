"""The English fixed strings of the domain checks (``domain_checks``, issue
#692): our own wording; the same keys and placeholders as ``texts_checks_es``."""

TEXTS: dict[str, str] = {
    "kind.quota": "Business unit quota",
    "check.basis_not_read": "the records it needs were not read",
    "check.approved_first": "Work starts on an approved plan",
    "check.approved_first_how": "the sprints planned from a specification, their first task event against that specification version's approval",
    "check.approved_first_obs": "{part} of {whole} sprints planned from a specification started after its approval; {unplanned} were not planned from one",
    "check.approved_first_none": "no sprint planned from a specification has started",
    "check.approved_first_unknown": "the specification version of {part} of {whole} started sprints is not in the records",
    "check.churn": "A sprint's scope holds once it is planned",
    "check.churn_how": "the tasks that joined a sprint after it was planned, accepted from an intake request (the platform's task event), over the sprints' tasks",
    "check.churn_obs": "{part} of {whole} tasks joined their sprint after it was planned, from an intake request",
    "check.on_time": "Iterations finish by their planned end",
    "check.on_time_how": "completed sprints whose last task event falls on or before the last day of their start date plus their planned length in days",
    "check.on_time_obs": "{part} of {whole} completed sprints with a planned length closed on or before its planned end; {unplanned} have no planned length",
    "check.on_time_none": "no completed sprint has a planned length recorded (start date and days)",
    "check.label_length": "planned length {days} days",
    "check.quota": "Model usage stays within the configured quota",
    "check.quota_how": "the tokens counted against the business units' configured token quota this period, as the LLM proxy reported them",
    "check.quota_obs": "{used} of {limit} tokens of the configured quota used this period ({units} business units with a quota; {reached} reached it)",
    "check.quota_none": "no business unit has a token quota configured (0 means unlimited)",
    "check.sprint_team": "Each sprint's work goes to the team chosen for it",
    "check.sprint_team_how": "the tasks of the sprints that record a team, assigned to a role on that team",
    "check.sprint_team_obs": "{part} of {whole} tasks of sprints with a team go to a role on it",
    "check.sprint_team_none": "no sprint records a team",
}
