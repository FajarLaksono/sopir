"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-09-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    op.create_table(
        'scenarios',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('uuid_generate_v4()')),
        sa.Column('type', sa.String(50), nullable=False),
        sa.Column('config', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('net_file_path', sa.String(500)),
        sa.Column('route_file_path', sa.String(500)),
        sa.Column('config_file_path', sa.String(500)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )
    op.create_index('ix_scenarios_type', 'scenarios', ['type'])

    op.create_table(
        'simulation_runs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('uuid_generate_v4()')),
        sa.Column('scenario_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('scenarios.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='queued'),
        sa.Column('worker_id', sa.String(100)),
        sa.Column('started_at', sa.DateTime(timezone=True)),
        sa.Column('completed_at', sa.DateTime(timezone=True)),
        sa.Column('error_message', sa.Text()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )
    op.create_index('ix_runs_scenario', 'simulation_runs', ['scenario_id'])
    op.create_index('ix_runs_status', 'simulation_runs', ['status'])
    op.create_index('ix_runs_worker', 'simulation_runs', ['worker_id'])

    op.create_table(
        'telemetry',
        sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('run_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('simulation_runs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('step', sa.Integer, nullable=False),
        sa.Column('vehicle_id', sa.String(100), nullable=False),
        sa.Column('x', sa.Float, nullable=False),
        sa.Column('y', sa.Float, nullable=False),
        sa.Column('speed', sa.Float, nullable=False),
        sa.Column('angle', sa.Float, nullable=False),
        sa.Column('lane_id', sa.String(100), nullable=False),
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )
    op.create_index('ix_telemetry_run_step', 'telemetry', ['run_id', 'step'])
    op.create_index('ix_telemetry_run_vehicle', 'telemetry', ['run_id', 'vehicle_id'])

    op.create_table(
        'metrics',
        sa.Column('run_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('simulation_runs.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('collision_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('min_ttc', sa.Float),
        sa.Column('avg_speed', sa.Float, nullable=False),
        sa.Column('speed_violations', sa.Integer, nullable=False, server_default='0'),
        sa.Column('lane_deviations', sa.Integer, nullable=False, server_default='0'),
        sa.Column('computed_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )

    op.create_table(
        'failures',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('uuid_generate_v4()')),
        sa.Column('run_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('simulation_runs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('severity', sa.String(20), nullable=False),
        sa.Column('rule', sa.String(100), nullable=False),
        sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )
    op.create_index('ix_failures_run', 'failures', ['run_id'])
    op.create_index('ix_failures_severity', 'failures', ['severity'])

    op.execute("""
        CREATE OR REPLACE FUNCTION update_updated_at()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        BEGIN NEW.updated_at = now(); RETURN NEW; END $$;
    """)
    op.execute("""
        CREATE TRIGGER trg_scenarios_updated
        BEFORE UPDATE ON scenarios
        FOR EACH ROW EXECUTE FUNCTION update_updated_at();
    """)
    op.execute("""
        CREATE TRIGGER trg_runs_updated
        BEFORE UPDATE ON simulation_runs
        FOR EACH ROW EXECUTE FUNCTION update_updated_at();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_runs_updated ON simulation_runs")
    op.execute("DROP TRIGGER IF EXISTS trg_scenarios_updated ON scenarios")
    op.execute("DROP FUNCTION IF EXISTS update_updated_at()")

    op.drop_table('failures')
    op.drop_table('metrics')
    op.drop_table('telemetry')
    op.drop_table('simulation_runs')
    op.drop_table('scenarios')
    op.execute('DROP EXTENSION IF EXISTS "uuid-ossp"')