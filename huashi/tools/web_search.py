"""基于 langchain-tavily 封装有限结果、超时可控的联网搜索工具。"""

from __future__ import annotations

import os
from queue import Empty, Queue
from threading import Thread
from typing import Any, Callable, Protocol

from langchain_core.tools import StructuredTool
from langchain_tavily import TavilySearch

from huashi.models import SearchResult, SourceItem


class SearchToolLike(Protocol):
    """便于注入 Fake Tavily 的最小协议。"""

    def invoke(self, payload: dict[str, Any]) -> Any:
        """执行搜索。"""


class TavilySearchClient:
    """官方 langchain-tavily 的轻量包装，增加硬超时和结果归一化。"""

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 15.0,
        tool_factory: Callable[[int], SearchToolLike] | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("TAVILY_API_KEY 未配置")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self._tool_factory = tool_factory or self._default_tool_factory

    def _default_tool_factory(self, max_results: int) -> SearchToolLike:
        # langchain-tavily 在构造 API wrapper 时读取标准环境变量。
        previous = os.environ.get("TAVILY_API_KEY")
        os.environ["TAVILY_API_KEY"] = self.api_key
        try:
            return TavilySearch(
                max_results=max_results,
                search_depth="basic",
                topic="general",
                include_answer=False,
                include_raw_content=False,
            )
        finally:
            if previous is None:
                os.environ.pop("TAVILY_API_KEY", None)
            else:
                os.environ["TAVILY_API_KEY"] = previous

    def search(self, query: str, max_results: int = 5) -> SearchResult:
        """执行一次搜索；调用方可由 ToolRetryMiddleware 进行有限重试。"""

        cleaned = query.strip()
        if not cleaned:
            raise ValueError("搜索关键词不能为空")
        bounded = max(1, min(int(max_results), 5))
        native_tool = self._tool_factory(bounded)
        result_queue: Queue[tuple[bool, Any]] = Queue(maxsize=1)

        def _run() -> None:
            try:
                result_queue.put((True, native_tool.invoke({"query": cleaned})))
            except Exception as exc:  # 将原异常送回调用线程，交由中间件判断是否重试。
                result_queue.put((False, exc))

        Thread(target=_run, name="tavily-search", daemon=True).start()
        try:
            ok, value = result_queue.get(timeout=self.timeout_seconds)
        except Empty as exc:
            raise TimeoutError("Tavily 搜索超时") from exc
        if not ok:
            raise value
        raw = value

        if isinstance(raw, str):
            raise RuntimeError("Tavily 返回了非结构化结果")
        entries = (raw or {}).get("results") or []
        sources = [
            SourceItem(
                title=str(item.get("title") or "未命名来源")[:300],
                url=str(item.get("url")) if item.get("url") else None,
                summary=str(item.get("content") or "")[:3000] or None,
            )
            for item in entries[:bounded]
        ]
        return SearchResult(success=True, query=cleaned, results=sources)


def search_web(
    query: str,
    max_results: int = 5,
    *,
    client: TavilySearchClient | None = None,
) -> SearchResult:
    """直接调用版本；失败时返回明确错误且不伪造结果。"""

    if client is None:
        return SearchResult(
            success=False,
            query=query,
            error_message="Tavily 客户端未配置",
        )
    try:
        return client.search(query, max_results)
    except Exception as exc:  # 直接接口需要稳定返回模型
        return SearchResult(
            success=False,
            query=query,
            error_message=f"联网搜索失败：{str(exc)[:300]}",
        )


def build_search_tool(client: TavilySearchClient) -> StructuredTool:
    """构建让异常向 ToolRetryMiddleware 传播的 Agent 工具。"""

    def _search(query: str, max_results: int = 5) -> dict[str, object]:
        """搜索需要最新资料、网页来源或当前信息的问题。"""

        return client.search(query, max_results).model_dump(mode="json")

    return StructuredTool.from_function(
        func=_search,
        name="search_web",
        description=(
            "使用 Tavily 搜索最新或需要来源的化学与实验资料；"
            "基础稳定知识通常无需调用。单次最多 5 条。"
        ),
    )
