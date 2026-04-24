"""Add tenant_id to enterprise_info and scope records per tenant.

Revision ID: add_enterprise_info_tenant_id
Revises: increase_api_key_length
Create Date: 2026-04-23
"""

import uuid

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "add_enterprise_info_tenant_id"
down_revision = "increase_api_key_length"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    op.execute(
        "ALTER TABLE enterprise_info "
        "ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id)"
    )

    # The old schema enforced a single global record per info_type.
    # Replace that with one record per (tenant_id, info_type).
    op.execute("ALTER TABLE enterprise_info DROP CONSTRAINT IF EXISTS enterprise_info_info_type_key")
    op.execute("DROP INDEX IF EXISTS ix_enterprise_info_info_type")

    rows = conn.execute(
        sa.text(
            """
            SELECT id, info_type, content, version, visible_roles, updated_by, created_at, updated_at
            FROM enterprise_info
            WHERE tenant_id IS NULL
            ORDER BY created_at ASC NULLS LAST, info_type ASC
            """
        )
    ).mappings().all()
    tenants = conn.execute(
        sa.text("SELECT id FROM tenants ORDER BY created_at ASC NULLS LAST, id ASC")
    ).mappings().all()

    if rows and tenants:
        first_tenant_id = tenants[0]["id"]

        # Reuse existing rows for the earliest tenant to avoid rewriting JSON blobs.
        conn.execute(
            sa.text(
                """
                UPDATE enterprise_info
                SET tenant_id = :tenant_id
                WHERE tenant_id IS NULL
                """
            ),
            {"tenant_id": first_tenant_id},
        )

        # Copy the previously-global records to every other tenant so each
        # company starts with its own editable snapshot after the migration.
        for tenant in tenants[1:]:
            tenant_id = tenant["id"]
            for row in rows:
                existing = conn.execute(
                    sa.text(
                        """
                        SELECT 1
                        FROM enterprise_info
                        WHERE tenant_id = :tenant_id AND info_type = :info_type
                        LIMIT 1
                        """
                    ),
                    {"tenant_id": tenant_id, "info_type": row["info_type"]},
                ).scalar_one_or_none()
                if existing:
                    continue

                conn.execute(
                    sa.text(
                        """
                        INSERT INTO enterprise_info (
                            id, tenant_id, info_type, content, version,
                            visible_roles, updated_by, created_at, updated_at
                        )
                        VALUES (
                            :id, :tenant_id, :info_type, :content, :version,
                            :visible_roles, :updated_by, :created_at, :updated_at
                        )
                        """
                    ),
                    {
                        "id": uuid.uuid4(),
                        "tenant_id": tenant_id,
                        "info_type": row["info_type"],
                        "content": row["content"],
                        "version": row["version"],
                        "visible_roles": row["visible_roles"],
                        "updated_by": row["updated_by"],
                        "created_at": row["created_at"],
                        "updated_at": row["updated_at"],
                    },
                )

    op.execute("CREATE INDEX IF NOT EXISTS ix_enterprise_info_tenant_id ON enterprise_info (tenant_id)")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_enterprise_info_tenant_type "
        "ON enterprise_info (tenant_id, info_type)"
    )


def downgrade() -> None:
    # Downgrading is omitted intentionally because the upgrade fans out a
    # global record into per-tenant copies and collapsing that data would be lossy.
    pass
