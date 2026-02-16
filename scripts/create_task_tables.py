"""
创建任务管理表

Revision ID: create_task_tables
Revises: 
Create Date: 2026-02-16

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'create_task_tables'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 创建任务表
    op.create_table(
        'tasks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('task_uuid', sa.String(36), nullable=False, unique=True),
        sa.Column('task_type', sa.Enum('crawl_data', 'process_data', 'generate_report', 'generate_section', 'review_section', 'export_data', 'send_email', name='tasktype'), nullable=False),
        sa.Column('task_name', sa.String(500), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('status', sa.Enum('pending', 'queued', 'running', 'completed', 'failed', 'cancelled', 'retrying', name='taskstatus'), nullable=False, server_default='pending'),
        sa.Column('priority', sa.Enum('low', 'normal', 'high', 'urgent', name='taskpriority'), nullable=False, server_default='normal'),
        sa.Column('country_id', sa.Integer(), nullable=True),
        sa.Column('report_id', sa.Integer(), nullable=True),
        sa.Column('parent_task_id', sa.Integer(), nullable=True),
        sa.Column('progress', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_steps', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('completed_steps', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('estimated_duration', sa.Integer(), nullable=True),
        sa.Column('actual_duration', sa.Integer(), nullable=True),
        sa.Column('retry_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('max_retries', sa.Integer(), nullable=False, server_default='3'),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('input_data', postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('output_data', postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('tags', postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('metadata', postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['country_id'], ['countries.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['report_id'], ['reports.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['parent_task_id'], ['tasks.id'], ondelete='SET NULL'),
    )
    
    # 创建任务表索引
    op.create_index('idx_task_uuid', 'tasks', ['task_uuid'])
    op.create_index('idx_task_status', 'tasks', ['status'])
    op.create_index('idx_task_type', 'tasks', ['task_type'])
    op.create_index('idx_task_country', 'tasks', ['country_id'])
    op.create_index('idx_task_report', 'tasks', ['report_id'])
    op.create_index('idx_task_parent', 'tasks', ['parent_task_id'])
    op.create_index('idx_task_created', 'tasks', ['created_at'])
    
    # 创建任务工作簿表
    op.create_table(
        'task_workbook',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('task_id', sa.Integer(), nullable=False),
        sa.Column('entry_uuid', sa.String(36), nullable=False, unique=True),
        sa.Column('entry_type', sa.String(50), nullable=False),
        sa.Column('title', sa.String(500), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('content_type', sa.String(50), nullable=False, server_default='text'),
        sa.Column('prompt', sa.Text(), nullable=True),
        sa.Column('response', sa.Text(), nullable=True),
        sa.Column('model_used', sa.String(100), nullable=True),
        sa.Column('tokens_used', sa.Integer(), nullable=True),
        sa.Column('cost', sa.Float(), nullable=True),
        sa.Column('duration', sa.Float(), nullable=True),
        sa.Column('success', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('metadata', postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
    )
    
    # 创建任务工作簿表索引
    op.create_index('idx_workbook_task', 'task_workbook', ['task_id'])
    op.create_index('idx_workbook_uuid', 'task_workbook', ['entry_uuid'])
    op.create_index('idx_workbook_type', 'task_workbook', ['entry_type'])
    op.create_index('idx_workbook_created', 'task_workbook', ['created_at'])
    
    # 创建任务依赖关系表
    op.create_table(
        'task_dependencies',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('task_id', sa.Integer(), nullable=False),
        sa.Column('depends_on_task_id', sa.Integer(), nullable=False),
        sa.Column('dependency_type', sa.String(50), nullable=False, server_default='finish'),
        sa.Column('is_required', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('metadata', postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['depends_on_task_id'], ['tasks.id'], ondelete='CASCADE'),
    )
    
    # 创建任务依赖关系表索引
    op.create_index('idx_dependency_task', 'task_dependencies', ['task_id'])
    op.create_index('idx_dependency_depends', 'task_dependencies', ['depends_on_task_id'])


def downgrade() -> None:
    # 删除任务依赖关系表
    op.drop_index('idx_dependency_depends', 'task_dependencies')
    op.drop_index('idx_dependency_task', 'task_dependencies')
    op.drop_table('task_dependencies')
    
    # 删除任务工作簿表
    op.drop_index('idx_workbook_created', 'task_workbook')
    op.drop_index('idx_workbook_type', 'task_workbook')
    op.drop_index('idx_workbook_uuid', 'task_workbook')
    op.drop_index('idx_workbook_task', 'task_workbook')
    op.drop_table('task_workbook')
    
    # 删除任务表
    op.drop_index('idx_task_created', 'tasks')
    op.drop_index('idx_task_parent', 'tasks')
    op.drop_index('idx_task_report', 'tasks')
    op.drop_index('idx_task_country', 'tasks')
    op.drop_index('idx_task_type', 'tasks')
    op.drop_index('idx_task_status', 'tasks')
    op.drop_index('idx_task_uuid', 'tasks')
    op.drop_table('tasks')
    
    # 删除枚举类型
    op.execute('DROP TYPE IF EXISTS tasktype')
    op.execute('DROP TYPE IF EXISTS taskstatus')
    op.execute('DROP TYPE IF EXISTS taskpriority')
