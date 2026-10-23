import os


os.environ.setdefault("AUTH_DISABLED", "true")

from tests.contract.auth_support import hospital_operator  # noqa: E402,F401
from tests.helpers.owned_output import owned_outputs  # noqa: E402,F401
