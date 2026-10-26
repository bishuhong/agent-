"""Pinned LangGraph PostgreSQL checkpoint schema (3.2.0)."""

from alembic import op

revision = "0002_langgraph_checkpoints"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

SQL = (
    "CREATE TABLE IF NOT EXISTS checkpoint_migrations (\n    v INTEGER PRIMARY KEY\n);",
    "CREATE TABLE IF NOT EXISTS checkpoints (\n    thread_id TEXT NOT NULL,\n    checkpoint_ns TEXT NOT NULL DEFAULT '',\n    checkpoint_id TEXT NOT NULL,\n    parent_checkpoint_id TEXT,\n    type TEXT,\n    checkpoint JSONB NOT NULL,\n    metadata JSONB NOT NULL DEFAULT '{}',\n    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)\n);",
    "CREATE TABLE IF NOT EXISTS checkpoint_blobs (\n    thread_id TEXT NOT NULL,\n    checkpoint_ns TEXT NOT NULL DEFAULT '',\n    channel TEXT NOT NULL,\n    version TEXT NOT NULL,\n    type TEXT NOT NULL,\n    blob BYTEA,\n    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)\n);",
    "CREATE TABLE IF NOT EXISTS checkpoint_writes (\n    thread_id TEXT NOT NULL,\n    checkpoint_ns TEXT NOT NULL DEFAULT '',\n    checkpoint_id TEXT NOT NULL,\n    task_id TEXT NOT NULL,\n    idx INTEGER NOT NULL,\n    channel TEXT NOT NULL,\n    type TEXT,\n    blob BYTEA NOT NULL,\n    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)\n);",
    "ALTER TABLE checkpoint_blobs ALTER COLUMN blob DROP not null;",
    "SELECT 1;",
    "\n    CREATE INDEX CONCURRENTLY IF NOT EXISTS checkpoints_thread_id_idx ON checkpoints(thread_id);\n    ",
    "\n    CREATE INDEX CONCURRENTLY IF NOT EXISTS checkpoint_blobs_thread_id_idx ON checkpoint_blobs(thread_id);\n    ",
    "\n    CREATE INDEX CONCURRENTLY IF NOT EXISTS checkpoint_writes_thread_id_idx ON checkpoint_writes(thread_id);\n    ",
    "ALTER TABLE checkpoint_writes ADD COLUMN IF NOT EXISTS task_path TEXT NOT NULL DEFAULT '';",
)


def upgrade():
    for index, statement in enumerate(SQL):
        if "CONCURRENTLY" in statement:
            with op.get_context().autocommit_block():
                op.execute(statement)
        else:
            op.execute(statement)
        op.execute(
            f"INSERT INTO checkpoint_migrations (v) VALUES ({index}) ON CONFLICT (v) DO NOTHING"
        )


def downgrade():
    raise RuntimeError(
        "Checkpoint removal is destructive; use an explicitly reviewed recovery migration"
    )
