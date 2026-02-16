"""
GlobalID V2 Task Management Models

Task management models: Track progress and status of each work unit using UUIDs
"""
import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional, List

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Index, Integer, JSON, String, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import BaseModel


class TaskStatus(str, PyEnum):
    """Task status enumeration"""
    PENDING = "pending"  # Waiting to be processed
    QUEUED = "queued"  # Added to queue
    RUNNING = "running"  # Currently running
    COMPLETED = "completed"  # Successfully completed
    FAILED = "failed"  # Failed with error
    CANCELLED = "cancelled"  # Cancelled by user
    RETRYING = "retrying"  # Retrying after failure


class TaskType(str, PyEnum):
    """Task type enumeration"""
    CRAWL_DATA = "crawl_data"  # Data crawling task
    PROCESS_DATA = "process_data"  # Data processing task
    GENERATE_REPORT = "generate_report"  # Report generation task
    GENERATE_SECTION = "generate_section"  # Section generation task
    REVIEW_SECTION = "review_section"  # Section review task
    EXPORT_DATA = "export_data"  # Data export task
    SEND_EMAIL = "send_email"  # Email sending task


class TaskPriority(str, PyEnum):
    """Task priority enumeration"""
    LOW = "low"  # Low priority
    NORMAL = "normal"  # Normal priority
    HIGH = "high"  # High priority
    URGENT = "urgent"  # Urgent priority


class Task(BaseModel):
    """
    Task Model
    
    Track progress and status of each work unit using UUIDs.
    Supports hierarchical task structure with parent-child relationships.
    """
    __tablename__ = "tasks"
    
    # UUID identifier (primary identifier)
    task_uuid: Mapped[str] = mapped_column(
        String(36),
        unique=True,
        nullable=False,
        default=lambda: str(uuid.uuid4()),
        comment="Task UUID - unique identifier"
    )
    
    # Basic task information
    task_type: Mapped[str] = mapped_column(
        Enum(TaskType),
        nullable=False,
        comment="Task type (crawl, process, generate, etc.)"
    )
    task_name: Mapped[str] = mapped_column(String(500), nullable=False, comment="Task name")
    description: Mapped[Optional[str]] = mapped_column(Text, comment="Task description")
    
    # Status and priority
    status: Mapped[str] = mapped_column(
        Enum(TaskStatus),
        nullable=False,
        default=TaskStatus.PENDING,
        comment="Current task status"
    )
    priority: Mapped[str] = mapped_column(
        Enum(TaskPriority),
        nullable=False,
        default=TaskPriority.NORMAL,
        comment="Task priority level"
    )
    
    # Relationships
    country_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("countries.id", ondelete="SET NULL"),
        comment="Associated country ID"
    )
    report_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("reports.id", ondelete="SET NULL"),
        comment="Associated report ID"
    )
    parent_task_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("tasks.id", ondelete="SET NULL"),
        comment="Parent task ID for hierarchical tasks"
    )
    
    # Progress tracking
    progress: Mapped[int] = mapped_column(Integer, default=0, comment="Progress percentage (0-100)")
    total_steps: Mapped[int] = mapped_column(Integer, default=1, comment="Total number of steps")
    completed_steps: Mapped[int] = mapped_column(Integer, default=0, comment="Number of completed steps")
    
    # Timing information
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, comment="Task start time")
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, comment="Task completion time")
    estimated_duration: Mapped[Optional[int]] = mapped_column(Integer, comment="Estimated duration in seconds")
    actual_duration: Mapped[Optional[int]] = mapped_column(Integer, comment="Actual duration in seconds")
    
    # Retry information
    retry_count: Mapped[int] = mapped_column(Integer, default=0, comment="Number of retry attempts")
    max_retries: Mapped[int] = mapped_column(Integer, default=3, comment="Maximum retry attempts")
    last_error: Mapped[Optional[str]] = mapped_column(Text, comment="Last error message")
    
    # Input and output data
    input_data = Column(JSON, nullable=False, default=dict, comment="Input data for the task")
    output_data = Column(JSON, nullable=False, default=dict, comment="Output data from the task")
    
    # Tags and metadata
    tags = Column(JSON, nullable=False, default=list, comment="List of tags for categorization")
    metadata_ = Column("metadata", JSON, nullable=False, default=dict, comment="Additional metadata")
    
    # Relationships
    workbook_entries: Mapped[List["TaskWorkbook"]] = relationship(
        "TaskWorkbook",
        back_populates="task",
        cascade="all, delete-orphan"
    )
    
    # Subtasks (child tasks)
    subtasks: Mapped[List["Task"]] = relationship(
        "Task",
        backref="parent_task",
        remote_side="Task.id",
        foreign_keys="Task.parent_task_id",
        cascade="all, delete-orphan",
        single_parent=True,
    )
    
    # Indexes for efficient querying
    __table_args__ = (
        Index("idx_task_uuid", "task_uuid"),
        Index("idx_task_status", "status"),
        Index("idx_task_type", "task_type"),
        Index("idx_task_country", "country_id"),
        Index("idx_task_report", "report_id"),
        Index("idx_task_parent", "parent_task_id"),
        Index("idx_task_created", "created_at"),
    )
    
    def __repr__(self) -> str:
        return f"<Task(uuid={self.task_uuid}, type={self.task_type}, status={self.status})>"
    
    def update_progress(self, completed: int, total: int):
        """
        Update task progress
        
        Args:
            completed: Number of completed steps
            total: Total number of steps
        """
        self.completed_steps = completed
        self.total_steps = total
        self.progress = int((completed / total) * 100) if total > 0 else 0


