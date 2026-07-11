"""定义工具结果、记忆数据和最终结构化响应的 Pydantic 模型。"""

from __future__ import annotations

from datetime import datetime
import re
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SourceItem(BaseModel):
    """联网检索或文档解析中可展示的来源。"""

    title: str = Field(min_length=1, max_length=300)
    url: str | None = Field(default=None, max_length=2048)
    summary: str | None = Field(default=None, max_length=3000)


class GeneratedFile(BaseModel):
    """智能体生成或解析后落盘的文件。"""

    filename: str = Field(min_length=1, max_length=255)
    relative_path: str = Field(min_length=1, max_length=1024)
    file_type: str = Field(min_length=1, max_length=20)


class AttachmentResult(BaseModel):
    """聊天附件的公开状态，不包含服务器绝对路径或完整正文。"""

    attachment_id: str = Field(pattern=r"^att_[0-9a-f]{32}$")
    filename: str = Field(min_length=1, max_length=220)
    file_type: str = Field(min_length=1, max_length=20)
    file_size: int = Field(ge=1)
    parse_status: Literal[
        "waiting_upload",
        "uploading",
        "waiting_parse",
        "parsing",
        "parsed",
        "failed",
    ]
    summary: str | None = Field(default=None, max_length=3000)
    error_message: str | None = Field(default=None, max_length=1000)
    created_at: datetime
    reused: bool = False


class AssistantResponse(BaseModel):
    """Agent 对外返回的稳定结构化结果。"""

    model_config = ConfigDict(extra="forbid")

    success: bool
    intent: Literal[
        "qa",
        "web_search",
        "file_write",
        "file_read",
        "experiment_guidance",
        "safety_refusal",
        "error",
    ]
    answer: str = Field(min_length=1, max_length=20000)
    tools_used: list[str] = Field(default_factory=list, max_length=20)
    sources: list[SourceItem] = Field(default_factory=list, max_length=20)
    generated_files: list[GeneratedFile] = Field(default_factory=list, max_length=20)
    attachments: list[AttachmentResult] = Field(default_factory=list, max_length=20)
    safety_level: Literal["low", "medium", "high"] = "low"
    safety_notes: list[str] = Field(default_factory=list, max_length=20)
    error_message: str | None = Field(default=None, max_length=3000)

    @model_validator(mode="after")
    def validate_consistency(self) -> "AssistantResponse":
        """校验拒绝和错误响应的关键字段一致性。"""

        if self.intent == "safety_refusal" and self.safety_level != "high":
            raise ValueError("安全拒绝必须标记为 high")
        if self.intent == "error" and self.success:
            raise ValueError("error intent 不能标记 success=True")
        if not self.success and not self.error_message and self.intent == "error":
            raise ValueError("错误响应必须包含 error_message")
        return self


class SearchResult(BaseModel):
    """Tavily 工具的标准返回。"""

    success: bool
    query: str
    results: list[SourceItem] = Field(default_factory=list, max_length=5)
    error_message: str | None = None


class FileWriteResult(BaseModel):
    """安全文件写入结果。"""

    success: bool
    relative_path: str | None = None
    file_type: Literal["md", "txt", "json"] | None = None
    error_message: str | None = None


class DocumentParseResult(BaseModel):
    """本地文档读取或 MinerU 解析结果。"""

    success: bool
    status: Literal["done", "failed", "unavailable"]
    title: str | None = None
    summary: str | None = None
    content_excerpt: str | None = Field(default=None, max_length=8000)
    result_path: str | None = None
    error_message: str | None = None


class PreferenceKey(str, Enum):
    """学习版长期记忆允许保存的低敏感偏好字段。"""

    preferred_name = "preferred_name"
    learning_stage = "learning_stage"
    detail_level = "detail_level"
    report_format = "report_format"
    learning_preference = "learning_preference"


