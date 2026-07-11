"""集中加载环境变量、工作目录和外部服务配置。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class HuashiSettings(BaseSettings):
    """“化实”运行配置；敏感字段使用 SecretStr，避免意外打印。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    volcengine_api_key: SecretStr | None = Field(
        default=None, validation_alias="VOLCENGINE_API_KEY"
    )
    volcengine_base_url: str | None = Field(
        default=None, validation_alias="VOLCENGINE_BASE_URL"
    )
    chat_model: str = Field(
        default="doubao-seed-2.0-mini", validation_alias="CHAT_MODEL"
    )
    embedding_model: str = Field(
        default="doubao-embedding-vision", validation_alias="EMBEDDING_MODEL"
    )

    tavily_api_key: SecretStr | None = Field(
        default=None, validation_alias="TAVILY_API_KEY"
    )
    mineru_api_token: SecretStr | None = Field(
        default=None, validation_alias="MINERU_API_TOKEN"
    )
    mineru_api_base_url: str = Field(
        default="https://mineru.net", validation_alias="MINERU_API_BASE_URL"
    )

    langsmith_tracing: bool = Field(
        default=False, validation_alias="LANGSMITH_TRACING"
    )
    langsmith_endpoint: str = Field(
        default="https://api.smith.langchain.com",
        validation_alias="LANGSMITH_ENDPOINT",
    )
    langsmith_api_key: SecretStr | None = Field(
        default=None, validation_alias="LANGSMITH_API_KEY"
    )
    langsmith_project: str = Field(
        default="huashi-agent", validation_alias="LANGSMITH_PROJECT"
    )

    workspace_dir: Path = Field(
        default=Path("workspace"), validation_alias="WORKSPACE_DIR"
    )
    max_file_size_mb: int = Field(
        default=20, ge=1, le=200, validation_alias="MAX_FILE_SIZE_MB"
    )
    max_attachments_per_message: int = Field(
        default=3, ge=1, le=10, validation_alias="MAX_ATTACHMENTS_PER_MESSAGE"
    )
    max_attachment_context_chars: int = Field(
        default=12000, ge=1000, le=50000, validation_alias="MAX_ATTACHMENT_CONTEXT_CHARS"
    )
    max_attachment_file_chars: int = Field(
        default=6000, ge=500, le=20000, validation_alias="MAX_ATTACHMENT_FILE_CHARS"
    )
    max_filename_length: int = Field(
        default=180, ge=20, le=220, validation_alias="MAX_FILENAME_LENGTH"
    )
    mineru_poll_interval_seconds: float = Field(
        default=3.0,
        ge=0.1,
        le=30.0,
        validation_alias="MINERU_POLL_INTERVAL_SECONDS",
    )
    mineru_max_poll_attempts: int = Field(
        default=40,
        ge=1,
        le=300,
        validation_alias="MINERU_MAX_POLL_ATTEMPTS",
    )
    tavily_timeout_seconds: float = Field(
        default=15.0, ge=1.0, le=120.0, validation_alias="TAVILY_TIMEOUT_SECONDS"
    )
    model_timeout_seconds: float = Field(
        default=60.0, ge=5.0, le=300.0, validation_alias="MODEL_TIMEOUT_SECONDS"
    )
    heartbeat_enabled: bool = Field(
        default=True, validation_alias="HEARTBEAT_ENABLED"
    )
    heartbeat_interval_seconds: int = Field(
        default=120,
        ge=1,
        le=3600,
        validation_alias="HEARTBEAT_INTERVAL_SECONDS",
    )
    debug: bool = Field(default=False, validation_alias="HUASHI_DEBUG")

    @field_validator("volcengine_base_url", "mineru_api_base_url")
    @classmethod
    def strip_trailing_slash(cls, value: str | None) -> str | None:
        """统一移除服务地址末尾斜杠。"""

        return value.rstrip("/") if value else value

    @property
    def inputs_dir(self) -> Path:
        """允许用户放置待读取文件的目录。"""

        return self.workspace_dir / "inputs"

    @property
    def outputs_dir(self) -> Path:
        """学习笔记和报告草稿的安全输出目录。"""

        return self.workspace_dir / "outputs"

    @property
    def parsed_dir(self) -> Path:
        """MinerU 或本地文本解析结果目录。"""

        return self.workspace_dir / "parsed"

    @property
    def has_model_config(self) -> bool:
        """是否具备真实模型调用所需的最小配置。"""

        return bool(self.volcengine_api_key and self.volcengine_base_url)

    @property
    def has_tavily_config(self) -> bool:
        """是否配置 Tavily。"""

        return bool(self.tavily_api_key)

    @property
    def has_mineru_config(self) -> bool:
        """是否配置 MinerU 精准解析 API。"""

        return bool(self.mineru_api_token)

    def ensure_workspace(self) -> None:
        """创建项目允许使用的三个工作目录。"""

        for directory in (self.inputs_dir, self.outputs_dir, self.parsed_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def configure_langsmith_environment(self) -> None:
        """仅在配置完整时启用 LangSmith；从不记录密钥。"""

        enabled = self.langsmith_tracing and bool(self.langsmith_api_key)
        os.environ["LANGSMITH_TRACING"] = "true" if enabled else "false"
        os.environ["LANGSMITH_ENDPOINT"] = self.langsmith_endpoint
        os.environ["LANGSMITH_PROJECT"] = self.langsmith_project
        if enabled and self.langsmith_api_key:
            os.environ["LANGSMITH_API_KEY"] = self.langsmith_api_key.get_secret_value()

    def capability_summary(self) -> dict[str, Any]:
        """返回不含秘密值的能力可用性摘要。"""

        return {
            "model": self.has_model_config,
            "tavily": self.has_tavily_config,
            "mineru": self.has_mineru_config,
            "langsmith": self.langsmith_tracing and bool(self.langsmith_api_key),
            "heartbeat": self.heartbeat_enabled,
        }
