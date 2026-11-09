"""The tools context: what a model may call during a task (ADR 0012, 0016).

The bounded tool loop and the MCP servers with their configuration
(``mcp_tools``, ``mcp_client``, ``mcp_config``), the Developer's workspace
tools, their schemas and replies (``developer_tools``,
``developer_tool_schemas``, ``developer_tool_replies``), the checked edits and
the lint after an edit (``workspace_edits``, ``edit_lint``), the repository map
(``repo_map``), the research tools (``research_tools``, ``research_schemas``,
``research_registries``) and the compressed history (``tool_history``). Each
call is recorded as an observation; the tools never decide a verdict. It
imports only ``contracts``, ``platform``, ``evidence`` and ``workspace`` among
the contexts; ``scripts/check_architecture.py`` keeps it so.
"""
