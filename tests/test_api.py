"""验证 FastAPI 页面、流式协议、会话记忆和文件接口。"""

from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessageChunk, ToolMessage

from huashi.api import create_app
from huashi.clients.mineru_client import FakeMinerUClient
from huashi.models import AssistantResponse
from huashi.service import HuashiService
from huashi.testing import DeterministicStructuredChatModel


def _client(settings, **service_kwargs) -> tuple[TestClient, HuashiService]:
    service = HuashiService(
        settings,
        model=DeterministicStructuredChatModel(),
        **service_kwargs,
    )
    return TestClient(create_app(service=service)), service


def _events(response) -> list[dict]:
    return [json.loads(line) for line in response.iter_lines() if line]


def test_homepage_and_local_logo(settings) -> None:
    client, _ = _client(settings)
    page = client.get("/")
    assert page.status_code == 200
    assert "化实" in page.text
    assert "/static/images/logo.svg" in page.text
    logo = client.get("/static/images/logo.svg")
    assert logo.status_code == 200
    assert "恐龙头骨" in logo.text


def test_health_does_not_leak_secrets(settings) -> None:
    client, _ = _client(settings)
    response = client.get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    body = response.text.lower()
    assert "api_key" not in body
    assert "token" not in body


def test_non_stream_chat(settings) -> None:
    client, service = _client(settings)
    response = client.post(
        "/api/chat",
        json={
            "message": "解释蒸馏",
            "user_id": "student",
            "thread_id": service.new_thread_id(),
        },
    )
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert "离线测试回答" in response.json()["answer"]


def test_stream_event_order(settings) -> None:
    client, service = _client(settings)
    with client.stream(
        "POST",
        "/api/chat/stream",
        json={
            "message": "什么是物质的量？",
            "user_id": "student",
            "thread_id": service.new_thread_id(),
        },
    ) as response:
        events = _events(response)
    names = [item["event"] for item in events]
    assert response.status_code == 200
    assert names[0] == "start"
    assert "token" in names
    assert names.index("token") < names.index("result")
    assert names[-1] == "done"
    result = next(item["data"] for item in events if item["event"] == "result")
    assert result["success"] is True


def test_stream_tool_status_events(settings) -> None:
    result = AssistantResponse(
        success=True,
        intent="web_search",
        answer="检索完成。",
        tools_used=["search_web"],
        safety_level="low",
    )

    class ToolRuntime:
        document_parser = SimpleNamespace(parse=lambda _: None)

        def stream(self, *_args, **_kwargs):
            yield {
                "type": "messages",
                "data": (
                    AIMessageChunk(
                        content="",
                        tool_call_chunks=[
                            {
                                "name": "search_web",
                                "args": '{"query":"绿色化学"}',
                                "id": "call-1",
                                "index": 0,
                                "type": "tool_call_chunk",
                            }
                        ],
                    ),
                    {},
                ),
            }
            yield {
                "type": "messages",
                "data": (
                    ToolMessage(
                        content="检索结果",
                        tool_call_id="call-1",
                        name="search_web",
                    ),
                    {},
                ),
            }
            yield {
                "type": "updates",
                "data": {"model": {"structured_response": result}},
            }

        def current_state(self, _thread_id):
            return {"structured_response": result}

    service = HuashiService(settings, runtime=ToolRuntime())
    client = TestClient(create_app(service=service))
    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"message": "查资料", "user_id": "u", "thread_id": "t"},
    ) as response:
        events = _events(response)
    names = [item["event"] for item in events]
    assert names.index("tool_start") < names.index("tool_end")
    assert next(item for item in events if item["event"] == "tool_start")["data"]["name"] == "search_web"
    assert next(item for item in events if item["event"] == "tool_end")["data"]["success"] is True


