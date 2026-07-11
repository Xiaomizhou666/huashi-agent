"""测试 MinerU 上传、任务轮询、成功结果和失败状态处理。"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import httpx
import pytest

from huashi.clients.mineru_client import MinerUAPIError, MinerUClient
from huashi.tools.file_reader import DocumentParser, parse_local_document


def _result_zip(markdown: str) -> bytes:
    """创建包含 full.md 的内存 ZIP。"""

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("result/full.md", markdown)
    return buffer.getvalue()


def test_parse_file_retries_first_status_timeout_and_waits_until_done(
    tmp_path: Path,
) -> None:
    """首次状态请求超时后应继续轮询，而不是在任务提交后立即失败。"""

    workspace = tmp_path / "workspace"
    inputs_dir = workspace / "inputs"
    parsed_root = workspace / "parsed"
    inputs_dir.mkdir(parents=True)
    source = inputs_dir / "paper.pdf"
    source.write_bytes(b"%PDF-1.7\nmock")
    zip_bytes = _result_zip("# 解析成功\n\n等待状态后返回的正文。")
    states = iter(["waiting-file", "pending", "running", "converting", "done"])
    poll_calls = 0
    uploaded = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal poll_calls, uploaded
        if request.method == "POST" and request.url.path == "/api/v4/file-urls/batch":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "msg": "ok",
                    "data": {
                        "batch_id": "batch-123",
                        "file_urls": ["https://upload.example/paper.pdf"],
                    },
                },
            )
        if request.method == "PUT" and request.url.host == "upload.example":
            body = request.read()
            assert body == source.read_bytes()
            assert request.headers["content-length"] == str(source.stat().st_size)
            uploaded = True
            return httpx.Response(200)
        if (
            request.method == "GET"
            and request.url.path == "/api/v4/extract-results/batch/batch-123"
        ):
            assert uploaded
            poll_calls += 1
            if poll_calls == 1:
                raise httpx.ReadTimeout("temporary status timeout", request=request)
            state = next(states)
            item: dict[str, object] = {
                "file_name": source.name,
                "state": state,
                "err_msg": "",
            }
            if state == "done":
                item["full_zip_url"] = "https://download.example/result.zip"
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "msg": "ok",
                    "data": {"batch_id": "batch-123", "extract_result": [item]},
                },
            )
        if request.method == "GET" and request.url.host == "download.example":
            return httpx.Response(200, content=zip_bytes)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    sleeps: list[float] = []
    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = MinerUClient(
        "fake-token",
        http_client=http_client,
        poll_interval_seconds=0.25,
        max_poll_attempts=8,
        sleep_fn=sleeps.append,
    )

    parser = DocumentParser(
        inputs_dir,
        parsed_root,
        workspace,
        mineru_client=client,
    )
    result = parse_local_document("paper.pdf", parser=parser)

    assert result.success
    assert (result.content_excerpt or "").startswith("# 解析成功")
    assert result.result_path and result.result_path.endswith("full.md")
    assert poll_calls == 6
    assert sleeps == [0.25] * 5


def test_poll_batch_failed_state_raises_api_error() -> None:
    """MinerU 明确返回 failed 时应立即报告服务端失败原因。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": 0,
                "msg": "ok",
                "data": {
                    "batch_id": "batch-failed",
                    "extract_result": {
                        "file_name": "bad.pdf",
                        "state": "failed",
                        "err_msg": "file damaged",
                    },
                },
            },
        )

    client = MinerUClient(
        "fake-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep_fn=lambda _: None,
    )

    with pytest.raises(MinerUAPIError, match="file damaged"):
        client.poll_batch("batch-failed", "bad.pdf")


def test_poll_batch_times_out_only_after_all_waiting_attempts() -> None:
    """持续 pending 时必须完成配置的全部轮询次数后才超时。"""

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "code": 0,
                "msg": "ok",
                "data": {
                    "batch_id": "batch-pending",
                    "extract_result": [
                        {"file_name": "paper.pdf", "state": "pending"}
                    ],
                },
            },
        )

    sleeps: list[float] = []
    client = MinerUClient(
        "fake-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        poll_interval_seconds=0.5,
        max_poll_attempts=4,
        sleep_fn=sleeps.append,
    )

    with pytest.raises(TimeoutError, match="最后状态：pending"):
        client.poll_batch("batch-pending", "paper.pdf")

    assert calls == 4
    assert sleeps == [0.5, 0.5, 0.5]
