"""Infrastructure storage settings (PostgreSQL, Redis, Qdrant)."""

from pydantic import Field

from .base import _BaseEnvSettings


class DatabaseSettings(_BaseEnvSettings):
    """数据库配置"""

    url: str = Field(
        default="postgresql+asyncpg://globalid:globalid_dev_password@localhost:5432/globalid",
        validation_alias="DATABASE_URL",
        description="异步数据库连接URL",
    )
    url_sync: str = Field(
        default="postgresql://globalid:globalid_dev_password@localhost:5432/globalid",
        validation_alias="DATABASE_URL_SYNC",
        description="同步数据库连接URL（用于Alembic）",
    )
    echo: bool = Field(default=False, description="是否打印SQL语句")
    pool_size: int = Field(default=10, description="连接池大小")
    max_overflow: int = Field(default=20, description="最大溢出连接数")


class RedisSettings(_BaseEnvSettings):
    """Redis配置"""

    url: str = Field(default="redis://localhost:6379/0", description="Redis连接URL")
    encoding: str = Field(default="utf-8", description="编码")
    decode_responses: bool = Field(default=True, description="自动解码响应")


class QdrantSettings(_BaseEnvSettings):
    """Qdrant向量数据库配置"""

    url: str = Field(default="http://localhost:6333", description="Qdrant连接URL")
    api_key: str | None = Field(default=None, description="API密钥")
    collection_name: str = Field(default="agent_memory_v1", description="集合名称")
    vector_size: int = Field(default=1536, description="向量维度")


__all__ = [
    "DatabaseSettings",
    "RedisSettings",
    "QdrantSettings",
]
