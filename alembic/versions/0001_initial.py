from __future__ import annotations

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The application uses SQLAlchemy metadata creation for the local MVP. This migration marks
    # the first schema version so future structural changes have a stable base.
    bind = op.get_bind()
    from botc_ai.infra import orm  # noqa: F401
    from botc_ai.infra.db import Base

    Base.metadata.create_all(bind)


def downgrade() -> None:
    bind = op.get_bind()
    from botc_ai.infra import orm  # noqa: F401
    from botc_ai.infra.db import Base

    Base.metadata.drop_all(bind)
