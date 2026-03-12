from __future__ import annotations

import json
from typing import Any, Callable

from agent_runtime_framework.graph.runtime import ExecutionContext
from agent_runtime_framework.graph.types import ResolverFunc, RouteDecision


def _default_state_snapshot(state: Any) -> dict[str, Any]:
    if hasattr(state, "model_dump"):
        return state.model_dump()
    if hasattr(state, "__dict__"):
        return dict(state.__dict__)
    return {"state_repr": repr(state)}


class RuleRouter:
    def __init__(self, resolver: ResolverFunc) -> None:
        self.resolver = resolver

    def decide(
        self,
        state: Any,
        context: ExecutionContext,
        available_actions: list[str],
    ) -> RouteDecision:
        result = self.resolver(state, context, available_actions)
        if isinstance(result, RouteDecision):
            if result.source is None:
                result.source = "rule"
            return result
        return RouteDecision(
            next_node=result,
            reason="由规则路由直接选择。",
            source="rule",
        )


class JsonLLMRouter:
    def __init__(
        self,
        model: str,
        system_prompt: str | None = None,
        temperature: float = 0.0,
        snapshot_builder: Callable[[Any], dict[str, Any]] | None = None,
    ) -> None:
        self.model = model
        self.system_prompt = system_prompt or "你是一个严格遵守 JSON 输出格式的图路由器。"
        self.temperature = temperature
        self.snapshot_builder = snapshot_builder or _default_state_snapshot

    def decide(
        self,
        state: Any,
        context: ExecutionContext,
        available_actions: list[str],
    ) -> RouteDecision:
        if context.llm_client is None:
            raise RuntimeError("ExecutionContext 中缺少 llm_client，无法执行 LLM 路由。")

        snapshot = self.snapshot_builder(state)
        prompt = f"""
你是一个 StateGraph 的路由器。你只负责从给定动作中选择下一步。

当前状态：
{json.dumps(snapshot, ensure_ascii=False, indent=2)}

可选动作：
{json.dumps(available_actions, ensure_ascii=False)}

请只输出 JSON：
{{
  "next_node": "<必须从可选动作中选择>",
  "reason": "简洁说明原因"
}}
""".strip()

        response = context.llm_client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=self.temperature,
        )
        content = (response.choices[0].message.content or "").strip()
        data = json.loads(content)
        decision = RouteDecision.model_validate(data)
        if decision.next_node not in available_actions:
            raise ValueError(f"模型返回了非法动作: {decision.next_node}")
        if decision.source is None:
            decision.source = "llm"
        return decision


class FallbackRouter:
    def __init__(self, primary: Any, fallback: Any) -> None:
        self.primary = primary
        self.fallback = fallback

    def decide(
        self,
        state: Any,
        context: ExecutionContext,
        available_actions: list[str],
    ) -> RouteDecision:
        try:
            decision = self.primary.decide(state, context, available_actions)
            if decision.source is None:
                decision.source = "primary"
            return decision
        except Exception as exc:
            if hasattr(state, "add_note"):
                state.add_note(f"主路由失败，回退到备用路由: {exc}")
            decision = self.fallback.decide(state, context, available_actions)
            if decision.source is None or decision.source == "rule":
                decision.source = "fallback_rule"
            return decision
