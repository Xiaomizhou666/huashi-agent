"""提供不调用模型或工具的 Web 会话轻量心跳文案。"""

from __future__ import annotations

import time
from collections.abc import Callable

from huashi.config import HuashiSettings
from huashi.models import HeartbeatResponse


class HeartbeatService:
    """根据会话持续时间生成轮换文案；服务本身无会话状态。"""

    _MESSAGES = (
        "咕噜咕噜……化石又沉了下去。",
        "化实提醒您，我们已经聊了 {minutes} 分钟了，别忘记你的实验哦。",
        "烧杯里的气泡冒了一会儿，目前没有新情况。",
    )

    def __init__(
        self,
        settings: HuashiSettings,
        *,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self.settings = settings
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))

    @property
    def enabled(self) -> bool:
        """返回 Web 心跳是否启用。"""

        return self.settings.heartbeat_enabled

    def generate(
        self,
        *,
        session_started_at_ms: int,
        sequence: int,
    ) -> HeartbeatResponse | None:
        """生成一次心跳；关闭时返回 ``None``，不产生任何消息。"""

        if not self.enabled:
            return None
        now_ms = self._clock_ms()
        elapsed_seconds = max(0, (now_ms - session_started_at_ms) // 1000)
        elapsed_minutes = elapsed_seconds // 60
        template = self._MESSAGES[sequence % len(self._MESSAGES)]
        message = template.format(minutes=elapsed_minutes)
        return HeartbeatResponse(
            message=message,
            elapsed_seconds=elapsed_seconds,
            elapsed_minutes=elapsed_minutes,
            interval_seconds=self.settings.heartbeat_interval_seconds,
            sequence=sequence,
        )
