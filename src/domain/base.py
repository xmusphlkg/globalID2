"""
GlobalID V2 Domain Models Base

SQLAlchemy 模型基类和通用混入类
"""
from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy import Column, DateTime, Integer
from sqlalchemy.orm import DeclarativeBase, declared_attr


class Base(DeclarativeBase):
    """SQLAlchemy 声明式基类"""
    pass


def utc_now() -> datetime:
    """Return an aware UTC timestamp for timezone-aware database columns."""
    return datetime.now(timezone.utc)


class TimestampMixin:
    """时间戳混入类"""
    
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class IDMixin:
    """ID 主键混入类"""
    
    id = Column(Integer, primary_key=True, autoincrement=True)


class BaseModel(Base, IDMixin, TimestampMixin):
    """
    通用基础模型
    
    包含 ID 主键和时间戳字段
    """
    __abstract__ = True
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        result = {}
        for column in self.__table__.columns:
            # The Python attribute can intentionally differ from the database
            # column name (for example ``metadata_`` maps to ``metadata``).
            # Reading by column.name would resolve DeclarativeBase.metadata in
            # that case and leak SQLAlchemy's MetaData object into API output.
            attribute_name = self.__mapper__.get_property_by_column(column).key
            value = getattr(self, attribute_name)
            if isinstance(value, datetime):
                value = value.isoformat()
            result[column.name] = value
        return result
    
    def __repr__(self) -> str:
        """字符串表示"""
        return f"<{self.__class__.__name__}(id={getattr(self, 'id', None)})>"
