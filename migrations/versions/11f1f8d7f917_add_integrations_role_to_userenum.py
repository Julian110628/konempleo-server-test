"""Add 'integrations' role to userenum

Revision ID: 11f1f8d7f917
Revises: 95b2a6861c05
Create Date: 2025-01-03 13:38:30.109525

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM

# revision identifiers, used by Alembic.
revision: str = '11f1f8d7f917'
down_revision: Union[str, None] = '95b2a6861c05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

user_enum = ENUM('super_admin', 'admin', 'company', 'company_recruit', 'integrations', name='userenum', create_type=False)

def upgrade():
    # Add the new value to the enum
    with op.get_context().autocommit_block():
        op.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM pg_enum e
            JOIN pg_type t ON e.enumtypid = t.oid
            WHERE t.typname = 'userenum'
              AND e.enumlabel = 'integrations'
        ) THEN
            ALTER TYPE userenum ADD VALUE 'integrations';
        END IF;
    END$$;
    """)

def downgrade():
    pass