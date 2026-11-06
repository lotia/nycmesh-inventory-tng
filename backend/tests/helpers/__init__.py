"""What tests share that is not a fixture."""

import pkgutil

import pytest

# Before any is imported, so their asserts say what failed, as a test's do.
pytest.register_assert_rewrite(
    *(f"{__name__}.{module.name}" for module in pkgutil.iter_modules(__path__))
)
