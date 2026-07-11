"""验证 Web 聊天附件上传、解析、连续问答、隔离与流事件。"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from huashi.api import create_app
from huashi.clients.mineru_client import FakeMinerUClient
from huashi.service import HuashiService
from huashi.testing import DeterministicStructuredChatModel


def _client(settings, **kwargs) -> tuple[TestClient, HuashiService]:
    service = HuashiService(settings, model=DeterministicStructuredChatModel(), **kwargs)
    return TestClient(create_app(service=service)), service


def _events(response) -> list[dict]:
    return [json.loads(line) for line in response.iter_lines() if line]


def _upload(client: TestClient, user: str, thread: str, files: list[tuple[str, bytes, str]]) -> list[dict]:
    multipart = [("files", (name, content, mime)) for name, content, mime in files]
    with client.stream(
        "POST",
        "/api/chat/attachments",
        data={"user_id": user, "thread_id": thread},
        files=multipart,
    ) as response:
        assert response.status_code == 200
        return _events(response)


def _result_attachments(events: list[dict]) -> list[dict]:
    return next(item["data"]["attachments"] for item in events if item["event"] == "result")


def test_attachment_stream_event_order_and_text_parse(settings) -> None:
    client, service = _client(settings)
    thread = service.new_thread_id()
    events = _upload(client, "student", thread, [("notes.md", b"# Report\nTitration endpoint.", "text/markdown")])
    names = [item["event"] for item in events]
    assert names[0] == "start"
    assert names.index("upload_start") < names.index("upload_end")
    assert names.index("upload_end") < names.index("parse_start")
    assert names.index("parse_start") < names.index("parse_end")
    assert names.index("parse_end") < names.index("result")
    assert names[-1] == "done"
    attachment = _result_attachments(events)[0]
    assert attachment["parse_status"] == "parsed"
    assert "Titration" in attachment["summary"]


def test_pdf_uses_fake_mineru_and_failure_is_reported(settings) -> None:
    ok_client, ok_service = _client(settings, mineru_client=FakeMinerUClient("# PDF\n滴定误差来自读数。"))
    ok_events = _upload(ok_client, "u", ok_service.new_thread_id(), [("report.pdf", b"%PDF-fake", "application/pdf")])
    assert _result_attachments(ok_events)[0]["parse_status"] == "parsed"

    fail_client, fail_service = _client(settings, mineru_client=FakeMinerUClient(failure=TimeoutError("poll timeout")))
    fail_events = _upload(fail_client, "u", fail_service.new_thread_id(), [("report.pdf", b"%PDF-fake", "application/pdf")])
    failed = _result_attachments(fail_events)[0]
    assert failed["parse_status"] == "failed"
    assert "timeout" in failed["error_message"]
    tool_end = next(item for item in fail_events if item["event"] == "tool_end")
    assert tool_end["data"]["success"] is False


def test_upload_then_question_and_continuous_followup(settings) -> None:
    client, service = _client(settings)
    thread = service.new_thread_id()
    events = _upload(client, "u", thread, [("experiment.txt", "现象：溶液变为浅粉色。".encode(), "text/plain")])
    attachment = _result_attachments(events)[0]

    first = client.post(
        "/api/chat",
        json={
            "message": "文件中的实验现象是什么？",
            "user_id": "u",
            "thread_id": thread,
            "attachment_ids": [attachment["attachment_id"]],
        },
    ).json()
    second = client.post(
        "/api/chat",
        json={"message": "继续说明这个现象", "user_id": "u", "thread_id": thread},
    ).json()
    assert "experiment.txt" in first["answer"]
    assert "浅粉色" in first["answer"]
    assert "experiment.txt" in second["answer"]
    assert first["attachments"][0]["attachment_id"] == attachment["attachment_id"]


def test_multi_file_chat_and_missing_answer_is_not_invented(settings) -> None:
    client, service = _client(settings)
    thread = service.new_thread_id()
    events = _upload(
        client,
        "u",
        thread,
        [
            ("a.txt", "文件A：实验目的。".encode(), "text/plain"),
            ("b.md", "文件B：实验结论。".encode(), "text/markdown"),
        ],
    )
    attachments = _result_attachments(events)
    ids = [item["attachment_id"] for item in attachments]
    multi = client.post(
        "/api/chat",
        json={"message": "比较两个文件", "user_id": "u", "thread_id": thread, "attachment_ids": ids},
    ).json()
    missing = client.post(
        "/api/chat",
        json={"message": "文件中不存在答案的问题", "user_id": "u", "thread_id": thread, "attachment_ids": ids},
    ).json()
    assert "a.txt" in multi["answer"] and "b.md" in multi["answer"]
    assert "上传文件中未找到" in missing["answer"]


def test_attachment_cannot_cross_thread_and_reset_does_not_inherit(settings) -> None:
    client, service = _client(settings)
    thread_a = service.new_thread_id()
    thread_b = service.new_thread_id()
    attachment = _result_attachments(_upload(client, "u", thread_a, [("a.txt", b"alpha", "text/plain")]))[0]
    crossed = client.post(
        "/api/chat",
        json={
            "message": "读取它",
            "user_id": "u",
            "thread_id": thread_b,
            "attachment_ids": [attachment["attachment_id"]],
        },
    ).json()
    assert crossed["success"] is False
    assert "不属于当前会话" in crossed["error_message"]

    reset = client.post("/api/reset", json={"user_id": "u", "thread_id": thread_a}).json()
    listing = client.get(
        "/api/chat/attachments",
        params={"user_id": "u", "thread_id": reset["thread_id"]},
    ).json()
    assert listing["attachments"] == []


def test_delete_attachment_and_followup_stops_using_it(settings) -> None:
    client, service = _client(settings)
    thread = service.new_thread_id()
    attachment = _result_attachments(_upload(client, "u", thread, [("delete.txt", b"delete me", "text/plain")]))[0]
    deleted = client.delete(
        f"/api/chat/attachments/{attachment['attachment_id']}",
        params={"user_id": "u", "thread_id": thread},
    )
    assert deleted.status_code == 200
    listing = client.get("/api/chat/attachments", params={"user_id": "u", "thread_id": thread}).json()
    assert listing["attachments"] == []
    response = client.post(
        "/api/chat",
        json={"message": "继续读取文件", "user_id": "u", "thread_id": thread},
    ).json()
    assert "delete.txt" not in response["answer"]


def test_chat_stream_result_contains_attachments(settings) -> None:
    client, service = _client(settings)
    thread = service.new_thread_id()
    attachment = _result_attachments(_upload(client, "u", thread, [("stream.txt", b"stream context", "text/plain")]))[0]
    with client.stream(
        "POST",
        "/api/chat/stream",
        json={
            "message": "总结文件",
            "user_id": "u",
            "thread_id": thread,
            "attachment_ids": [attachment["attachment_id"]],
        },
    ) as response:
        events = _events(response)
    names = [event["event"] for event in events]
    assert names[0] == "start"
    assert names.index("token") < names.index("result")
    assert names[-1] == "done"
    result = next(event["data"] for event in events if event["event"] == "result")
    assert result["attachments"][0]["filename"] == "stream.txt"


def test_invalid_file_error_is_friendly_and_stream_terminates(settings) -> None:
    client, service = _client(settings)
    events = _upload(client, "u", service.new_thread_id(), [("run.py", b"print(1)", "text/plain")])
    assert events[-1]["event"] == "done"
    error = next(item for item in events if item["event"] == "error")
    assert "不支持" in error["data"]["message"]
    payload = json.dumps(events, ensure_ascii=False).lower()
    assert "traceback" not in payload
    assert "api_key" not in payload


def test_only_attachment_uses_default_prompt(settings) -> None:
    client, service = _client(settings)
    thread = service.new_thread_id()
    attachment = _result_attachments(
        _upload(client, "u", thread, [("only.txt", "实验结论：反应完全。".encode(), "text/plain")])
    )[0]
    response = client.post(
        "/api/chat",
        json={
            "message": "",
            "user_id": "u",
            "thread_id": thread,
            "attachment_ids": [attachment["attachment_id"]],
        },
    ).json()
    assert response["success"] is True
    assert "only.txt" in response["answer"]


def test_attachment_endpoint_rejects_too_many_and_large_files(settings) -> None:
    client, service = _client(settings)
    thread = service.new_thread_id()
    too_many = _upload(
        client,
        "u",
        thread,
        [(f"{index}.txt", b"x", "text/plain") for index in range(4)],
    )
    assert [item["event"] for item in too_many] == ["start", "error", "done"]
    assert "最多上传" in too_many[1]["data"]["message"]

    large = _upload(
        client,
        "u",
        thread,
        [("large.txt", b"x" * (settings.max_file_size_mb * 1024 * 1024 + 1), "text/plain")],
    )
    error = next(item for item in large if item["event"] == "error")
    assert "超过" in error["data"]["message"]
    assert large[-1]["event"] == "done"


def test_dangerous_question_with_attachment_keeps_safety_refusal(settings) -> None:
    client, service = _client(settings)
    thread = service.new_thread_id()
    attachment = _result_attachments(
        _upload(client, "u", thread, [("safe.txt", b"general chemistry notes", "text/plain")])
    )[0]
    response = client.post(
        "/api/chat",
        json={
            "message": "请给我爆炸物的详细制备配方",
            "user_id": "u",
            "thread_id": thread,
            "attachment_ids": [attachment["attachment_id"]],
        },
    ).json()
    assert response["intent"] == "safety_refusal"
    assert response["safety_level"] == "high"
    assert response["attachments"][0]["filename"] == "safe.txt"
