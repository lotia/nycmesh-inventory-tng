"""Marks for tests of what is in a clone but not in the image."""

import pytest

from inventory_tng import local_env

clone_only = pytest.mark.skipif(
    not (local_env.CLONE / "mise.toml").exists(),
    reason="only in a clone, not in the image",
)
