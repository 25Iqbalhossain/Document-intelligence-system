"""reconciliation runs

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reconciliation_runs",
        sa.Column("left_document_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("right_document_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name=op.f("fk_reconciliation_runs_created_by_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reconciliation_runs")),
    )
    # run_id was a plain UUID in 0001; rows without a run cannot reference one, so clear any stray values first
    op.execute("UPDATE document_relationships SET run_id = NULL WHERE run_id IS NOT NULL")
    op.create_foreign_key(
        op.f("fk_document_relationships_run_id_reconciliation_runs"), "document_relationships",
        "reconciliation_runs", ["run_id"], ["id"], ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_document_relationships_run_id_reconciliation_runs"), "document_relationships", type_="foreignkey")
    op.drop_table("reconciliation_runs")
