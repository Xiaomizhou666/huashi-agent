"""提供 CLI、Web API 与未来客户端共用的稳定业务接口。"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any, Literal

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage
from pydantic import ValidationError

from huashi.agent import HuashiAgentRuntime, build_agent_runtime
from huashi.attachments import (
    AttachmentAccessError,
    AttachmentManager,
    PendingAttachment,
)
from huashi.document_context import DocumentContextBuilder
from huashi.config import HuashiSettings
from huashi.memory import HuashiMemory
from huashi.middleware import assess_chem_risk
from huashi.models import (
    AssistantResponse,
    AttachmentResult,
    DocumentParseResult,
    FileWriteResult,
    RiskAssessment,
    StreamEvent,
)
from huashi.tools.file_writer import write_learning_file

_MEMORY_INTENT = re.compile(
    r"(?:请|帮我)?记住|以后(?:都|请)|跨会话|下次也|保存(?:这个|我的)偏好",
    re.IGNORECASE,
)
_ANSWER_FIELD = re.compile(r'"answer"\s*:\s*"')


def _chunk_text(text: str, size: int = 12) -> Iterator[str]:
    """将非流式兜底文本切成适合浏览器逐段渲染的小块。"""

    for start in range(0, len(text), size):
        yield text[start : start + size]


def _message_text(message: BaseMessage) -> str:
    """从 LangChain 消息中提取可展示文本，不暴露工具参数。"""

    content = message.content
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") in {
            "text",
            "output_text",
        }:
            parts.append(str(block.get("text") or ""))
    return "".join(parts)


def _find_structured_response(value: Any) -> AssistantResponse | None:
    """从 LangGraph v2 更新或最终状态中递归查找结构化响应。"""

    if isinstance(value, AssistantResponse):
        return value
    if isinstance(value, dict):
        if "structured_response" in value:
            try:
                return AssistantResponse.model_validate(value["structured_response"])
            except ValidationError:
                pass
        for child in value.values():
            found = _find_structured_response(child)
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for child in value:
            found = _find_structured_response(child)
            if found is not None:
                return found
    return None


def _stream_message(part: dict[str, Any]) -> BaseMessage | None:
    """兼容 LangGraph v2 messages 数据的元组和直接消息形式。"""

    data = part.get("data")
    if isinstance(data, BaseMessage):
        return data
    if isinstance(data, tuple) and data and isinstance(data[0], BaseMessage):
        return data[0]
    if isinstance(data, list) and data and isinstance(data[0], BaseMessage):
        return data[0]
    return None


def _tool_names(message: BaseMessage) -> list[str]:
    """提取模型发起的普通工具名，排除结构化输出 schema 工具。"""

    names: list[str] = []
    for call in getattr(message, "tool_calls", None) or []:
        name = str(call.get("name") or "")
        if name and name != "AssistantResponse":
            names.append(name)
    for call in getattr(message, "tool_call_chunks", None) or []:
        name = str(call.get("name") or "")
        if name and name != "AssistantResponse":
            names.append(name)
    return names


class _StructuredAnswerAccumulator:
    """从流式 ToolStrategy 参数中增量提取 ``answer`` 字符串。"""

    def __init__(self) -> None:
        self._buffers: dict[int, str] = {}
        self._names: dict[int, str] = {}
        self._last_answer = ""

    @staticmethod
    def _decode_answer_prefix(payload: str) -> str | None:
        match = _ANSWER_FIELD.search(payload)
        if match is None:
            return None
        raw_start = match.end()
        escaped = False
        closing: int | None = None
        for index, char in enumerate(payload[raw_start:], start=raw_start):
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                closing = index
                break
        raw = payload[raw_start:closing]
        if escaped:
            return None
        try:
            return json.loads(f'"{raw}"')
        except json.JSONDecodeError:
            return None

    def feed(self, message: BaseMessage) -> str:
        """追加一个消息块并返回新出现的 answer 文本。"""

        complete_calls = getattr(message, "tool_calls", None) or []
        for call in complete_calls:
            if call.get("name") == "AssistantResponse":
                args = call.get("args") or {}
                if isinstance(args, dict) and isinstance(args.get("answer"), str):
                    answer = args["answer"]
                    delta = answer[len(self._last_answer) :] if answer.startswith(
                        self._last_answer
                    ) else answer
                    self._last_answer = answer
                    return delta

        latest: str | None = None
        for call in getattr(message, "tool_call_chunks", None) or []:
            index = int(call.get("index") or 0)
            name = str(call.get("name") or "")
            if name:
                self._names[index] = name
            if self._names.get(index) != "AssistantResponse":
                continue
            args = call.get("args")
            if isinstance(args, str):
                self._buffers[index] = self._buffers.get(index, "") + args
                latest = self._decode_answer_prefix(self._buffers[index]) or latest
        if latest is None:
            return ""
        delta = latest[len(self._last_answer) :] if latest.startswith(
            self._last_answer
        ) else latest
        self._last_answer = latest
        return delta


class HuashiService:
    """“化实”对外服务层，统一 Agent、CLI、Web 与文件对话逻辑。"""

    DEFAULT_ATTACHMENT_PROMPT = (
        "请告诉我你希望针对该文件进行总结、提取、问答还是其他处理。"
    )

    def __init__(
        self,
        settings: HuashiSettings | None = None,
        *,
        memory: HuashiMemory | None = None,
        runtime: HuashiAgentRuntime | None = None,
        model: Any | None = None,
        tavily_client: Any | None = None,
        mineru_client: Any | None = None,
        attachment_manager: AttachmentManager | None = None,
    ) -> None:
        self.settings = settings or HuashiSettings()
        self.settings.ensure_workspace()
        self.memory = memory or HuashiMemory()
        self.runtime = runtime
        self.initialization_error: str | None = None
        if self.runtime is None and (model is not None or self.settings.has_model_config):
            try:
                self.runtime = build_agent_runtime(
                    self.settings,
                    self.memory,
                    model=model,
                    tavily_client=tavily_client,
                    mineru_client=mineru_client,
                )
            except Exception as exc:
                self.initialization_error = f"Agent 初始化失败：{str(exc)[:500]}"
        self.attachments = attachment_manager or AttachmentManager(self.settings)
        self.context_builder = DocumentContextBuilder(
            max_total_chars=self.settings.max_attachment_context_chars,
            max_file_chars=self.settings.max_attachment_file_chars,
        )
        self._standalone_parser = None
        self._mineru_client = mineru_client

    @staticmethod
    def new_thread_id() -> str:
        """创建新的 thread_id。"""

        return HuashiMemory.new_thread_id()

    def reset_session(self, user_id: str, previous_thread_id: str | None = None) -> str:
        """创建新线程，并在显式提供旧线程时清理其临时附件。"""

        if previous_thread_id:
            self.attachments.clear_thread(user_id=user_id, thread_id=previous_thread_id)
        return self.new_thread_id()

    @staticmethod
    def _safety_response(
        assessment: RiskAssessment,
        attachments: list[AttachmentResult] | None = None,
    ) -> AssistantResponse:
        alternatives = "；".join(assessment.safe_alternatives)
        return AssistantResponse(
            success=True,
            intent="safety_refusal",
            answer=(
                "这个请求涉及可直接执行的高风险化学操作，我不能提供配方、条件或步骤。"
                f"可以改为讨论非操作性的原理与风险，或采用这些安全替代：{alternatives}。"
            ),
            tools_used=[],
            attachments=attachments or [],
            safety_level="high",
            safety_notes=assessment.reasons
            + ["本回答不能替代教师、实验室负责人或安全数据表。"],
        )

    def _unavailable_reason(self) -> str:
        return self.initialization_error or (
            "缺少 VOLCENGINE_API_KEY 或 VOLCENGINE_BASE_URL；"
            "文件读写命令仍可使用。"
        )

    def _document_parser(self):
        """复用 Agent 的 DocumentParser；无 Agent 时保留文本解析能力。"""

        if self.runtime is not None:
            return self.runtime.document_parser
        if self._standalone_parser is None:
            from huashi.tools.file_reader import DocumentParser

            self._standalone_parser = DocumentParser(
                self.settings.inputs_dir,
                self.settings.parsed_dir,
                self.settings.workspace_dir,
                max_file_size_mb=self.settings.max_file_size_mb,
                mineru_client=self._mineru_client,
            )
        return self._standalone_parser

    def create_attachment(
        self,
        pending: PendingAttachment,
        *,
        user_id: str,
        thread_id: str,
    ) -> AttachmentResult:
        """校验并保存一个待解析的聊天附件。"""

        return self.attachments.store(
            pending, user_id=user_id, thread_id=thread_id
        )

    def parse_attachment(
        self,
        attachment_id: str,
        *,
        user_id: str,
        thread_id: str,
    ) -> AttachmentResult:
        """通过现有 DocumentParser/MinerU 解析聊天附件。"""

        return self.attachments.parse(
            attachment_id,
            user_id=user_id,
            thread_id=thread_id,
            parser=self._document_parser(),
        )

    def list_attachments(self, *, user_id: str, thread_id: str) -> list[AttachmentResult]:
        """列出当前会话附件。"""

        return self.attachments.list_public(user_id=user_id, thread_id=thread_id)

    def delete_attachment(
        self, attachment_id: str, *, user_id: str, thread_id: str
    ) -> bool:
        """删除当前会话中的一个附件。"""

        return self.attachments.delete(
            attachment_id, user_id=user_id, thread_id=thread_id
        )

    def _prepare_agent_message(
        self,
        message: str,
        *,
        user_id: str,
        thread_id: str,
        attachment_ids: list[str] | None,
    ) -> tuple[str, str, list[AttachmentResult]]:
        """解析线程附件归属并构建有字符预算的文件上下文。"""

        records = self.attachments.resolve(
            user_id=user_id,
            thread_id=thread_id,
            attachment_ids=attachment_ids,
        )
        if not attachment_ids and len(records) > 20:
            records = records[-20:]
        cleaned = message.strip()
        parsed_records = [record for record in records if record.parse_status == "parsed"]
        if not cleaned:
            if not parsed_records:
                raise ValueError("请输入问题，或先上传并成功解析附件。")
            cleaned = self.DEFAULT_ATTACHMENT_PROMPT

        enhanced, used = self.context_builder.build(cleaned, records)
        if records and not used:
            failed = "、".join(record.filename for record in records)
            enhanced = (
                f"【用户问题】\n{cleaned}\n\n"
                f"【附件状态】\n{failed} 尚未成功解析，不能作为回答依据。"
                "请明确说明无法读取这些附件，不得假装已读取。"
            )
        return cleaned, enhanced, [record.public() for record in records]

    @staticmethod
    def _with_attachments(
        response: AssistantResponse, attachments: list[AttachmentResult]
    ) -> AssistantResponse:
        """将权威线程附件状态合并进 Agent 结构化响应。"""

        return response.model_copy(update={"attachments": attachments})

    def chat(
        self,
        message: str,
        user_id: str,
        thread_id: str,
        attachment_ids: list[str] | None = None,
    ) -> AssistantResponse:
        """执行一次对话；当前线程附件会经长度控制后交给同一 Agent。"""

        try:
            cleaned, agent_message, attachment_results = self._prepare_agent_message(
                message,
                user_id=user_id,
                thread_id=thread_id,
                attachment_ids=attachment_ids,
            )
        except (AttachmentAccessError, ValueError) as exc:
            return AssistantResponse(
                success=False,
                intent="error",
                answer="无法使用本次附件或消息。",
                safety_level="low",
                error_message=str(exc),
            )

        assessment = assess_chem_risk(cleaned)
        if assessment.level == "high":
            return self._safety_response(assessment, attachment_results)

        if self.runtime is None:
            return AssistantResponse(
                success=False,
                intent="error",
                answer="当前无法调用对话模型。",
                attachments=attachment_results,
                safety_level=assessment.level,
                safety_notes=assessment.reasons,
                error_message=self._unavailable_reason(),
            )

        try:
            state = self.runtime.invoke(
                agent_message,
                user_id=user_id,
                thread_id=thread_id,
                allow_memory_write=bool(_MEMORY_INTENT.search(cleaned)),
                debug=self.settings.debug,
            )
            structured = state.get("structured_response")
            if structured is None:
                raise ValueError("Agent 未返回 structured_response")
            response = AssistantResponse.model_validate(structured)
            return self._with_attachments(response, attachment_results)
        except ValidationError as exc:
            return AssistantResponse(
                success=False,
                intent="error",
                answer="模型返回的结构化数据未通过校验。",
                attachments=attachment_results,
                safety_level=assessment.level,
                safety_notes=assessment.reasons,
                error_message=f"Pydantic 校验失败：{str(exc)[:800]}",
            )
        except Exception as exc:
            error_message = "模型或工具调用失败，请检查配置或稍后重试。"
            if self.settings.debug:
                error_message = f"{type(exc).__name__}: {str(exc)[:500]}"
            return AssistantResponse(
                success=False,
                intent="error",
                answer="本次处理失败，请检查配置或稍后重试。",
                attachments=attachment_results,
                safety_level=assessment.level,
                safety_notes=assessment.reasons,
                error_message=error_message,
            )

    def chat_stream(
        self,
        message: str,
        user_id: str,
        thread_id: str,
        attachment_ids: list[str] | None = None,
    ) -> Iterator[StreamEvent]:
        """流式执行同一 Agent，并在末尾返回附件增强的结构化结果。"""

        yield StreamEvent(
            event="start",
            data={"user_id": user_id, "thread_id": thread_id},
        )
        try:
            cleaned, agent_message, attachment_results = self._prepare_agent_message(
                message,
                user_id=user_id,
                thread_id=thread_id,
                attachment_ids=attachment_ids,
            )
        except (AttachmentAccessError, ValueError) as exc:
            yield StreamEvent(event="error", data={"message": str(exc)})
            yield StreamEvent(event="done", data={})
            return

        assessment = assess_chem_risk(cleaned)
        if assessment.level == "high":
            refusal = self._safety_response(assessment, attachment_results)
            for chunk in _chunk_text(refusal.answer):
                yield StreamEvent(event="token", data={"text": chunk})
            yield StreamEvent(event="result", data=refusal.model_dump(mode="json"))
            yield StreamEvent(event="done", data={})
            return

        if self.runtime is None:
            yield StreamEvent(
                event="error",
                data={
                    "message": "当前无法调用对话模型。",
                    "detail": self._unavailable_reason(),
                },
            )
            yield StreamEvent(event="done", data={})
            return

        emitted_text = ""
        started_tools: set[str] = set()
        completed_tools: set[str] = set()
        final_response: AssistantResponse | None = None
        structured_accumulator = _StructuredAnswerAccumulator()
        try:
            for part in self.runtime.stream(
                agent_message,
                user_id=user_id,
                thread_id=thread_id,
                allow_memory_write=bool(_MEMORY_INTENT.search(cleaned)),
                debug=self.settings.debug,
            ):
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "messages":
                    message_chunk = _stream_message(part)
                    if message_chunk is None:
                        continue
                    for tool_name in _tool_names(message_chunk):
                        if tool_name not in started_tools:
                            started_tools.add(tool_name)
                            yield StreamEvent(event="tool_start", data={"name": tool_name})
                    if isinstance(message_chunk, ToolMessage):
                        tool_name = str(message_chunk.name or "tool")
                        if tool_name == "AssistantResponse":
                            continue
                        if tool_name not in started_tools:
                            started_tools.add(tool_name)
                            yield StreamEvent(event="tool_start", data={"name": tool_name})
                        if tool_name not in completed_tools:
                            completed_tools.add(tool_name)
                            yield StreamEvent(
                                event="tool_end",
                                data={"name": tool_name, "success": True},
                            )
                        continue
                    text = _message_text(message_chunk)
                    if text and isinstance(message_chunk, (AIMessage, AIMessageChunk)):
                        emitted_text += text
                        yield StreamEvent(event="token", data={"text": text})
                    structured_delta = structured_accumulator.feed(message_chunk)
                    if structured_delta:
                        emitted_text += structured_delta
                        yield StreamEvent(event="token", data={"text": structured_delta})
                elif part.get("type") == "updates":
                    final_response = (
                        _find_structured_response(part.get("data")) or final_response
                    )

            if final_response is None:
                final_response = _find_structured_response(
                    self.runtime.current_state(thread_id)
                )
            if final_response is None:
                raise ValueError("Agent 流结束后未返回 structured_response")
            final_response = self._with_attachments(final_response, attachment_results)

            for tool_name in final_response.tools_used:
                if tool_name not in started_tools:
                    started_tools.add(tool_name)
                    yield StreamEvent(event="tool_start", data={"name": tool_name})
                if tool_name not in completed_tools:
                    completed_tools.add(tool_name)
                    yield StreamEvent(
                        event="tool_end",
                        data={"name": tool_name, "success": True},
                    )

            if not emitted_text:
                for chunk in _chunk_text(final_response.answer):
                    emitted_text += chunk
                    yield StreamEvent(event="token", data={"text": chunk})
            elif final_response.answer.startswith(emitted_text):
                for chunk in _chunk_text(final_response.answer[len(emitted_text) :]):
                    yield StreamEvent(event="token", data={"text": chunk})

            yield StreamEvent(event="result", data=final_response.model_dump(mode="json"))
        except ValidationError:
            yield StreamEvent(event="error", data={"message": "模型返回的数据格式不正确。"})
        except Exception as exc:
            detail = (
                f"{type(exc).__name__}: {str(exc)[:300]}"
                if self.settings.debug
                else None
            )
            data = {"message": "请求处理失败，请检查配置或稍后重试。"}
            if detail:
                data["detail"] = detail
            yield StreamEvent(event="error", data=data)
        finally:
            yield StreamEvent(event="done", data={})

    def chat_json(
        self,
        message: str,
        user_id: str,
        thread_id: str,
        attachment_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """供前端或其他客户端获取 JSON 可序列化结果。"""

        return self.chat(
            message, user_id, thread_id, attachment_ids
        ).model_dump(mode="json")

    def write_file(
        self,
        filename: str,
        content: str,
        file_format: Literal["md", "txt", "json"] = "md",
        overwrite: bool = False,
    ) -> FileWriteResult:
        """不经过模型直接安全写文件。"""

        return write_learning_file(
            filename,
            content,
            file_format,
            overwrite,
            outputs_dir=self.settings.outputs_dir,
            workspace_dir=self.settings.workspace_dir,
        )

    def parse_document(self, file_path: str) -> DocumentParseResult:
        """保留原通用解析接口，供 CLI 和兼容客户端使用。"""

        return self._document_parser().parse(file_path)
