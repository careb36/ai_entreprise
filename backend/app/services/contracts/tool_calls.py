"""The tool-call observation's shape shared by the tools and the evidence.

The task worker records each tool call as an observation with a ``server``
field; the built-in workspace tools record ``WORKSPACE_SERVER``, and the
evidence (``context_read``) tells their calls apart by it (ADR 0013).
"""

WORKSPACE_SERVER = "workspace"
