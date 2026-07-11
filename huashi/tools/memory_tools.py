"""提供显式授权的长期偏好保存与读取工具。"""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from langchain.tools import ToolRuntime

from huashi.memory import HuashiContext, HuashiMemory
from huashi.models import PreferenceKey


def build_memory_tools(memory: HuashiMemory) -> list[StructuredTool]:
    """构建绑定 InMemoryStore 的偏好工具。"""

    def _get(runtime: ToolRuntime[HuashiContext]) -> dict[str, object]:
        """读取当前用户已明确保存的学习偏好。"""

        return memory.get_preferences(runtime.context.user_id).model_dump(
            mode="json", exclude_none=True
        )

    def _save(
        key: PreferenceKey,
        value: str,
        runtime: ToolRuntime[HuashiContext],
    ) -> dict[str, object]:
        """仅在用户明确要求记住时保存一个白名单学习偏好。"""

        saved = memory.save_preference(
            runtime.context.user_id,
            key,
            value,
            explicit_permission=runtime.context.allow_memory_write,
        )
        return {
            "success": True,
            "preferences": saved.model_dump(mode="json", exclude_none=True),
            "note": "InMemoryStore 数据会在程序重启后丢失。",
        }

    return [
        StructuredTool.from_function(
            func=_get,
            name="get_user_preferences",
            description="读取当前 user_id 已保存的称呼、学习阶段和回答偏好。",
        ),
        StructuredTool.from_function(
            func=_save,
            name="save_user_preference",
            description=(
                "仅当用户明确说‘记住’或要求跨会话保留偏好时调用；"
                "只保存白名单学习偏好，禁止保存秘密和完整文档。"
            ),
        ),
    ]
