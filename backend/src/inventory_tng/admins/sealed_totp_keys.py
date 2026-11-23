"""TOTP keys encrypted at rest (docs/decisions/0017), so a copy of the
database alone cannot make an administrator's codes.

Each authenticator's key is sealed with AES-GCM from the cryptography
package, never anything written here (AGENTS.md rule 3), under one of the
keys in TOTP_KEYS_DIR: a directory holding one file per key version, named
by its number (1, 2, ...), each holding a key the command's `new` printed
(admins/sealed_totp_keys_command.py, which also reseals and retires). The
highest version seals; every version present opens. A Kubernetes Secret
mounted as a directory has this shape. In development the directory
defaults to the clone's .local/totp-keys, which `mise run setup` fills.

Opening an authenticator's key (open_key), for sign-in, startup and the
command, counting those that do not open, and resealing them with the
newest version are here too. Retiring them is account work
(admins/accounts.py's retire_authenticators): it ends sessions, removes
sign-in and mails administrators.

DEPLOYMENT.md, TOTP keys, says how to rotate them and what to do if one is
lost.
"""

import base64
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import structlog
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.core.db_locking import locked
from inventory_tng.core.db_once_committed import once_committed
from inventory_tng.core.db_sessions import set_actor
from inventory_tng.core.local_env import CLONE
from inventory_tng.core.settings import SettingsError, is_development
from inventory_tng.models.admins import CURRENT_TOTP, Authenticator

log = structlog.get_logger(__name__)

DEVELOPMENT_DIR = CLONE / ".local" / "totp-keys"
KEY_BITS = 256
# AES-GCM's standard nonce, random for each seal: at a few hundred seals
# per key, far below where random nonces could repeat.
NONCE_BYTES = 12
# A version's file name: no sign or leading zero, so each has one name.
VERSION = re.compile(r"[1-9][0-9]*")
# The newest version authenticator.key_version (a smallint) holds.
MOST_VERSION = 32767


class TotpKeysError(SettingsError):
    """TOTP_KEYS_DIR is missing, empty or holds something unusable."""


class Unreadable(Exception):
    """A sealed key that cannot be opened: its key version is gone, its
    version's file holds another key, it was not sealed for this
    administrator, or it is damaged."""


def new_key() -> str:
    """A key for a new version's file, as text."""
    return base64.b64encode(AESGCM.generate_key(KEY_BITS)).decode()


def _for(admin_id: int) -> bytes:
    """What a sealed key is bound to (AES-GCM's associated data): its
    administrator, so a sealed key moved to another's row does not open."""
    return f"inventory-tng totp key, admin {admin_id}".encode()


