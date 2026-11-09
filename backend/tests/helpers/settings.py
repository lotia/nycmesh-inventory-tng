"""Settings read from an environment a test gives."""

import os
from collections.abc import Mapping

from inventory_tng.settings import Settings


def from_environ(environ: Mapping[str, str]) -> Settings:
    """Settings.from_environ, with the tests' TOTP keys (conftest's
    no_settings_from_the_shell) unless `environ` names its own: a test about
    another setting need not give TOTP_KEYS_DIR, which is needed outside
    development."""
    return Settings.from_environ(
        {"TOTP_KEYS_DIR": os.environ["TOTP_KEYS_DIR"]} | dict(environ)
    )
