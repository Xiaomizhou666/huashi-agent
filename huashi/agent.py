"""创建 LangChain 1.2 Agent、工具集合、中间件和结构化输出策略。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from huashi.clients.mineru_client import MinerUClient
from huashi.config import HuashiSettings
from huashi.memory import HuashiContext, HuashiMemory
from huashi.middleware import build_middleware
from huashi.models import AssistantResponse
from huashi.prompts import SYSTEM_PROMPT
from huashi.tools.file_reader import DocumentParser, build_document_parser_tool
from huashi.tools.file_writer import build_file_writer_tool
from huashi.tools.memory_tools import build_memory_tools
from huashi.tools.web_search import TavilySearchClient, build_search_tool


def initialize_chat_model(settings: HuashiSettings) -> BaseChatModel:
    """用火山引擎 OpenAI 兼容地址初始化 ChatOpenAI。"""

    if not settings.has_model_config:
        raise ValueError("缺少 VOLCENGINE_API_KEY 或 VOLCENGINE_BASE_URL")
    assert settings.volcengine_api_key is not None
    assert settings.volcengine_base_url is not None
    return ChatOpenAI(
        model=settings.chat_model,
        api_key=settings.volcengine_api_key.get_secret_value(),
        base_url=settings.volcengine_base_url,
        temperature=0.2,
        timeout=settings.model_timeout_seconds,
        max_retries=0,  # 重试由 ModelRetryMiddleware 统一控制。
    )


@dataclass
class HuashiAgentRuntime:
    """保存已编译 Agent 及其工具依赖。"""

    graph: Any
    document_parser: DocumentParser

    @staticmethod
    def _input(message: str) -> dict[str, list[dict[str, str]]]:
        """构造 Agent 输入，避免 CLI 与 Web 各自维护消息列表。"""

        return {"messages": [{"role": "user", "content": message}]}

    @staticmethod
    def _config(thread_id: str) -> dict[str, dict[str, str]]:
        """构造 LangGraph Checkpointer 配置。"""

        return {"configurable": {"thread_id": thread_id}}

    @staticmethod
    def _context(
        user_id: str, allow_memory_write: bool, debug: bool
    ) -> HuashiContext:
        """构造跨工具和中间件共享的运行上下文。"""

        return HuashiContext(
            user_id=user_id,
            allow_memory_write=allow_memory_write,
            debug=debug,
        )

    def invoke(
        self,
        message: str,
        *,
        user_id: str,
        thread_id: str,
        allow_memory_write: bool,
        debug: bool = False,
    ) -> dict[str, Any]:
        """按 thread_id 和 user_id 调用已编译 Agent。"""

        return self.graph.invoke(
            self._input(message),
            config=self._config(thread_id),
            context=self._context(user_id, allow_memory_write, debug),
        )

    def stream(
        self,
        message: str,
        *,
        user_id: str,
        thread_id: str,
        allow_memory_write: bool,
        debug: bool = False,
    ) -> Any:
        """流式执行同一个 Agent，返回 LangGraph v2 消息与状态更新。"""

        return self.graph.stream(
            self._input(message),
            config=self._config(thread_id),
            context=self._context(user_id, allow_memory_write, debug),
            stream_mode=["messages", "updates"],
            version="v2",
        )

    def current_state(self, thread_id: str) -> dict[str, Any]:
        """读取流结束后的线程状态，不触发第二次模型调用。"""

        snapshot = self.graph.get_state(self._config(thread_id))
        return dict(snapshot.values or {})


def build_agent_runtime(
    settings: HuashiSettings,
    memory: HuashiMemory,
    *,
    model: BaseChatModel | None = None,
    tavily_client: TavilySearchClient | None = None,
    mineru_client: MinerUClient | Any | None = None,
) -> HuashiAgentRuntime:
    """构建“化实”单 Agent；只使用 LangChain 1.2 的 create_agent API。"""

    settings.ensure_workspace()
    settings.configure_langsmith_environment()
    selected_model = model or initialize_chat_model(settings)

    if tavily_client is None and settings.tavily_api_key:
        tavily_client = TavilySearchClient(
            settings.tavily_api_key.get_secret_value(),
            timeout_seconds=settings.tavily_timeout_seconds,
        )
    if mineru_client is None and settings.mineru_api_token:
        mineru_client = MinerUClient(
            settings.mineru_api_token.get_secret_value(),
            base_url=settings.mineru_api_base_url,
            poll_interval_seconds=settings.mineru_poll_interval_seconds,
            max_poll_attempts=settings.mineru_max_poll_attempts,
            max_download_mb=max(settings.max_file_size_mb * 5, 50),
        )

    parser = DocumentParser(
        settings.inputs_dir,
        settings.parsed_dir,
        settings.workspace_dir,
        max_file_size_mb=settings.max_file_size_mb,
        mineru_client=mineru_client,
    )
    tools: list[Any] = [
        build_file_writer_tool(settings.outputs_dir, settings.workspace_dir),
        build_document_parser_tool(parser),
        *build_memory_tools(memory),
    ]
    if tavily_client is not None:
        tools.append(build_search_tool(tavily_client))

    availability_note = (
        f"\n当前外部能力：Tavily={'可用' if tavily_client else '未配置'}；"
        f"MinerU={'可用' if mineru_client else '未配置（txt/md 仍可读取）'}。"
    )
    graph = create_agent(
        model=selected_model,
        tools=tools,
        system_prompt=SYSTEM_PROMPT + availability_note,
        middleware=build_middleware(),
        response_format=ToolStrategy(
            AssistantResponse,
            handle_errors="结构化输出校验失败，请严格按 AssistantResponse 字段重试。",
        ),
        checkpointer=memory.checkpointer,
        store=memory.store,
        context_schema=HuashiContext,
        name="huashi",
        debug=settings.debug,
    )
    return HuashiAgentRuntime(graph=graph, document_parser=parser)
