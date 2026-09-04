"""Explicitly imported fixtures for shared test isolation."""

from collections.abc import Iterator

import pytest

from taskman_ops.output import clear_secrets


@pytest.fixture(autouse=True)
def no_registered_secrets_between_tests() -> Iterator[None]:
    clear_secrets()
    yield
    clear_secrets()