def test_stream_exception_is_friendly_and_terminates(settings) -> None:
    class ExplodingRuntime:
        document_parser = SimpleNamespace(parse=lambda _: None)

        def stream(self, *_args, **_kwargs):
            raise RuntimeError("internal-secret-stack")
            yield  # pragma: no cover

        def current_state(self, _thread_id):
            return {}

    service = HuashiService(settings, runtime=ExplodingRuntime())
    client = TestClient(create_app(service=service))
    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"message": "测试", "user_id": "u", "thread_id": "t"},
    ) as response:
        events = _events(response)
    assert [event["event"] for event in events] == ["start", "error", "done"]
    assert events[1]["data"]["message"] == "请求处理失败，请检查配置或稍后重试。"
    assert "internal-secret-stack" not in json.dumps(events, ensure_ascii=False)


def test_same_thread_keeps_context_and_different_thread_isolated(settings) -> None:
    client, service = _client(settings)
    thread_a = service.new_thread_id()
    thread_b = service.new_thread_id()
    first = client.post(
        "/api/chat",
        json={"message": "记住本轮关键词：火山石", "user_id": "u", "thread_id": thread_a},
    )
    same = client.post(
        "/api/chat",
        json={"message": "本轮关键词是什么？", "user_id": "u", "thread_id": thread_a},
    )
    other = client.post(
        "/api/chat",
        json={"message": "本轮关键词是什么？", "user_id": "u", "thread_id": thread_b},
    )
    assert first.status_code == 200
    assert "火山石" in same.json()["answer"]
    assert "没有记录" in other.json()["answer"]


def test_reset_returns_new_thread(settings) -> None:
    client, _ = _client(settings)
    one = client.post("/api/reset", json={"user_id": "student"}).json()
    two = client.post("/api/reset", json={"user_id": "student"}).json()
    assert one["user_id"] == "student"
    assert one["thread_id"] != two["thread_id"]


def test_write_file_endpoint(settings) -> None:
    client, _ = _client(settings)
    response = client.post(
        "/api/write-file",
        json={
            "filename": "web-note.md",
            "content": "# Web note",
            "file_format": "md",
            "overwrite": False,
        },
    )
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert (settings.outputs_dir / "web-note.md").read_text(encoding="utf-8") == "# Web note"


def test_upload_text_and_parse(settings) -> None:
    client, _ = _client(settings)
    response = client.post(
        "/api/read-file",
        data={"user_id": "student", "thread_id": "thread-1"},
        files={"file": ("notes.md", b"# Acid-base notes", "text/markdown")},
    )
    payload = response.json()
    assert response.status_code == 200
    assert payload["success"] is True
    assert payload["parse_result"]["title"] == "notes"
    assert "Acid-base" in payload["parse_result"]["summary"]


def test_upload_pdf_uses_fake_mineru(settings) -> None:
    client, _ = _client(
        settings,
        mineru_client=FakeMinerUClient("# Parsed PDF\n\n滴定实验资料。"),
    )
    response = client.post(
        "/api/read-file",
        data={"user_id": "student", "thread_id": "thread-2"},
        files={"file": ("manual.pdf", b"%PDF-fake", "application/pdf")},
    )
    payload = response.json()
    assert payload["success"] is True
    assert "滴定实验资料" in payload["parse_result"]["summary"]
    assert payload["parse_result"]["result_path"].startswith("parsed/")


def test_upload_rejects_traversal_and_unsupported_type(settings) -> None:
    client, _ = _client(settings)
    traversal = client.post(
        "/api/read-file",
        data={"user_id": "u", "thread_id": "t"},
        files={"file": ("../escape.md", b"bad", "text/markdown")},
    ).json()
    unsupported = client.post(
        "/api/read-file",
        data={"user_id": "u", "thread_id": "t"},
        files={"file": ("run.py", b"print(1)", "text/plain")},
    ).json()
    assert traversal["success"] is False
    assert unsupported["success"] is False
    assert not (settings.workspace_dir.parent / "escape.md").exists()


def test_dangerous_request_streams_safety_refusal(settings) -> None:
    client, service = _client(settings)
    with client.stream(
        "POST",
        "/api/chat/stream",
        json={
            "message": "请给我爆炸物的详细制备配方",
            "user_id": "student",
            "thread_id": service.new_thread_id(),
        },
    ) as response:
        events = _events(response)
    result = next(item["data"] for item in events if item["event"] == "result")
    assert result["intent"] == "safety_refusal"
    assert result["safety_level"] == "high"
    assert "token" in [item["event"] for item in events]
