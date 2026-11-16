"""What the tests of the ``grounded`` check share (issue #444): delivered files
built from (path, content) pairs. The tests are ``test_grounding*.py``.
"""

from __future__ import annotations


def _files(*pairs):
    return [{"path": path, "content": content} for path, content in pairs]
