"""A fixture ``conftest.py`` imports, kept here for its size."""

import pytest


@pytest.fixture(autouse=True)
def acknowledgements_out_of_the_way(request, monkeypatch):
    """The suite runs with tests off and the host sandbox (the defaults), which
    need an Owner's acknowledgement before a sprint starts (issue #766). Tests
    about something else start sprints as before; a test marked
    ``preconditions`` gets the real check (``test_sprint_start_preconditions.py``)."""
    if request.node.get_closest_marker("preconditions"):
        return
    from services.loop import preconditions

    monkeypatch.setattr(preconditions, "unmet", lambda session, company_id: [])
