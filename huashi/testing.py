"""提供默认测试使用的离线结构化聊天模型。"""

from __future__ import annotations

import re
from typing import Any, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool
from pydantic import Field


class DeterministicStructuredChatModel(BaseChatModel):
    """始终调用 AssistantResponse schema 工具的确定性 Fake 模型。"""

    bound_tools: list[Any] = Field(default_factory=list, exclude=True)

    @property
    def _llm_type(self) -> str:
        return "huashi-deterministic-fake"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Any | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> "DeterministicStructuredChatModel":
        """记录 create_agent 绑定的工具，包括结构化响应工具。"""

        return self.model_copy(update={"bound_tools": list(tools)})

    @staticmethod
    def _human_texts(messages: list[BaseMessage]) -> list[str]:
        return [message.text for message in messages if isinstance(message, HumanMessage)]

    def _answer(self, messages: list[BaseMessage]) -> str:
        human_texts = self._human_texts(messages)
        latest = human_texts[-1] if human_texts else ""
        question_match = re.search(
            r"【用户问题】\s*(.*?)\s*【用户上传文件上下文】",
            latest,
            re.DOTALL,
        )
        question = question_match.group(1).strip() if question_match else latest
        if "本轮关键词是什么" in question:
            for prior in reversed(human_texts[:-1]):
                match = re.search(r"记住本轮关键词[：:]\s*([^\s，。]+)", prior)
                if match:
                    return f"本轮关键词是：{match.group(1)}"
            return "本轮没有记录关键词。"
        if "记住本轮关键词" in question:
            return "已在当前 thread 的短期记忆中保留。"
        if "【用户上传文件上下文】" in latest:
            filenames = re.findall(r"--- 文件 \d+：(.+?) ---", latest)
            body = latest.split("【文件问答规则】", 1)[0]
            if "不存在答案" in question or "文件里没有" in question:
                return f"上传文件中未找到该信息。已检查：{'、'.join(filenames)}。"
            marker = re.search(r"解析内容片段：\s*(.{1,80})", body, re.DOTALL)
            excerpt = " ".join((marker.group(1) if marker else "").split())
            return f"依据文件 {'、'.join(filenames)}：{excerpt}"
        return f"离线测试回答：{question}"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        response_tool = next(
            (
                tool
                for tool in self.bound_tools
                if getattr(tool, "name", None) == "AssistantResponse"
            ),
            None,
        )
        if response_tool is None:
            raise RuntimeError("Fake 模型未找到 AssistantResponse 工具")
        payload = {
            "success": True,
            "intent": "qa",
            "answer": self._answer(messages),
            "tools_used": [],
            "sources": [],
            "generated_files": [],
            "safety_level": "low",
            "safety_notes": [],
            "error_message": None,
        }
        message = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "AssistantResponse",
                    "args": payload,
                    "id": "fake-structured-call",
                    "type": "tool_call",
                }
            ],
        )
        return ChatResult(generations=[ChatGeneration(message=message)])