class UserPreferences(BaseModel):
    """跨线程可读取的用户学习偏好。"""

    preferred_name: str | None = Field(default=None, max_length=80)
    learning_stage: str | None = Field(default=None, max_length=120)
    detail_level: Literal["简洁", "适中", "详细"] | None = None
    report_format: str | None = Field(default=None, max_length=1000)
    learning_preference: str | None = Field(default=None, max_length=1000)

    @field_validator("preferred_name", "learning_stage", "report_format", "learning_preference")
    @classmethod
    def reject_sensitive_content(cls, value: str | None) -> str | None:
        """拒绝明显像密钥或密码的长期记忆内容。"""

        if value is None:
            return value
        lowered = value.lower()
        blocked = ("api_key", "apikey", "token=", "password", "secret_key", "sk-")
        if any(marker in lowered for marker in blocked):
            raise ValueError("长期记忆不允许保存密钥、Token 或密码")
        return value.strip()


class RiskAssessment(BaseModel):
    """规则型化学安全分类结果。"""

    level: Literal["low", "medium", "high"]
    categories: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    safe_alternatives: list[str] = Field(default_factory=list)


class StreamEvent(BaseModel):
    """Web 流式接口发送的统一事件。"""

    event: Literal[
        "start",
        "upload_start",
        "upload_end",
        "parse_start",
        "parse_progress",
        "parse_end",
        "tool_start",
        "tool_end",
        "token",
        "result",
        "error",
        "done",
    ]
    data: dict[str, Any] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    """普通与流式聊天接口的请求体，可引用当前线程附件。"""

    message: str = Field(default="", max_length=20000)
    user_id: str = Field(min_length=1, max_length=120, pattern=r"^[\w.@+-]+$")
    thread_id: str = Field(min_length=1, max_length=160, pattern=r"^[\w-]+$")
    attachment_ids: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("message", "user_id", "thread_id")
    @classmethod
    def strip_request_text(cls, value: str) -> str:
        """去除请求字段首尾空白。"""

        return value.strip()

    @field_validator("attachment_ids")
    @classmethod
    def validate_attachment_ids(cls, values: list[str]) -> list[str]:
        """拒绝格式异常或重复的附件 ID。"""

        if len(values) != len(set(values)):
            raise ValueError("attachment_ids 不能重复")
        if any(not re.fullmatch(r"att_[0-9a-f]{32}", value) for value in values):
            raise ValueError("attachment_id 格式无效")
        return values


class ResetRequest(BaseModel):
    """新建会话请求，可同时清理旧线程的临时附件。"""

    user_id: str = Field(min_length=1, max_length=120, pattern=r"^[\w.@+-]+$")
    thread_id: str | None = Field(
        default=None, max_length=160, pattern=r"^[\w-]+$"
    )


class ResetResponse(BaseModel):
    """新建会话结果。"""

    user_id: str
    thread_id: str


class WriteFileRequest(BaseModel):
    """前端安全写文件请求。"""

    filename: str = Field(min_length=1, max_length=220)
    content: str = Field(max_length=200000)
    file_format: Literal["md", "txt", "json"] = "md"
    overwrite: bool = False


class HealthResponse(BaseModel):
    """不含秘密值的健康检查响应。"""

    status: Literal["ok"] = "ok"
    name: str = "化实"
    capabilities: dict[str, bool]


class HeartbeatResponse(BaseModel):
    """Web 页面心跳响应，不属于 Agent 结构化输出。"""

    message: str = Field(min_length=1, max_length=200)
    elapsed_seconds: int = Field(ge=0)
    elapsed_minutes: int = Field(ge=0)
    interval_seconds: int = Field(ge=1, le=3600)
    sequence: int = Field(ge=0)


class DocumentUploadResponse(BaseModel):
    """上传并解析文档后的响应。"""

    success: bool
    user_id: str
    thread_id: str
    uploaded_path: str | None = None
    parse_result: DocumentParseResult


class AttachmentDeleteResponse(BaseModel):
    """删除聊天附件后的结果。"""

    success: bool
    attachment_id: str
    message: str


class AttachmentListResponse(BaseModel):
    """当前线程附件列表。"""

    user_id: str
    thread_id: str
    attachments: list[AttachmentResult] = Field(default_factory=list, max_length=50)
