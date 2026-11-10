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
    python -m inventory_tng.totp_keys retire-authenticators --unreadable
    python -m inventory_tng.totp_keys retire-authenticators --all

DEPLOYMENT.md, TOTP keys, says how to rotate them and what to do if one is
lost.
"""

import argparse
import base64
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.orm import Session

from inventory_tng.local_env import CLONE
from inventory_tng.settings import SettingsError, is_development

if TYPE_CHECKING:  # Imported by the command, which needs no mail for `new`.
    from inventory_tng.mail import Notifier

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
                    "`python -m inventory_tng.totp_keys new` made"
                )
            keys[int(entry.name)] = key
        if not keys:
            raise TotpKeysError(
                f"TOTP_KEYS_DIR {directory} holds no key: add one as a file "
                "named 1, from `python -m inventory_tng.totp_keys new`"
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
    retire = commands.add_parser(
        "retire-authenticators",
        help="retire authenticators, for a lost or leaked key: each is set up again",
    ).add_mutually_exclusive_group(required=True)
    retire.add_argument(
        "--unreadable", action="store_true", help="those a lost key sealed"
    )
    retire.add_argument("--all", action="store_true", help="every one: a leaked key")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse(argv)
    if args.command == "new":
        print(new_key())
        return 0
    # Imported here: `new` needs no database or settings.
    from inventory_tng.command import run_command, run_command_telling

    if args.command == "reseal":
        return run_command(_reseal, args)
    return run_command_telling(_retire, args)


def _reseal(session: Session, _args: argparse.Namespace) -> int:
    from inventory_tng.accounts import reseal_authenticators

    resealed, unreadable = reseal_authenticators(session, TotpKeys.from_environ())
    print(f"Resealed {resealed}; {unreadable} could not be read.")
    return 1 if unreadable else 0


def _retire(session: Session, args: argparse.Namespace, notify: Notifier) -> int:
    from inventory_tng.accounts import retire_authenticators

    retired, stranded = retire_authenticators(
        session,
        TotpKeys.from_environ(),
        datetime.now(UTC),
        every=args.all,
        notify=notify,
    )
    # Printed once committed.
    notify.tasks.add_task(print, f"Retired {retired} authenticators.")
    if stranded:
        names = ", ".join(stranded)
        notify.tasks.add_task(print, f"No second factor left, so let them in: {names}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
