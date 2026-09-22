"""ZEN-008: OCI Agent registry and immutable versions.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_BUILTIN_AGENT_ID = "01930000-0000-7000-8000-000000000001"
_BUILTIN_VERSION_ID = "01930000-0000-7000-8000-000000000002"
_BUILTIN_DIGEST = "sha256:" + "0" * 64

# JSON 以綁定參數傳入：text() 會把字面值裡的 `:true` 當成參數名解析掉。
_BUILTIN_MANIFEST = '{"agent_version": "0.0.0-builtin", "builtin": true}'
_BUILTIN_ENTRYPOINTS = (
    '[{"name": "default", "description": "Echo a question",'
    ' "input_schema": {"type": "object", "properties": {"question": {"type": "string"}},'
    ' "required": ["question"]},'
    ' "output_schema": {"type": "object", "properties": {"echo": {"type": "string"}},'
    ' "required": ["echo"]}}]'
)


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True)),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "agent_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("semver", sa.String(64), nullable=False),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("image_ref", sa.Text(), nullable=False),
        sa.Column("image_digest", sa.String(71), nullable=False),
        sa.Column("version_id", sa.String(12), nullable=False),
        sa.Column("image_platform", sa.String(64)),
        sa.Column("image_size_bytes", sa.BigInteger()),
        sa.Column("runtime_contract", sa.String(64)),
        sa.Column("manifest", postgresql.JSONB()),
        sa.Column("entrypoints", postgresql.JSONB()),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", postgresql.JSONB()),
        sa.Column("validation_payload", postgresql.JSONB()),
        sa.Column("build_claimed_at", sa.DateTime(timezone=True)),
        sa.Column("builder_task_id", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("agent_id", "semver", name="uq_agent_versions_agent_semver"),
        sa.CheckConstraint("source_type = 'OCI_IMAGE'", name="ck_agent_versions_source_type"),
        sa.CheckConstraint("status IN ('BUILDING','PUBLISHED','FAILED')",
                           name="ck_agent_versions_status"),
        sa.CheckConstraint("image_digest ~ '^sha256:[0-9a-f]{64}$'",
                           name="ck_agent_versions_digest"),
        sa.CheckConstraint("version_id ~ '^[0-9a-f]{12}$'",
                           name="ck_agent_versions_version_id"),
    )
    op.create_index(
        "ix_agent_versions_agent_status_created", "agent_versions",
        ["agent_id", "status", sa.text("created_at DESC")],
    )

    # Preserve the ZEN-000 fixture rows while the public path moves to the
    # registry. New OCI versions never use this reserved digest.
    op.execute(sa.text(
        "INSERT INTO agents (id, name, description) "
        "VALUES (CAST(:id AS uuid), 'builtin-echo', 'ZEN-000 compatibility fixture') "
        "ON CONFLICT (id) DO NOTHING"
    ).bindparams(id=_BUILTIN_AGENT_ID))
    op.execute(sa.text(
        "INSERT INTO agent_versions "
        "(id, agent_id, semver, source_type, image_ref, image_digest, version_id, "
        " image_platform, runtime_contract, manifest, entrypoints, status, published_at) "
        "VALUES (CAST(:id AS uuid), CAST(:agent_id AS uuid), '0.0.0-builtin', 'OCI_IMAGE', :image_ref, :digest, "
        " '000000000000', 'linux/amd64', 'zentra.image/v1', "
        " CAST(:manifest AS jsonb), "
        " CAST(:entrypoints AS jsonb), "
        " 'PUBLISHED', now()) ON CONFLICT (id) DO NOTHING"
    ).bindparams(
        id=_BUILTIN_VERSION_ID,
        agent_id=_BUILTIN_AGENT_ID,
        image_ref=f"zentra.local/builtin@{_BUILTIN_DIGEST}",
        digest=_BUILTIN_DIGEST,
        manifest=_BUILTIN_MANIFEST,
        entrypoints=_BUILTIN_ENTRYPOINTS,
    ))

    op.create_foreign_key("fk_runs_agent", "runs", "agents", ["agent_id"], ["id"])
    op.create_foreign_key(
        "fk_runs_agent_version", "runs", "agent_versions", ["agent_version_id"], ["id"]
    )
    op.create_check_constraint("ck_runs_trigger", "runs", "trigger IN ('API','MCP')")


def downgrade() -> None:
    op.drop_constraint("ck_runs_trigger", "runs", type_="check")
    op.drop_constraint("fk_runs_agent_version", "runs", type_="foreignkey")
    op.drop_constraint("fk_runs_agent", "runs", type_="foreignkey")
    op.drop_index("ix_agent_versions_agent_status_created", table_name="agent_versions")
    op.drop_table("agent_versions")
    op.drop_table("agents")
