Fixed

* **A tool call typed as text is not passed on as the Architect's brief.** Asked for a brief without tools, the Architect of sprint 7 (2026-11-24) answered only with `<tool_calls><invoke name="exec_command">…` markup, and the Developer got it as its brief. `contracts.tool_calls.written_as_text` now tells such an answer apart (XML-style tool calls and models' tool-call tokens), and the consultation counts as giving no brief.
