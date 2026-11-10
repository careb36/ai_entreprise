"""The management context: the owner as a client (ADR 0012, 0016).

The Manager agent (``manager_agent``) with what it reads and does: the inbox
and its further items (``manager_inbox``, ``manager_inbox_extra``), the policy
a Manager writes and the rules it names (``manager_policy``,
``manager_rules``), the probes of what nothing stored (``manager_observe``),
the actions taken as a person would and their override (``manager_actions``,
``manager_actions_extra``) and its record against the client's decisions
(``manager_record``, ``manager_record_extra``); the client's brief and its
mail (``manager_brief``, ``brief_mail``); the Reviewer agent
(``reviewer_agent``) and the Support agent (``support_agent``). Its
suggestions are claims kept apart from what people decide. It is the top
layer: it may import every other context, and no other context imports it;
``scripts/check_architecture.py`` keeps it so.
"""
