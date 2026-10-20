import os
from tests.formal_schema_fixture import create_formal_schema

from src.db import Base, engine
import src.models  # noqa: F401


# Keep the existing test suite behavior stable while production defaults fail closed.
os.environ.setdefault("AUTH_DISABLED", "true")
os.environ.setdefault("SAWA_ENVIRONMENT", "test")
os.environ.setdefault("SAWA_TEST_DISABLE_API_AUTH", "true")

create_formal_schema(engine)
