"""The models context: which model a role runs and what it costs (ADR 0012, 0016).

The role defaults (``model_factory``, moved from ``backend/llm``, ADR 0015),
the models.dev catalogue (``model_catalog``) and prices (``model_prices``,
``run_cost``), the company's provider connections (``provider_connections``)
and model settings (``company_models``), difficulty routing
(``model_routing``), the suggestions per role (``model_suggestions``) and the
records over completed sprints (``model_record``, ``routing_record``). It
never calls a provider; the LLM proxy does. It imports only ``contracts``,
``platform`` and ``tasks`` among the contexts; ``scripts/check_architecture.py``
keeps it so.
"""
