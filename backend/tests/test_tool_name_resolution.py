"""
A model's call to an offered tool by a name it plainly meant goes to that
tool instead of costing a round on "unknown tool" (``mcp_tools.resolve_tool_name``):
sprint 2's agents called ``read``, ``search`` and ``workspace__grep`` while
``workspace__read`` and ``workspace__search`` were offered.
"""

from services.tools.mcp_tools import resolve_tool_name

OFFERED = ["workspace__list", "workspace__read", "workspace__search", "board__task"]


def test_an_offered_name_is_kept():
    assert resolve_tool_name("workspace__read", OFFERED) == "workspace__read"


def test_a_name_without_its_server_goes_to_the_one_tool_with_that_part():
    assert resolve_tool_name("read", OFFERED) == "workspace__read"
    assert resolve_tool_name("search", OFFERED) == "workspace__search"


def test_a_common_alias_goes_to_the_offered_tool():
    assert resolve_tool_name("workspace__grep", OFFERED) == "workspace__search"
    assert resolve_tool_name("grep", OFFERED) == "workspace__search"
    assert resolve_tool_name("cat", OFFERED) == "workspace__read"


def test_an_ambiguous_or_unknown_name_comes_back_unchanged():
    two = [*OFFERED, "docs__read"]
    assert resolve_tool_name("read", two) == "read"
    assert resolve_tool_name("deploy", OFFERED) == "deploy"
    assert resolve_tool_name("", OFFERED) == ""
