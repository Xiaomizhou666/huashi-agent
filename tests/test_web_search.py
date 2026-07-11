"""测试 Tavily 结果归一化、失败和硬超时，不访问外网。"""

import time

from huashi.tools.web_search import TavilySearchClient, search_web


class FakeSearchTool:
    def __init__(self, payload=None, delay: float = 0.0, failure: Exception | None = None):
        self.payload = payload or {"results": []}
        self.delay = delay
        self.failure = failure

    def invoke(self, payload):
        if self.delay:
            time.sleep(self.delay)
        if self.failure:
            raise self.failure
        return self.payload


def test_search_normalizes_and_limits_results() -> None:
    payload = {
        "results": [
            {"title": f"R{i}", "url": f"https://e/{i}", "content": "摘要"}
            for i in range(8)
        ]
    }
    client = TavilySearchClient(
        "fake-key",
        tool_factory=lambda max_results: FakeSearchTool(payload),
    )
    result = client.search("化学教育", 5)
    assert result.success
    assert len(result.results) == 5


def test_search_failure_does_not_fabricate() -> None:
    client = TavilySearchClient(
        "fake-key",
        tool_factory=lambda max_results: FakeSearchTool(
            failure=ConnectionError("offline")
        ),
    )
    result = search_web("query", client=client)
    assert not result.success
    assert result.results == []


def test_search_timeout(settings) -> None:
    client = TavilySearchClient(
        "fake-key",
        timeout_seconds=0.02,
        tool_factory=lambda max_results: FakeSearchTool(delay=0.1),
    )
    result = search_web("query", client=client)
    assert not result.success
    assert "超时" in (result.error_message or "")
