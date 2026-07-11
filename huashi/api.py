"""定义轻量 FastAPI 路由，并把业务处理委托给 HuashiService。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from huashi.attachments import AttachmentError, PendingAttachment
from huashi.config import HuashiSettings
from huashi.heartbeat import HeartbeatService
from huashi.models import (
    AssistantResponse,
    AttachmentDeleteResponse,
    AttachmentListResponse,
    ChatRequest,
    DocumentParseResult,
    DocumentUploadResponse,
    FileWriteResult,
    HealthResponse,
    HeartbeatResponse,
    ResetRequest,
    ResetResponse,
    StreamEvent,
    WriteFileRequest,
)
from huashi.service import HuashiService

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_FRONTEND_ROOT = _PROJECT_ROOT / "frontend"
_ALLOWED_UPLOAD_EXTENSIONS = {
    ".txt",
    ".md",
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".jp2",
    ".webp",
    ".gif",
    ".bmp",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
}
_SAFE_UPLOAD_NAME = re.compile(r"^[\w\-一-鿿（）() .]+$")


def _ndjson(event: object) -> bytes:
    """将 Pydantic 流事件编码为一行 UTF-8 NDJSON。"""

    payload = event.model_dump(mode="json")  # type: ignore[attr-defined]
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def _safe_upload_name(filename: str) -> str:
    """清理上传文件名并拒绝路径穿越、隐藏文件和非法扩展名。"""

    cleaned = filename.strip()
    path = Path(cleaned)
    if not cleaned or len(cleaned) > 220:
        raise ValueError("文件名为空或过长")
    if path.is_absolute() or path.name != cleaned or cleaned.startswith("."):
        raise ValueError("文件名不能包含目录、绝对路径或隐藏文件")
    if not _SAFE_UPLOAD_NAME.fullmatch(cleaned) or any(ord(ch) < 32 for ch in cleaned):
        raise ValueError("文件名包含不安全字符")
    if path.suffix.lower() not in _ALLOWED_UPLOAD_EXTENSIONS:
        raise ValueError("不支持该文件类型")
    return cleaned


async def _save_upload(upload: UploadFile, settings: HuashiSettings) -> tuple[str, str]:
    """分块保存上传文件，限制目录、类型和最大字节数。"""

    safe_name = _safe_upload_name(upload.filename or "")
    root = settings.inputs_dir.resolve()
    target = (settings.inputs_dir / safe_name).resolve()
    if root not in target.parents:
        raise ValueError("上传路径超出允许目录")
    if target.exists():
        target = target.with_name(f"{target.stem}-{uuid4().hex[:8]}{target.suffix}")
    max_bytes = settings.max_file_size_mb * 1024 * 1024
    total = 0
    try:
        with target.open("xb") as output:
            while chunk := await upload.read(1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"文件超过 {settings.max_file_size_mb} MB 限制")
                output.write(chunk)
        if total == 0:
            raise ValueError("文件为空")
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()
    return target.name, target.relative_to(settings.workspace_dir.resolve()).as_posix()


def create_app(
    *,
    service: HuashiService | None = None,
    settings: HuashiSettings | None = None,
    heartbeat_service: HeartbeatService | None = None,
) -> FastAPI:
    """创建可测试的 FastAPI 应用实例。"""

    resolved_settings = settings or (service.settings if service else HuashiSettings())
    resolved_service = service or HuashiService(resolved_settings)
    resolved_heartbeat = heartbeat_service or HeartbeatService(resolved_settings)
    resolved_settings.ensure_workspace()

    app = FastAPI(
        title="化实 API",
        description="化学实验学习智能助手的轻量 Web 接口",
        version="1.2.0",
        docs_url="/api/docs",
        redoc_url=None,
    )
    app.state.service = resolved_service
    app.state.settings = resolved_settings
    app.state.heartbeat = resolved_heartbeat
    app.mount(
        "/static",
        StaticFiles(directory=_FRONTEND_ROOT / "static"),
        name="static",
    )
    templates = Jinja2Templates(directory=_FRONTEND_ROOT / "templates")

    @app.exception_handler(Exception)
    async def friendly_exception_handler(_: Request, exc: Exception) -> JSONResponse:
        """隐藏异常堆栈，仅在调试模式返回脱敏类型信息。"""

        content: dict[str, str] = {"detail": "请求处理失败，请稍后重试。"}
        if resolved_settings.debug:
            content["debug"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        return JSONResponse(status_code=500, content=content)

    @app.get("/", include_in_schema=False)
    async def index(request: Request):
        """渲染本地静态资源驱动的主页面。"""

        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "max_file_size_mb": resolved_settings.max_file_size_mb,
                "max_attachments": resolved_settings.max_attachments_per_message,
                "heartbeat_enabled": resolved_settings.heartbeat_enabled,
                "heartbeat_interval_seconds": resolved_settings.heartbeat_interval_seconds,
            },
        )

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        """返回服务状态与不含秘密值的能力开关。"""

        return HealthResponse(capabilities=resolved_settings.capability_summary())

    @app.get(
        "/api/heartbeat",
        response_model=HeartbeatResponse,
        responses={204: {"description": "Heartbeat disabled"}},
    )
    async def heartbeat(
        session_started_at_ms: Annotated[int, Query(ge=0)],
        sequence: Annotated[int, Query(ge=0)] = 0,
    ) -> HeartbeatResponse | Response:
        """返回一次无模型、无工具、无记忆写入的轻量 Web 心跳。"""

        try:
            result = resolved_heartbeat.generate(
                session_started_at_ms=session_started_at_ms,
                sequence=sequence,
            )
        except Exception:
            return JSONResponse(
                status_code=503,
                content={"detail": "心跳暂时不可用，不影响正常对话。"},
            )
        if result is None:
            return Response(status_code=204)
        return result

    @app.post("/api/chat", response_model=AssistantResponse)
    async def chat(payload: ChatRequest) -> AssistantResponse:
        """提供非流式聊天接口，供测试和其他客户端使用。"""

        return resolved_service.chat(
            payload.message,
            user_id=payload.user_id,
            thread_id=payload.thread_id,
            attachment_ids=payload.attachment_ids,
        )

    @app.post("/api/chat/stream")
    async def chat_stream(payload: ChatRequest) -> StreamingResponse:
        """以 NDJSON 流发送文本、工具状态与最终结构化结果。"""

        def iterator():
            for event in resolved_service.chat_stream(
                payload.message,
                user_id=payload.user_id,
                thread_id=payload.thread_id,
                attachment_ids=payload.attachment_ids,
            ):
                yield _ndjson(event)

        return StreamingResponse(
            iterator(),
            media_type="application/x-ndjson; charset=utf-8",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/reset", response_model=ResetResponse)
    async def reset(payload: ResetRequest) -> ResetResponse:
        """创建新 thread_id；长期记忆仍按 user_id 保留。"""

        return ResetResponse(
            user_id=payload.user_id,
            thread_id=resolved_service.reset_session(
                payload.user_id, payload.thread_id
            ),
        )

    @app.post("/api/chat/attachments")
    async def chat_attachments(
        user_id: Annotated[str, Form(min_length=1, max_length=120, pattern=r"^[\w.@+-]+$")],
        thread_id: Annotated[str, Form(min_length=1, max_length=160, pattern=r"^[\w-]+$")],
        files: Annotated[list[UploadFile], File(...)],
    ) -> StreamingResponse:
        """上传并解析 1～3 个聊天附件，以 NDJSON 返回阶段事件。"""

        if len(files) > resolved_settings.max_attachments_per_message:
            def too_many():
                yield _ndjson(StreamEvent(event="start", data={}))
                yield _ndjson(
                    StreamEvent(
                        event="error",
                        data={
                            "message": (
                                "单次最多上传 "
                                f"{resolved_settings.max_attachments_per_message} 个文件"
                            )
                        },
                    )
                )
                yield _ndjson(StreamEvent(event="done", data={}))

            return StreamingResponse(
                too_many(), media_type="application/x-ndjson; charset=utf-8"
            )

        pending: list[PendingAttachment] = []
        max_bytes = resolved_settings.max_file_size_mb * 1024 * 1024
        for upload in files:
            data = await upload.read(max_bytes + 1)
            pending.append(
                PendingAttachment(
                    filename=upload.filename or "",
                    content_type=upload.content_type or "application/octet-stream",
                    content=data,
                )
            )
            await upload.close()

        def iterator():
            results = []
            yield _ndjson(
                StreamEvent(
                    event="start",
                    data={"user_id": user_id, "thread_id": thread_id},
                )
            )
            for item in pending:
                yield _ndjson(
                    StreamEvent(
                        event="upload_start", data={"filename": item.filename}
                    )
                )
                try:
                    stored = resolved_service.create_attachment(
                        item, user_id=user_id, thread_id=thread_id
                    )
                    yield _ndjson(
                        StreamEvent(
                            event="upload_end",
                            data=stored.model_dump(mode="json"),
                        )
                    )
                    if stored.reused and stored.parse_status == "parsed":
                        results.append(stored)
                        yield _ndjson(
                            StreamEvent(
                                event="parse_end",
                                data={
                                    **stored.model_dump(mode="json"),
                                    "success": True,
                                },
                            )
                        )
                        continue

                    yield _ndjson(
                        StreamEvent(
                            event="parse_start",
                            data={
                                "attachment_id": stored.attachment_id,
                                "filename": stored.filename,
                            },
                        )
                    )
                    yield _ndjson(
                        StreamEvent(
                            event="parse_progress",
                            data={
                                "attachment_id": stored.attachment_id,
                                "filename": stored.filename,
                                "message": "正在提取可用于问答的文本",
                            },
                        )
                    )
                    yield _ndjson(
                        StreamEvent(
                            event="tool_start",
                            data={"name": "parse_local_document"},
                        )
                    )
                    parsed = resolved_service.parse_attachment(
                        stored.attachment_id,
                        user_id=user_id,
                        thread_id=thread_id,
                    )
                    success = parsed.parse_status == "parsed"
                    yield _ndjson(
                        StreamEvent(
                            event="tool_end",
                            data={
                                "name": "parse_local_document",
                                "success": success,
                            },
                        )
                    )
                    yield _ndjson(
                        StreamEvent(
                            event="parse_end",
                            data={
                                **parsed.model_dump(mode="json"),
                                "success": success,
                            },
                        )
                    )
                    results.append(parsed)
                except AttachmentError as exc:
                    message = str(exc)[:500]
                    yield _ndjson(
                        StreamEvent(
                            event="error",
                            data={"filename": item.filename, "message": message},
                        )
                    )
                except Exception as exc:
                    message = "附件处理失败，请检查文件或稍后重试。"
                    if resolved_settings.debug:
                        message += f"（{type(exc).__name__}）"
                    yield _ndjson(
                        StreamEvent(
                            event="error",
                            data={"filename": item.filename, "message": message},
                        )
                    )
            yield _ndjson(
                StreamEvent(
                    event="result",
                    data={
                        "success": any(
                            result.parse_status == "parsed" for result in results
                        ),
                        "attachments": [
                            result.model_dump(mode="json") for result in results
                        ],
                    },
                )
            )
            yield _ndjson(StreamEvent(event="done", data={}))

        return StreamingResponse(
            iterator(),
            media_type="application/x-ndjson; charset=utf-8",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/chat/attachments", response_model=AttachmentListResponse)
    async def list_chat_attachments(
        user_id: Annotated[str, Query(min_length=1, max_length=120, pattern=r"^[\w.@+-]+$")],
        thread_id: Annotated[str, Query(min_length=1, max_length=160, pattern=r"^[\w-]+$")],
    ) -> AttachmentListResponse:
        """返回当前线程仍可用于连续追问的附件。"""

        return AttachmentListResponse(
            user_id=user_id,
            thread_id=thread_id,
            attachments=resolved_service.list_attachments(
                user_id=user_id, thread_id=thread_id
            ),
        )

    @app.delete(
        "/api/chat/attachments/{attachment_id}",
        response_model=AttachmentDeleteResponse,
    )
    async def delete_chat_attachment(
        attachment_id: str,
        user_id: Annotated[str, Query(min_length=1, max_length=120, pattern=r"^[\w.@+-]+$")],
        thread_id: Annotated[str, Query(min_length=1, max_length=160, pattern=r"^[\w-]+$")],
    ) -> AttachmentDeleteResponse:
        """删除当前线程附件并停止在后续问题中注入其上下文。"""

        try:
            resolved_service.delete_attachment(
                attachment_id, user_id=user_id, thread_id=thread_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return AttachmentDeleteResponse(
            success=True,
            attachment_id=attachment_id,
            message="附件已从当前会话移除",
        )

    @app.post("/api/write-file", response_model=FileWriteResult)
    async def write_file(payload: WriteFileRequest) -> FileWriteResult:
        """安全写入学习笔记或实验报告草稿。"""

        return resolved_service.write_file(
            payload.filename,
            payload.content,
            payload.file_format,
            payload.overwrite,
        )

    @app.post("/api/read-file", response_model=DocumentUploadResponse)
    async def read_file(
        user_id: Annotated[str, Form(min_length=1, max_length=120)],
        thread_id: Annotated[str, Form(min_length=1, max_length=160)],
        file: Annotated[UploadFile, File(...)],
    ) -> DocumentUploadResponse:
        """上传到 inputs 安全目录，并复用现有 DocumentParser/MinerU。"""

        try:
            saved_name, relative_path = await _save_upload(file, resolved_settings)
            result = resolved_service.parse_document(saved_name)
            return DocumentUploadResponse(
                success=result.success,
                user_id=user_id,
                thread_id=thread_id,
                uploaded_path=relative_path,
                parse_result=result,
            )
        except ValueError as exc:
            result = DocumentParseResult(
                success=False,
                status="failed",
                error_message=str(exc),
            )
            return DocumentUploadResponse(
                success=False,
                user_id=user_id,
                thread_id=thread_id,
                parse_result=result,
            )

    return app
