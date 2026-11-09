"""TOTP keys encrypted at rest (docs/decisions/0017), so a copy of the
database alone cannot make an administrator's codes.

Each authenticator's key is sealed with AES-GCM from the cryptography
package, never anything written here (AGENTS.md rule 3), under one of the
keys in TOTP_KEYS_DIR: a directory holding one file per key version, named
by its number (1, 2, ...), each holding a key `new` printed. The highest
version seals; every version present opens. A Kubernetes Secret mounted as
a directory has this shape. In development the directory defaults to the
clone's .local/totp-keys, which `mise run setup` fills.

    python -m inventory_tng.totp_keys new     # print a new key
    python -m inventory_tng.totp_keys reseal  # seal every key with the newest

DEPLOYMENT.md, TOTP keys, says how to rotate them and what to do if one is
lost.
"""

import argparse
import base64
import binascii
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import structlog
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.orm import Session

from inventory_tng.local_env import CLONE

log = structlog.get_logger(__name__)

DEVELOPMENT_DIR = CLONE / ".local" / "totp-keys"
KEY_BITS = 256
# AES-GCM's standard nonce, random for each seal: at a few hundred seals
# per key, far below where random nonces could repeat.
NONCE_BYTES = 12


class TotpKeysError(Exception):
    """TOTP_KEYS_DIR is missing, empty or holds something unusable."""


class Unreadable(Exception):
    """A sealed key that cannot be opened: its key version is gone, or it was
    not sealed for this administrator."""


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
        the hidden entries a mounted Secret has, are passed over."""
        try:
            entries = list(directory.iterdir())
        except OSError as error:
            raise TotpKeysError(
                f"TOTP_KEYS_DIR {directory} cannot be read: {error.strerror}"
            ) from None
        keys = {}
        for entry in entries:
            if not entry.name.isdigit() or int(entry.name) < 1:
                continue
            try:
                key = base64.b64decode(entry.read_text().strip(), validate=True)
            except OSError, ValueError, binascii.Error:
                key = b""
            if len(key) * 8 != KEY_BITS:
                raise TotpKeysError(
                    f"TOTP_KEYS_DIR's version {entry.name} is not a key "
                    "`python -m inventory_tng.totp_keys new` made"
                )
            keys[int(entry.name)] = key
        if not keys:
            raise TotpKeysError(
                f"TOTP_KEYS_DIR {directory} holds no key: add one as a file "
                "named 1, from `python -m inventory_tng.totp_keys new`"
            )
        log.info("totp keys loaded", versions=sorted(keys))
        return cls(keys)


def keys_dir(environ: Mapping[str, str], *, development: bool) -> Path:
    """TOTP_KEYS_DIR, or development's default; needed anywhere else."""
    if value := environ.get("TOTP_KEYS_DIR", "").strip():
        return Path(value)
    if development:
        return DEVELOPMENT_DIR
    raise TotpKeysError("TOTP_KEYS_DIR is needed outside APP_ENV=development")


def parse(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m inventory_tng.totp_keys",
        description="TOTP keys at rest (DEPLOYMENT.md, TOTP keys).",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("new", help="print a new key, for a new version's file")
    commands.add_parser(
        "reseal", help="seal every current authenticator's key with the newest"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse(argv)
    if args.command == "new":
        print(new_key())
        return 0
    # Imported here: `new` needs no database or settings.
    from inventory_tng.command import run_command

    return run_command(_reseal, args)


def _reseal(session: Session, _args: argparse.Namespace) -> int:
    from inventory_tng.accounts import reseal_authenticators
    from inventory_tng.settings import Settings

    keys = Settings.from_environ().totp_keys
    assert keys is not None  # from_environ always loads them.
    resealed, unreadable = reseal_authenticators(session, keys)
    print(f"Resealed {resealed}; {unreadable} could not be read.")
    return 1 if unreadable else 0


if __name__ == "__main__":
    sys.exit(main())
