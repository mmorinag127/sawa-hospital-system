"""Totals use the shared real authored registration/select/save/confirm path."""
import pytest

from tests.helpers.owned_output import owned_outputs  # noqa: F401


@pytest.fixture
def totals_inputs(owned_outputs):
    return owned_outputs
