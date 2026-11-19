"""An authenticator's TOTP key and its codes: making a key, the link and
the typed form an app reads it from, and checking a six-digit code.
Sealing keys at rest is admins/sealed_totp_keys.py's job.

Only established calls here (AGENTS.md rule 3): pyotp makes TOTP keys and
checks codes.
"""

import re
from datetime import datetime, timedelta

import pyotp

from inventory_tng.core.phrases import BRAND

# A TOTP-only field takes a six-digit code, with room for spaces.
TOTP_FIELD_MAX = 8
TOTP_CODE = re.compile(r"\d{6}")
TOTP_INTERVAL = pyotp.TOTP(pyotp.random_base32()).interval
# What authenticator apps show beside the account.
ISSUER = BRAND


def new_totp_key() -> str:
    return pyotp.random_base32()


def is_totp_key(key: str) -> bool:
    """A key as new_totp_key() makes them, sent back by a form."""
    return re.fullmatch(r"[A-Z2-7]{32}", key) is not None


def totp_link(key: str, username: str) -> str:
    """The otpauth:// link an authenticator app reads from the QR code."""
    return pyotp.TOTP(key).provisioning_uri(name=username, issuer_name=ISSUER)


def grouped(text: str, size: int, separator: str) -> str:
    """ "ABCD EFGH ...": easier to read out and type than one long run."""
    return separator.join(text[i : i + size] for i in range(0, len(text), size))


def key_for_typing(key: str) -> str:
    """For apps that cannot scan."""
    return grouped(key, 4, " ")


def totp_code(typed: str) -> str | None:
    """The six digits, if that is what was typed, spaces aside; else it may
    be a recovery code."""
    code = re.sub(r"\s", "", typed)
    return code if TOTP_CODE.fullmatch(code) else None


def accepted_step(
    key: str, typed: str, last_step: int | None, now: datetime
) -> int | None:
    """The time step `typed` is the code for, within one step either side of
    now and later than last_step; None if none."""
    code = totp_code(typed)
    if code is None:
        return None
    totp = pyotp.TOTP(key)
    for offset in (-1, 0, 1):
        at = now + timedelta(seconds=offset * totp.interval)
        step = totp.timecode(at)
        if last_step is not None and step <= last_step:
            continue
        if totp.verify(code, for_time=at):
            return step
    return None
