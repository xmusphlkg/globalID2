"""
Test Task Management System

This script tests all functionality of the task management system including:
- Task creation
- Status updates
- Progress tracking
- Workbook entries
- Task queries
- Statistics
"""
import os
import sys

# When running this test file directly, ensure the repository root is on
# `sys.path` so `import src...` works. Running under `pytest` already sets
# up imports correctly, but direct execution needs this helper.
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import asyncio
from src.core.task_manager import task_manager
from src.domain import TaskType, TaskPriority, TaskStatus


async def _test_task_management_async():
    """Async helper that performs the actual test steps"""
    print("=" * 60)
    print("Testing Task Management System")
    print("=" * 60)
    
    # 1. Create task
    print("\n1. Creating task...")
    task = await task_manager.create_task(
        task_type=TaskType.GENERATE_REPORT,
        task_name="Test Report Generation Task",
        priority=TaskPriority.HIGH,
        input_data={
            "period_start": "2026-01-01",
            "period_end": "2026-01-31",
        },
        tags=["test", "report"],
        description="This is a test task"
    )
    print(f"✓ Task created successfully: {task.task_uuid}")
    print(f"  Task ID: {task.id}")
    print(f"  Task Name: {task.task_name}")
    print(f"  Task Status: {task.status}")
    
    # 2. Update task status
    print("\n2. Updating task status...")
    await task_manager.update_task_status(
        task_uuid=task.task_uuid,
        status=TaskStatus.RUNNING
    )
    print(f"✓ Task status updated to: RUNNING")
    
    # 3. Update progress
    print("\n3. Updating task progress...")
    for i in range(1, 6):
        await task_manager.update_task_progress(
            task_uuid=task.task_uuid,
            completed_steps=i,
            total_steps=5
        )
        print(f"  Progress: {i}/5 ({i*20}%)")
    
    # 4. Add workbook entries
    print("\n4. Adding workbook entries...")
    entry1 = await task_manager.add_workbook_entry(
        task_uuid=task.task_uuid,
        entry_type="input",
        title="Input Data",
        content="Input data required for report generation",
        content_type="text"
    )
    print(f"✓ Input entry added: {entry1.entry_uuid}")
    
    entry2 = await task_manager.add_workbook_entry(
        task_uuid=task.task_uuid,
        entry_type="interaction",
        title="AI Analysis",
        content="AI analysis results",
        prompt="Please analyze the data...",
        response="Based on the data analysis...",
        model_used="qwen3-max-preview",
        tokens_used=1000,
        cost=0.02,
        duration=1.5
    )
    print(f"✓ Interaction entry added: {entry2.entry_uuid}")
    
    # 5. Complete task
    print("\n5. Completing task...")
    await task_manager.update_task_status(
        task_uuid=task.task_uuid,
        status=TaskStatus.COMPLETED
    )
    print(f"✓ Task completed")
    
    # 6. Query task
    print("\n6. Querying task...")
    retrieved_task = await task_manager.get_task_by_uuid(task.task_uuid)
    print(f"✓ Task retrieved: {retrieved_task.task_name}")
    print(f"  Status: {retrieved_task.status}")
    print(f"  Progress: {retrieved_task.progress}%")
    print(f"  Actual Duration: {retrieved_task.actual_duration}s")
    
    # 7. Get workbook
    print("\n7. Getting workbook...")
    workbook = await task_manager.get_task_workbook(task.task_uuid)
    print(f"✓ Workbook entries: {len(workbook)}")
    for entry in workbook:
        print(f"  - {entry.entry_type}: {entry.title}")
    
    # 8. Get statistics
    print("\n8. Getting statistics...")
    stats = await task_manager.get_task_statistics()
    print(f"✓ Total tasks: {stats['total']}")
    print(f"  By status: {stats['by_status']}")
    print(f"  By type: {stats['by_type']}")
    
    print("\n" + "=" * 60)
    print("Test completed successfully!")
    print("=" * 60)


def test_task_management():
    """Synchronous wrapper that runs the async test helper"""
    return asyncio.run(_test_task_management_async())


if __name__ == "__main__":
    test_task_management()
