"""
GlobalID V2 Task Manager

任务管理器：管理任务的创建、执行、追踪和恢复
"""
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any
from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload

from src.core.database import get_db
from src.core.logging import get_logger
from src.domain import Task, TaskWorkbook, TaskStatus, TaskType, TaskPriority

logger = get_logger(__name__)


class TaskManager:
    """任务管理器"""
    
    async def create_task(
        self,
        task_type: TaskType,
        task_name: str,
        country_id: Optional[int] = None,
        report_id: Optional[int] = None,
        parent_task_id: Optional[int] = None,
        priority: TaskPriority = TaskPriority.NORMAL,
        input_data: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
        description: Optional[str] = None,
    ) -> Task:
        """
        创建新任务
        
        Args:
            task_type: 任务类型
            task_name: 任务名称
            country_id: 国家ID
            report_id: 报告ID
            parent_task_id: 父任务ID
            priority: 优先级
            input_data: 输入数据
            tags: 标签列表
            description: 任务描述
            
        Returns:
            创建的任务对象
        """
        async with get_db() as db:
            task = Task(
                task_type=task_type,
                task_name=task_name,
                description=description,
                priority=priority,
                country_id=country_id,
                report_id=report_id,
                parent_task_id=parent_task_id,
                input_data=input_data or {},
                tags=tags or [],
            )
            
            db.add(task)
            await db.commit()
            await db.refresh(task)
            
            logger.info(f"Created task: {task.task_uuid} - {task_name}")
            return task
    
    async def get_task_by_uuid(self, task_uuid: str) -> Optional[Task]:
        """通过UUID获取任务"""
        async with get_db() as db:
            query = select(Task).where(Task.task_uuid == task_uuid)
            result = await db.execute(query)
            return result.scalar_one_or_none()
    
    async def get_task_by_id(self, task_id: int) -> Optional[Task]:
        """通过ID获取任务"""
        async with get_db() as db:
            query = select(Task).where(Task.id == task_id)
            result = await db.execute(query)
            return result.scalar_one_or_none()
    
    async def update_task_status(
        self,
        task_uuid: str,
        status: TaskStatus,
        error_message: Optional[str] = None,
    ) -> Optional[Task]:
        """更新任务状态"""
        async with get_db() as db:
            query = select(Task).where(Task.task_uuid == task_uuid)
            result = await db.execute(query)
            task = result.scalar_one_or_none()
            
            if not task:
                return None
            
            task.status = status
            
            if status == TaskStatus.RUNNING and not task.started_at:
                task.started_at = datetime.now()
            elif status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
                task.completed_at = datetime.now()
                if task.started_at:
                    task.actual_duration = int((task.completed_at - task.started_at).total_seconds())
            
            if error_message:
                task.last_error = error_message
                task.retry_count += 1
            
            await db.commit()
            await db.refresh(task)
            
            logger.info(f"Updated task {task_uuid}: {status}")
            return task
    
    async def update_task_progress(
        self,
        task_uuid: str,
        completed_steps: int,
        total_steps: int,
    ) -> Optional[Task]:
        """更新任务进度"""
        async with get_db() as db:
            query = select(Task).where(Task.task_uuid == task_uuid)
            result = await db.execute(query)
            task = result.scalar_one_or_none()
            
            if not task:
                return None
            
            task.update_progress(completed_steps, total_steps)
            await db.commit()
            await db.refresh(task)
            
            logger.debug(f"Updated task {task_uuid} progress: {task.progress}%")
            return task
    
    async def add_workbook_entry(
        self,
        task_uuid: str,
        entry_type: str,
        title: str,
        content: str,
        content_type: str = "text",
        prompt: Optional[str] = None,
        response: Optional[str] = None,
        model_used: Optional[str] = None,
        tokens_used: Optional[int] = None,
        cost: Optional[float] = None,
        duration: Optional[float] = None,
        success: bool = True,
        error_message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TaskWorkbook:
        """
        添加工作簿条目
        
        Args:
            task_uuid: 任务UUID
            entry_type: 条目类型（input/output/interaction/log/error）
            title: 标题
            content: 内容
            content_type: 内容类型
            prompt: AI提示词
            response: AI响应
            model_used: 使用的模型
            tokens_used: 使用的Token数
            cost: 成本
            duration: 耗时
            success: 是否成功
            error_message: 错误信息
            metadata: 元数据
            
        Returns:
            创建的工作簿条目
        """
        async with get_db() as db:
            # 获取任务
            query = select(Task).where(Task.task_uuid == task_uuid)
            result = await db.execute(query)
            task = result.scalar_one_or_none()
            
            if not task:
                raise ValueError(f"Task not found: {task_uuid}")
            
            # 创建工作簿条目
            entry = TaskWorkbook(
                task_id=task.id,
                entry_type=entry_type,
                title=title,
                content=content,
                content_type=content_type,
                prompt=prompt,
                response=response,
                model_used=model_used,
                tokens_used=tokens_used,
                cost=cost,
                duration=duration,
                success=success,
                error_message=error_message,
                metadata_=metadata or {},
            )
            
            db.add(entry)
            await db.commit()
            await db.refresh(entry)
            
            logger.debug(f"Added workbook entry: {entry.entry_uuid}")
            return entry
    
    async def get_pending_tasks(
        self,
        task_type: Optional[TaskType] = None,
        country_id: Optional[int] = None,
        limit: int = 10,
    ) -> List[Task]:
        """获取待处理的任务"""
        async with get_db() as db:
            query = select(Task).where(Task.status == TaskStatus.PENDING)
            
            if task_type:
                query = query.where(Task.task_type == task_type)
            if country_id:
                query = query.where(Task.country_id == country_id)
            
            query = query.order_by(Task.priority.desc(), Task.created_at.asc()).limit(limit)
            
            result = await db.execute(query)
            return list(result.scalars().all())
    
    async def get_running_tasks(
        self,
        task_type: Optional[TaskType] = None,
        country_id: Optional[int] = None,
    ) -> List[Task]:
        """获取正在运行的任务"""
        async with get_db() as db:
            query = select(Task).where(Task.status == TaskStatus.RUNNING)
            
            if task_type:
                query = query.where(Task.task_type == task_type)
            if country_id:
                query = query.where(Task.country_id == country_id)
            
            query = query.order_by(Task.started_at.asc())
            
            result = await db.execute(query)
            return list(result.scalars().all())
    
    async def get_task_workbook(self, task_uuid: str) -> List[TaskWorkbook]:
        """获取任务的工作簿"""
        async with get_db() as db:
            query = (
                select(TaskWorkbook)
                .join(Task)
                .where(Task.task_uuid == task_uuid)
                .order_by(TaskWorkbook.created_at.asc())
            )
            
            result = await db.execute(query)
            return list(result.scalars().all())
    
    async def get_task_statistics(self) -> Dict[str, Any]:
        """获取任务统计信息"""
        async with get_db() as db:
            from sqlalchemy import func
            
            # 按状态统计
            status_query = (
                select(Task.status, func.count(Task.id))
                .group_by(Task.status)
            )
            status_result = await db.execute(status_query)
            status_counts = {row[0].value: row[1] for row in status_result}
            
            # 按类型统计
            type_query = (
                select(Task.task_type, func.count(Task.id))
                .group_by(Task.task_type)
            )
            type_result = await db.execute(type_query)
            type_counts = {row[0].value: row[1] for row in type_result}
            
            return {
                "by_status": status_counts,
                "by_type": type_counts,
                "total": sum(status_counts.values()),
            }


# 全局任务管理器实例
task_manager = TaskManager()
