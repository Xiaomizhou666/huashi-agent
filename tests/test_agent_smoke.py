"""使用 Fake 模型验证 create_agent、ToolStrategy 和 service 接口。"""

from huashi.service import HuashiService
from huashi.testing import DeterministicStructuredChatModel


def test_agent_returns_structured_response(settings) -> None:
    service = HuashiService(settings, model=DeterministicStructuredChatModel())
    response = service.chat("什么是物质的量？", "student", service.new_thread_id())
    assert response.success
    assert response.intent == "qa"
    assert "离线测试回答" in response.answer


def test_frontend_json_interface(settings) -> None:
    service = HuashiService(settings, model=DeterministicStructuredChatModel())
    result = service.chat_json("解释蒸馏", "student", service.new_thread_id())
    assert isinstance(result, dict)
    assert result["success"] is True
    assert result["intent"] == "qa"