class TaskWorkbook(BaseModel):
    """
    Task Workbook Model
    
    Records detailed execution information and interaction content for each task.
    Acts as a comprehensive log of all inputs, outputs, and AI interactions.
    """
    __tablename__ = "task_workbook"
    
    # Associated task
    task_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        comment="Associated task ID"
    )
    
    # UUID identifier
    entry_uuid: Mapped[str] = mapped_column(
        String(36),
        unique=True,
        nullable=False,
        default=lambda: str(uuid.uuid4()),
        comment="Entry UUID - unique identifier"
    )
    
    # Entry type
    entry_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Entry type (input/output/interaction/log/error)"
    )
    
    # Content information
    title: Mapped[str] = mapped_column(String(500), nullable=False, comment="Entry title")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="Entry content")
    content_type: Mapped[str] = mapped_column(
        String(50),
        default="text",
        comment="Content type (text/json/markdown/html)"
    )
    
    # AI interaction information
    prompt: Mapped[Optional[str]] = mapped_column(Text, comment="AI prompt used")
    response: Mapped[Optional[str]] = mapped_column(Text, comment="AI response received")
    model_used: Mapped[Optional[str]] = mapped_column(String(100), comment="AI model used")
    tokens_used: Mapped[Optional[int]] = mapped_column(Integer, comment="Number of tokens used")
    cost: Mapped[Optional[float]] = mapped_column(comment="Cost in USD")
    
    # Execution information
    duration: Mapped[Optional[float]] = mapped_column(comment="Duration in seconds")
    success: Mapped[bool] = mapped_column(default=True, comment="Whether the operation was successful")
    error_message: Mapped[Optional[str]] = mapped_column(Text, comment="Error message if failed")
    
    # Metadata
    metadata_ = Column("metadata", JSON, nullable=False, default=dict, comment="Additional metadata")
    
    # Relationships
    task: Mapped["Task"] = relationship("Task", back_populates="workbook_entries")
    
    # Indexes
    __table_args__ = (
        Index("idx_workbook_task", "task_id"),
        Index("idx_workbook_uuid", "entry_uuid"),
        Index("idx_workbook_type", "entry_type"),
        Index("idx_workbook_created", "created_at"),
    )
    
    def __repr__(self) -> str:
        return f"<TaskWorkbook(uuid={self.entry_uuid}, type={self.entry_type})>"


class TaskDependency(BaseModel):
    """
    Task Dependency Model
    
    Manages dependencies between tasks.
    Supports different dependency types (finish, start, success).
    """
    __tablename__ = "task_dependencies"
    
    # Dependent task
    task_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        comment="Task ID that has the dependency"
    )
    
    # Task that is depended on
    depends_on_task_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        comment="Task ID that is depended on"
    )
    
    # Dependency type
    dependency_type: Mapped[str] = mapped_column(
        String(50),
        default="finish",
        comment="Dependency type (finish/start/success)"
    )
    
    # Whether the dependency is required
    is_required: Mapped[bool] = mapped_column(default=True, comment="Whether this dependency is required")
    
    # Metadata
    metadata_ = Column("metadata", JSON, nullable=False, default=dict, comment="Additional metadata")
    
    # Indexes
    __table_args__ = (
        Index("idx_dependency_task", "task_id"),
        Index("idx_dependency_depends", "depends_on_task_id"),
    )
    
    def __repr__(self) -> str:
        return f"<TaskDependency(task={self.task_id}, depends_on={self.depends_on_task_id})>"
