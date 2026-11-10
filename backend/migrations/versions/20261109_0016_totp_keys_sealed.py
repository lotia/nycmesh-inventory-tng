"""authenticators' TOTP keys sealed at rest (inventory-tng-8i4.48)

Each current authenticator's key is sealed with TOTP_KEYS_DIR's newest key
version, for its administrator (totp_keys.py, docs/decisions/0017). Only a
database that already has authenticators needs the keys to migrate, which
in development is the clone's .local/totp-keys.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-09 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from inventory_tng.totp_keys import TotpKeys

revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PLAIN = "(totp_key IS NULL) = (retired_at IS NOT NULL)"
SEALED = (
    "(sealed_key IS NULL) = (retired_at IS NOT NULL)"
    " AND (key_version IS NULL) = (sealed_key IS NULL)"
)
CONSTRAINT = "key_until_retired"


def upgrade() -> None:
    op.add_column("authenticator", sa.Column("sealed_key", sa.LargeBinary()))
    op.add_column("authenticator", sa.Column("key_version", sa.SmallInteger()))
    connection = op.get_bind()
    current = connection.execute(
        sa.text(
            "SELECT id, admin_id, totp_key FROM authenticator"
            " WHERE totp_key IS NOT NULL ORDER BY id"
        )
    ).all()
    # The keys only where there is something to seal.
    keys = TotpKeys.from_environ() if current else TotpKeys({})
    for id_, admin_id, totp_key in current:
        version, sealed = keys.seal(totp_key, admin_id)
        connection.execute(
            sa.text(
                "UPDATE authenticator SET sealed_key = :sealed,"
                " key_version = :version WHERE id = :id"
            ),
            {"sealed": sealed, "version": version, "id": id_},
        )
    op.drop_constraint(CONSTRAINT, "authenticator", type_="check")
    op.drop_column("authenticator", "totp_key")
    op.create_check_constraint(CONSTRAINT, "authenticator", SEALED)


def downgrade() -> None:
    op.add_column("authenticator", sa.Column("totp_key", sa.Text()))
    connection = op.get_bind()
    current = connection.execute(
        sa.text(
            "SELECT id, admin_id, key_version, sealed_key FROM authenticator"
            " WHERE sealed_key IS NOT NULL ORDER BY id"
        )
    ).all()
    keys = TotpKeys.from_environ() if current else TotpKeys({})
    for id_, admin_id, version, sealed in current:
        connection.execute(
            sa.text("UPDATE authenticator SET totp_key = :key WHERE id = :id"),
            {"key": keys.open(version, sealed, admin_id), "id": id_},
        )
    op.drop_constraint(CONSTRAINT, "authenticator", type_="check")
    op.drop_column("authenticator", "key_version")
    op.drop_column("authenticator", "sealed_key")
    op.create_check_constraint(CONSTRAINT, "authenticator", PLAIN)
