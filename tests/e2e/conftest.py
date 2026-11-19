"""E2E tests run against the real machine (installed games, real config and cache), read-only.

Run with: uv run --project cli pytest -m games
"""

import pytest


@pytest.fixture(autouse=True)
def isolated_env():
    """Override the unit-test isolation: e2e tests use the real GS_HOME, config and caches."""
    yield {}
