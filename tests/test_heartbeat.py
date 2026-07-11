"""验证 Web Heartbeat 配置、时长、隔离性和前端定时器生命周期。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from huashi.api import create_app
from huashi.config import HuashiSettings
from huashi.heartbeat import HeartbeatService
from huashi.service import HuashiService
from huashi.testing import DeterministicStructuredChatModel


def test_heartbeat_config_reads_environment(monkeypatch, tmp_path: Path) -> None:
    """环境变量应控制开关和秒级间隔。"""

    monkeypatch.setenv("HEARTBEAT_ENABLED", "false")
    monkeypatch.setenv("HEARTBEAT_INTERVAL_SECONDS", "45")
    settings = HuashiSettings(WORKSPACE_DIR=tmp_path / "workspace")
    assert settings.heartbeat_enabled is False
    assert settings.heartbeat_interval_seconds == 45


def test_disabled_heartbeat_generates_nothing(settings) -> None:
    """关闭时 Service 不生成文案，HTTP 接口返回 204。"""

    disabled = settings.model_copy(update={"heartbeat_enabled": False})
    heartbeat = HeartbeatService(disabled, clock_ms=lambda: 180_000)
    assert heartbeat.generate(session_started_at_ms=0, sequence=0) is None

    service = HuashiService(disabled, runtime=object())
    client = TestClient(
        create_app(service=service, heartbeat_service=heartbeat)
    )
    response = client.get(
        "/api/heartbeat",
        params={"session_started_at_ms": 0, "sequence": 0},
    )
    assert response.status_code == 204
    assert response.content == b""


def test_enabled_heartbeat_rotates_and_calculates_minutes(settings) -> None:
    """心跳按序号轮换，并按真实经过秒数计算完整分钟。"""

    enabled = settings.model_copy(
        update={"heartbeat_enabled": True, "heartbeat_interval_seconds": 30}
    )
    heartbeat = HeartbeatService(enabled, clock_ms=lambda: 305_900)
    first = heartbeat.generate(session_started_at_ms=5_900, sequence=0)
    second = heartbeat.generate(session_started_at_ms=5_900, sequence=1)
    third = heartbeat.generate(session_started_at_ms=5_900, sequence=2)

    assert first is not None and "咕噜咕噜" in first.message
    assert second is not None and "5 分钟" in second.message
    assert second.elapsed_seconds == 300
    assert second.elapsed_minutes == 5
    assert second.interval_seconds == 30
    assert third is not None and "目前没有新情况" in third.message


def test_heartbeat_endpoint_does_not_call_agent_tools_or_memory(settings) -> None:
    """独立端点不能触发 Runtime，也不能写 Checkpointer/Store。"""

    class ExplodingRuntime:
        def __getattr__(self, name):
            raise AssertionError(f"heartbeat must not access runtime.{name}")

    service = HuashiService(settings, runtime=ExplodingRuntime())
    heartbeat = HeartbeatService(settings, clock_ms=lambda: 120_000)
    client = TestClient(create_app(service=service, heartbeat_service=heartbeat))

    response = client.get(
        "/api/heartbeat",
        params={"session_started_at_ms": 0, "sequence": 1},
    )
    assert response.status_code == 200
    assert "2 分钟" in response.json()["message"]
    assert service.memory.get_preferences("student").model_dump(exclude_none=True) == {}
    assert not service.memory.checkpointer.storage


def test_heartbeat_failure_does_not_break_chat(settings) -> None:
    """心跳异常只影响该轻量请求，普通聊天仍可用。"""

    class BrokenHeartbeat(HeartbeatService):
        def generate(self, *, session_started_at_ms: int, sequence: int):
            raise RuntimeError("heartbeat-only-failure")

    service = HuashiService(settings, model=DeterministicStructuredChatModel())
    client = TestClient(
        create_app(service=service, heartbeat_service=BrokenHeartbeat(settings))
    )
    heartbeat_response = client.get(
        "/api/heartbeat",
        params={"session_started_at_ms": 0, "sequence": 0},
    )
    chat_response = client.post(
        "/api/chat",
        json={
            "message": "解释蒸馏",
            "user_id": "student",
            "thread_id": service.new_thread_id(),
        },
    )
    assert heartbeat_response.status_code == 503
    assert "heartbeat-only-failure" not in heartbeat_response.text
    assert chat_response.status_code == 200
    assert chat_response.json()["success"] is True


def test_frontend_controller_has_single_timer_and_resets_session() -> None:
    """Node 冒烟验证重复 start、reset、pause/resume 和关闭开关。"""

    root = Path(__file__).resolve().parents[1]
    module_uri = (root / "frontend/static/js/heartbeat.js").resolve().as_uri()
    script = f"""
      import assert from 'node:assert/strict';
      import {{ HeartbeatController }} from {json.dumps(module_uri)};

      let now = 1_000;
      let nextId = 0;
      const tasks = new Map();
      const setTimer = (callback, delay) => {{
        const id = ++nextId;
        tasks.set(id, {{ callback, delay }});
        return id;
      }};
      const clearTimer = (id) => tasks.delete(id);
      const controller = new HeartbeatController({{
        enabled: true,
        intervalSeconds: 9,
        onMessage: () => {{}},
        fetchImpl: async () => ({{ ok: true, status: 200, json: async () => ({{ message: 'ok' }}) }}),
        setTimeoutImpl: setTimer,
        clearTimeoutImpl: clearTimer,
        now: () => now,
        isVisible: () => true,
      }});

      const messages = [];
      controller.onMessage = (message) => messages.push(message);
      assert.equal(controller.start(), true);
      assert.equal(controller.start(), false);
      assert.equal(tasks.size, 1);
      assert.equal([...tasks.values()][0].delay, 9_000);
      const firstStart = controller.snapshot().startedAtMs;
      const [firstTimerId, firstTask] = tasks.entries().next().value;
      tasks.delete(firstTimerId);
      firstTask.callback();
      await new Promise((resolve) => globalThis.setTimeout(resolve, 0));
      assert.deepEqual(messages, ['ok']);
      assert.equal(controller.snapshot().sequence, 1);
      assert.equal(tasks.size, 1);

      now = 5_000;
      controller.reset();
      assert.equal(tasks.size, 1);
      assert.equal(controller.snapshot().startedAtMs, 5_000);
      assert.notEqual(controller.snapshot().startedAtMs, firstStart);
      assert.equal(controller.snapshot().sequence, 0);

      controller.pause();
      assert.equal(tasks.size, 0);
      assert.equal(controller.resume(), true);
      assert.equal(controller.resume(), false);
      assert.equal(tasks.size, 1);
      controller.stop({{ clearSession: true }});
      assert.equal(tasks.size, 0);
      assert.equal(controller.snapshot().startedAtMs, null);

      const disabled = new HeartbeatController({{
        enabled: false,
        intervalSeconds: 1,
        onMessage: () => {{ throw new Error('must not emit'); }},
        setTimeoutImpl: setTimer,
        clearTimeoutImpl: clearTimer,
        isVisible: () => true,
      }});
      assert.equal(disabled.start(), false);
      assert.equal(tasks.size, 0);
    """
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
