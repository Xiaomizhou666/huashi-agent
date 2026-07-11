"""测试 InMemoryStore 长期偏好与 thread_id 短期会话隔离。"""

import pytest

from huashi.memory import HuashiMemory
from huashi.models import PreferenceKey
from huashi.service import HuashiService
from huashi.testing import DeterministicStructuredChatModel


def test_long_term_preferences_cross_threads() -> None:
    memory = HuashiMemory()
    memory.save_preference(
        "student-1",
        PreferenceKey.detail_level,
        "详细",
        explicit_permission=True,
    )
    thread_a = memory.new_thread_id()
    thread_b = memory.new_thread_id()
    assert thread_a != thread_b
    assert memory.get_preferences("student-1").detail_level == "详细"


def test_memory_write_requires_explicit_permission() -> None:
    memory = HuashiMemory()
    with pytest.raises(PermissionError):
        memory.save_preference(
            "student-1",
            PreferenceKey.learning_stage,
            "高中",
            explicit_permission=False,
        )


def test_thread_isolation_with_checkpointer(settings) -> None:
    service = HuashiService(settings, model=DeterministicStructuredChatModel())
    thread_a = service.new_thread_id()
    thread_b = service.new_thread_id()
    first = service.chat("记住本轮关键词：甲", "student", thread_a)
    same_thread = service.chat("本轮关键词是什么？", "student", thread_a)
    other_thread = service.chat("本轮关键词是什么？", "student", thread_b)
    assert first.success
    assert "甲" in same_thread.answer
    assert "没有记录" in other_thread.answer
