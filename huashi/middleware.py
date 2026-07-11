"""组装 LangChain 内置中间件与自定义化学安全中间件。"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
    ModelRetryMiddleware,
    ToolCallLimitMiddleware,
    ToolRetryMiddleware,
)
from langchain_core.messages import HumanMessage, SystemMessage

from huashi.memory import HuashiContext
from huashi.models import RiskAssessment
from huashi.prompts import HIGH_RISK_CONTEXT

_HIGH_RISK_RULES: dict[str, tuple[str, ...]] = {
    "explosives": (
        "爆炸物",
        "炸药",
        "起爆剂",
        "雷管",
        "高能材料",
        "detonator",
        "explosive synthesis",
    ),
    "toxic_release": (
        "大规模制备有毒气体",
        "释放氯气",
        "释放硫化氢",
        "毒气攻击",
        "大量氰化氢",
    ),
    "controlled_substances": (
        "冰毒合成",
        "甲基苯丙胺合成",
        "芬太尼合成",
        "非法药物合成",
        "受控物质合成",
    ),
    "evasion": (
        "规避检测",
        "躲过监管",
        "绕过安全检查",
        "不被发现",
        "隐藏化学品来源",
    ),
    "harm": (
        "伤害他人",
        "毒死",
        "投毒",
        "自杀用化学品",
        "腐蚀别人",
    ),
}
_MEDIUM_RISK_TERMS = (
    "浓硫酸",
    "浓硝酸",
    "强碱",
    "明火",
    "高压反应",
    "高温反应",
    "有毒气体",
    "未成年人",
    "家庭实验",
)
_OPERATIONAL_TERMS = (
    "怎么制备",
    "如何合成",
    "详细步骤",
    "配方",
    "比例",
    "纯化",
    "浓缩",
    "放大",
    "批量",
    "温度和压力",
)


def assess_chem_risk(text: str) -> RiskAssessment:
    """使用可审计关键词规则给请求分级，不声称替代专业评估。"""

    normalized = re.sub(r"\s+", "", text.lower())
    categories: list[str] = []
    reasons: list[str] = []
    for category, terms in _HIGH_RISK_RULES.items():
        hits = [term for term in terms if term.lower().replace(" ", "") in normalized]
        if hits:
            categories.append(category)
            reasons.append(f"命中高风险类别 {category}")
    operational = any(term.replace(" ", "") in normalized for term in _OPERATIONAL_TERMS)
    medium_hits = [term for term in _MEDIUM_RISK_TERMS if term in normalized]
    if categories and operational:
        return RiskAssessment(
            level="high",
            categories=categories,
            reasons=reasons + ["请求包含可直接执行的操作性细节"],
            safe_alternatives=[
                "介绍非操作性的反应原理与风险机理",
                "使用虚拟实验或公开教学动画",
                "在专业教师和合规实验室监督下学习",
            ],
        )
    if categories:
        return RiskAssessment(
            level="high",
            categories=categories,
            reasons=reasons,
            safe_alternatives=["仅讨论理论背景和低风险替代演示"],
        )
    if medium_hits:
        return RiskAssessment(
            level="medium",
            categories=["laboratory_hazard"],
            reasons=[f"涉及潜在实验风险：{', '.join(medium_hits[:4])}"],
            safe_alternatives=["补充 PPE、通风、教师监督与废物处理要求"],
        )
    return RiskAssessment(level="low")


def latest_user_text(messages: Sequence[Any]) -> str:
    """从消息序列中提取最近一次用户文本。"""

    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return message.text
        if isinstance(message, dict) and message.get("role") == "user":
            return str(message.get("content") or "")
    return ""


class ChemSafetyMiddleware(AgentMiddleware[AgentState, HuashiContext]):
    """在每次模型调用前追加安全上下文，并对高风险请求移除执行工具。"""

    def wrap_model_call(
        self,
        request: ModelRequest[HuashiContext],
        handler: Callable[[ModelRequest[HuashiContext]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        text = latest_user_text(request.messages)
        assessment = assess_chem_risk(text)
        if assessment.level != "high":
            return handler(request)
        original = request.system_message.text if request.system_message else ""
        safe_prompt = f"{original}\n\n{HIGH_RISK_CONTEXT}".strip()
        # response_format 的结构化工具不在 request.tools 中，移除执行工具后仍可返回 schema。
        restricted = request.override(
            system_message=SystemMessage(content=safe_prompt),
            tools=[],
        )
        return handler(restricted)



def _should_retry_model(error: Exception) -> bool:
    """识别常见模型超时、连接、限流与临时服务错误。"""

    if isinstance(error, (TimeoutError, ConnectionError)):
        return True
    class_name = type(error).__name__.lower()
    if any(token in class_name for token in ("timeout", "connection", "ratelimit")):
        return True
    status_code = getattr(error, "status_code", None)
    return status_code in {408, 409, 429, 500, 502, 503, 504}


def _safe_tool_failure(error: Exception) -> str:
    """向模型提供脱敏且可理解的外部工具错误。"""

    return f"外部工具暂时不可用：{type(error).__name__}。请说明失败，不要编造结果。"


def build_middleware() -> list[AgentMiddleware[Any, Any]]:
    """返回实际接入 Agent 的有限重试、调用限制与安全中间件。"""

    return [
        ChemSafetyMiddleware(),
        ModelRetryMiddleware(
            max_retries=2,
            retry_on=_should_retry_model,
            initial_delay=0.5,
            max_delay=2.0,
            jitter=False,
            on_failure="error",
        ),
        ToolRetryMiddleware(
            max_retries=2,
            tools=["search_web", "parse_local_document"],
            retry_on=(TimeoutError, ConnectionError, RuntimeError),
            initial_delay=0.2,
            max_delay=1.0,
            jitter=False,
            on_failure=_safe_tool_failure,
        ),
        ToolCallLimitMiddleware(run_limit=6, exit_behavior="continue"),
        ToolCallLimitMiddleware(
            tool_name="search_web", run_limit=1, exit_behavior="continue"
        ),
        ToolCallLimitMiddleware(
            tool_name="parse_local_document", run_limit=1, exit_behavior="continue"
        ),
    ]
