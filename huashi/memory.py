"""封装 LangGraph 的短期 Checkpointer 与长期 InMemoryStore。"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from huashi.models import PreferenceKey, UserPreferences


@dataclass(frozen=True)
class HuashiContext:
    """每次 Agent 调用传入的运行上下文。"""

    user_id: str
    allow_memory_write: bool = False
    debug: bool = False


class HuashiMemory:
    """管理线程内短期记忆与跨线程学习偏好。"""

    def __init__(
        self,
        checkpointer: InMemorySaver | None = None,
        store: InMemoryStore | None = None,
    ) -> None:
        self.checkpointer = checkpointer or InMemorySaver()
        self.store = store or InMemoryStore()

    @staticmethod
    def new_thread_id() -> str:
        """创建新的会话线程 ID；reset 通过换用新 ID 实现。"""

        return uuid4().hex

    @staticmethod
    def _namespace(user_id: str) -> tuple[str, str, str]:
        return ("huashi", "preferences", user_id)

    def get_preferences(self, user_id: str) -> UserPreferences:
        """读取某用户跨线程保存的学习偏好。"""

        item = self.store.get(self._namespace(user_id), "profile")
        return UserPreferences.model_validate(item.value if item else {})

    def save_preference(
        self,
        user_id: str,
        key: PreferenceKey,
        value: str,
        *,
        explicit_permission: bool,
    ) -> UserPreferences:
        """仅在明确授权时写入一个白名单偏好字段。"""

        if not explicit_permission:
            raise PermissionError("仅在用户明确要求记住时才允许写入长期记忆")
        current = self.get_preferences(user_id).model_dump(exclude_none=True)
        current[key.value] = value
        validated = UserPreferences.model_validate(current)
        self.store.put(
            self._namespace(user_id),
            "profile",
            validated.model_dump(exclude_none=True),
        )
        return validated
