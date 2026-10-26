"""Correct the password verifier format; do not treat it as a SHA256 artifact digest."""

from alembic import op

revision = "0004_password_hash_format"
down_revision = "0003_agent_request_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_local_credentials_password_hash_sha256"), "local_credentials", type_="check"
    )
    op.create_check_constraint(
        "password_hash_scrypt",
        "local_credentials",
        "password_hash ~ '^scrypt[$][0-9]+[$][0-9]+[$][0-9]+[$][A-Za-z0-9_-]{22}[$][A-Za-z0-9_-]{43}$'",
    )


def downgrade() -> None:
    raise RuntimeError(
        "Cannot safely downgrade valid scrypt credentials to an incompatible digest constraint"
    )