@dataclass(frozen=True)
class TotpKeys:
    # Each version's key, by its number.
    keys: Mapping[int, bytes] = field(repr=False)

    @property
    def newest(self) -> int:
        return max(self.keys)

    def seal(self, totp_key: str, admin_id: int) -> tuple[int, bytes]:
        """The key version, and the nonce followed by the ciphertext."""
        nonce = os.urandom(NONCE_BYTES)
        sealed = AESGCM(self.keys[self.newest]).encrypt(
            nonce, totp_key.encode(), _for(admin_id)
        )
        return self.newest, nonce + sealed

    def open(self, version: int, sealed: bytes, admin_id: int) -> str:
        key = self.keys.get(version)
        if key is None:
            raise Unreadable(f"key version {version} is not in TOTP_KEYS_DIR")
        # Too short for AESGCM to take as a nonce, which it refuses with a
        # ValueError: damaged, so unreadable rather than a crash at startup.
        if len(sealed) < NONCE_BYTES:
            raise Unreadable("too short to be a sealed key")
        nonce, rest = sealed[:NONCE_BYTES], sealed[NONCE_BYTES:]
        try:
            return AESGCM(key).decrypt(nonce, rest, _for(admin_id)).decode()
        except InvalidTag:
            raise Unreadable(
                "not sealed with this key for this administrator"
            ) from None

    @classmethod
    def load(cls, directory: Path) -> TotpKeys:
        """Every version in `directory`. Names that are not a version, such as
        the hidden entries a mounted Secret has, are passed over. Not logged:
        settings load it before logging is set up (the server's starting
        line says which versions it has)."""
        try:
            entries = list(directory.iterdir())
        except OSError as error:
            raise TotpKeysError(
                f"TOTP_KEYS_DIR {directory} cannot be read: {error.strerror}"
            ) from None
        keys = {}
        for entry in entries:
            if not VERSION.fullmatch(entry.name):
                continue
            if int(entry.name) > MOST_VERSION:
                raise TotpKeysError(
                    f"TOTP_KEYS_DIR's version {entry.name} is past {MOST_VERSION}"
                )
            try:
                key = base64.b64decode(entry.read_text().strip(), validate=True)
            except OSError, ValueError:  # binascii.Error is a ValueError.
                key = b""
            if len(key) * 8 != KEY_BITS:
                raise TotpKeysError(
                    f"TOTP_KEYS_DIR's version {entry.name} is not a key "
                    "`python -m inventory_tng.manage sealed-totp-keys new` made"
                )
            keys[int(entry.name)] = key
        if not keys:
            raise TotpKeysError(
                f"TOTP_KEYS_DIR {directory} holds no key: add one as a file "
                "named 1, from `python -m inventory_tng.manage sealed-totp-keys new`"
            )
        return cls(keys)

    @classmethod
    def from_environ(cls, environ: Mapping[str, str] = os.environ) -> TotpKeys:
        """TOTP_KEYS_DIR's, or development's (keys_dir)."""
        return cls.load(keys_dir(environ))


def keys_dir(environ: Mapping[str, str]) -> Path:
    """TOTP_KEYS_DIR, or development's default; needed anywhere else."""
    if value := environ.get("TOTP_KEYS_DIR", "").strip():
        return Path(value)
    if is_development(environ):
        return DEVELOPMENT_DIR
    raise TotpKeysError("TOTP_KEYS_DIR is needed outside APP_ENV=development")


def open_key(keys: TotpKeys, authenticator: Authenticator) -> str | None:
    """Its key, or None if it cannot be read (its key version is gone from
    TOTP_KEYS_DIR, or that version's file holds another key): the one test of
    unreadable, for sign-in, startup and the operator's command. It then takes
    no code, and recovery codes and reset links still let its administrator
    in (DEPLOYMENT.md, TOTP keys)."""
    # Current ones keep theirs.
    assert authenticator.sealed_key is not None
    assert authenticator.key_version is not None
    try:
        return keys.open(
            authenticator.key_version, authenticator.sealed_key, authenticator.admin_id
        )
    except Unreadable:
        log.error(
            "authenticator unreadable",
            admin_id=authenticator.admin_id,
            authenticator_id=authenticator.id,
            key_version=authenticator.key_version,
        )
        return None


def unreadable_authenticators(session: Session, keys: TotpKeys) -> int:
    """How many current authenticators `keys` cannot open (open_key). Each is
    opened, one AES-GCM decryption apiece."""
    current = session.scalars(select(Authenticator).where(CURRENT_TOTP))
    return sum(open_key(keys, authenticator) is None for authenticator in current)


def reseal_authenticators(session: Session, keys: TotpKeys) -> tuple[int, int]:
    """Every current authenticator's key sealed again with the newest version,
    so older ones can leave TOTP_KEYS_DIR: how many were, and how many could
    not be read (left as they are). The caller commits."""
    set_actor(session, "system:reseal")
    resealed = unreadable = 0
    current = session.scalars(
        locked(select(Authenticator).where(CURRENT_TOTP).order_by(Authenticator.id))
    )
    for authenticator in current:
        key = open_key(keys, authenticator)
        if key is None:
            unreadable += 1
        elif authenticator.key_version != keys.newest:
            authenticator.key_version, authenticator.sealed_key = keys.seal(
                key, authenticator.admin_id
            )
            resealed += 1
    session.flush()
    once_committed(
        session,
        log.info,
        "authenticators resealed",
        key_version=keys.newest,
        resealed=resealed,
        unreadable=unreadable,
    )
    return resealed, unreadable
