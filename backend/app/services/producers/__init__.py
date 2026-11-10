"""The producers context: interchangeable producers of work (ADR 0012, 0016).

The producers other than the task worker's own model call: the terminal
assistants that advise a task (``console_assistants``, with the daemon's
probe of what is installed, ``console_capabilities``), the terminal coding
agents that produce a role's task (``console_clis``, ``console_producer``),
the optional LangGraph kickoff (``agent_worker``) and the GitHub adapter that
ingests pull requests as runs (``github_adapter``). A producer's output is a
claim handed to the evidence; what the loop owns (recording a run, moving a
task, its prompt sections) reaches a producer through the host the loop
passes in (``console_producer.ConsoleHost``). It imports only
``contracts``, ``platform``, ``tasks``, ``models``, ``evidence``,
``workspace`` and ``tools`` among the contexts;
``scripts/check_architecture.py`` keeps it so.
"""
