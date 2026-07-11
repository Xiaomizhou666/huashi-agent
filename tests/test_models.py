"""验证 Pydantic 结构化输出的正常与异常数据。"""

import json

import pytest
from pydantic import ValidationError

from huashi.models import AssistantResponse, SourceItem


def test_assistant_response_is_json_serializable() -> None:
    response = AssistantResponse(
        success=True,
        intent="web_search",
        answer="找到资料。",
        tools_used=["search_web"],
        sources=[SourceItem(title="来源", url="https://example.com")],
        safety_level="low",
    )
    encoded = json.dumps(response.model_dump(mode="json"), ensure_ascii=False)
    assert "找到资料" in encoded


def test_safety_refusal_requires_high_level() -> None:
    with pytest.raises(ValidationError):
        AssistantResponse(
            success=True,
            intent="safety_refusal",
            answer="拒绝。",
            safety_level="low",
        )


def test_error_requires_message() -> None:
    with pytest.raises(ValidationError):
        AssistantResponse(
            success=False,
            intent="error",
            answer="失败。",
            safety_level="low",
        )
