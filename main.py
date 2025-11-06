from __future__ import annotations

import re
import json
from dataclasses import dataclass
from typing import Iterable, List, Sequence

import astrbot.api.message_components as message_components
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, register

_SCOPE_OPTIONS = {"user_input", "ai_output", "both"}

_FLAG_SYMBOLS = {
    "ASCII": re.ASCII,
    "IGNORECASE": re.IGNORECASE,
    "LOCALE": re.LOCALE,
    "MULTILINE": re.MULTILINE,
    "DOTALL": re.DOTALL,
    "VERBOSE": re.VERBOSE,
    "UNICODE": re.UNICODE,
}


@dataclass(slots=True)
class RuntimeRegexRule:
    """在运行期间使用的正则规则实体。"""

    identifier: str
    scope: str
    order: int
    compiled: re.Pattern[str]
    replacement: str
    origin_index: int

    def applies_to(self, target_scope: str) -> bool:
        if target_scope == "user_input":
            return self.scope in {"user_input", "both"}
        if target_scope == "ai_output":
            return self.scope in {"ai_output", "both"}
        return False


@register(
    "astrbot_plugin_regex",
    "RegexCuttingLab",
    "基于有序正则流水线裁剪用户输入与模型输出文本内容。",
    "1.0.0",
    "https://github.com/iACER1/astrbot_plugin_regex",
)
class RegexCuttingLab(Star):
    """按照配置顺序执行正则替换的插件。"""

    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config or AstrBotConfig()
        self._compiled_rules: tuple[RuntimeRegexRule, ...] = ()
        self._signature: tuple[tuple, ...] = ()
        self._has_user_scoped_rules = False
        self._has_ai_scoped_rules = False

    async def initialize(self) -> None:  # noqa: D401
        """插件加载完成后预编译一次规则。"""
        self._ensure_rules()

    def _is_enabled(self) -> bool:
        return bool(self.config.get("enabled", True))

    def _ensure_rules(self) -> None:
        """根据配置构建正则流水线，避免重复编译。"""
        raw_rules: list = []
        if self.config:
            # 优先使用 rules_json（JSON 文本），提供更友好的代码编辑器界面
            rules_json_text = self.config.get("rules_json")
            if isinstance(rules_json_text, str) and rules_json_text.strip():
                try:
                    parsed = json.loads(rules_json_text)
                    if isinstance(parsed, list):
                        raw_rules = parsed
                    else:
                        logger.warning("RegexCuttingLab: `rules_json` 不是 JSON 数组，已忽略。")
                except Exception as exc:
                    logger.error("RegexCuttingLab: 解析 `rules_json` 失败：%s", exc)

            # 兼容旧版 list<object> 规则字段
            if not raw_rules:
                legacy_rules = self.config.get("rules", [])
                if isinstance(legacy_rules, list):
                    raw_rules = legacy_rules
                else:
                    logger.warning(
                        "RegexCuttingLab: `rules` 字段应为列表，实际类型为 %s，已忽略。",
                        type(legacy_rules),
                    )
                    raw_rules = []

        signature: List[tuple] = []
        candidates: List[RuntimeRegexRule] = []

        for index, item in enumerate(raw_rules):
            if not isinstance(item, dict):
                logger.warning(
                    "RegexCuttingLab: 第 %d 条规则不是对象，已跳过。", index + 1
                )
                continue

            name = str(item.get("name") or f"Rule {index + 1}")
            scope = self._normalize_scope(item.get("scope"))
            pattern_text = item.get("pattern", "")
            replacement_raw = item.get("replacement", "")
            replacement_text = "" if replacement_raw is None else str(replacement_raw)
            order_value = self._safe_int(item.get("order"), (index + 1) * 100)
            flag_tokens = self._normalize_flags(item.get("flags", []))
            enabled = bool(item.get("enabled", True))

            signature.append(
                (
                    name,
                    scope,
                    str(pattern_text),
                    replacement_text,
                    tuple(flag_tokens),
                    enabled,
                    order_value,
                )
            )

            if not enabled:
                continue

            if not pattern_text:
                logger.warning(
                    "RegexCuttingLab: 规则 %s 的 pattern 为空，已跳过。", name
                )
                continue

            try:
                compiled_pattern = re.compile(
                    str(pattern_text), self._flags_to_value(flag_tokens)
                )
            except re.error as exc:
                logger.error(
                    "RegexCuttingLab: 正则规则 %s 编译失败（pattern=%r）：%s",
                    name,
                    pattern_text,
                    exc,
                )
                continue

            candidates.append(
                RuntimeRegexRule(
                    identifier=name,
                    scope=scope,
                    order=order_value,
                    compiled=compiled_pattern,
                    replacement=replacement_text,
                    origin_index=index,
                )
            )

        signature_tuple = tuple(signature)
        if signature_tuple == self._signature:
            return

        candidates.sort(key=lambda rule: (rule.order, rule.origin_index))
        self._compiled_rules = tuple(candidates)
        self._signature = signature_tuple
        self._has_user_scoped_rules = any(
            rule.applies_to("user_input") for rule in self._compiled_rules
        )
        self._has_ai_scoped_rules = any(
            rule.applies_to("ai_output") for rule in self._compiled_rules
        )

        logger.info(
            "RegexCuttingLab: 已载入 %d 条规则（用户输入：%s / 模型输出：%s）。",
            len(self._compiled_rules),
            "是" if self._has_user_scoped_rules else "否",
            "是" if self._has_ai_scoped_rules else "否",
        )

    @filter.on_llm_request()
    async def on_llm_request(  # noqa: D401
        self, event: AstrMessageEvent, request: ProviderRequest
    ) -> None:
        """在请求 LLM 前裁剪用户输入。"""
        if not self._is_enabled():
            return

        self._ensure_rules()
        if not self._has_user_scoped_rules:
            return

        updated = False

        if isinstance(request.prompt, str) and request.prompt:
            transformed_prompt = self._run_pipeline(
                request.prompt, target_scope="user_input"
            )
            if transformed_prompt != request.prompt:
                request.prompt = transformed_prompt
                updated = True

        if self._apply_to_context_messages(request.contexts):
            updated = True

        if updated:
            logger.debug(
                "RegexCuttingLab: 会话 %s 的用户输入已按规则裁剪。",
                event.unified_msg_origin,
            )

    @filter.on_llm_response()
    async def on_llm_response(  # noqa: D401
        self, event: AstrMessageEvent, response: LLMResponse
    ) -> None:
        """在获取到 LLM 响应后裁剪模型输出。"""
        if not self._is_enabled():
            return

        self._ensure_rules()
        if not self._has_ai_scoped_rules:
            return

        chain_changed = self._apply_to_result_chain(response)
        text_changed = False if chain_changed else self._apply_to_completion_text(response)

        if chain_changed or text_changed:
            logger.debug(
                "RegexCuttingLab: 会话 %s 的模型输出已按规则裁剪。",
                event.unified_msg_origin,
            )

    def _apply_to_result_chain(self, response: LLMResponse) -> bool:
        """对消息链中的纯文本组件应用正则流水线。"""
        chain = response.result_chain
        if not chain:
            return False

        mutated = False
        new_components: List[message_components.BaseMessageComponent] = []

        for component in chain.chain:
            if isinstance(component, message_components.Plain):
                original_text = component.text or ""
                transformed_text = self._run_pipeline(
                    original_text, target_scope="ai_output"
                )
                if transformed_text != original_text:
                    mutated = True
                new_components.append(message_components.Plain(transformed_text))
            else:
                new_components.append(component)

        if mutated:
            chain.chain = new_components
            response._completion_text = chain.get_plain_text()

        return mutated

    def _apply_to_completion_text(self, response: LLMResponse) -> bool:
        """当消息链不存在时，回退裁剪 completion_text。"""
        original_text = response.completion_text
        transformed_text = self._run_pipeline(original_text, target_scope="ai_output")
        if transformed_text == original_text:
            return False

        response.completion_text = transformed_text
        return True

    def _apply_to_context_messages(self, contexts: list[dict] | None) -> bool:
        """遍历上下文中的用户消息并执行裁剪。"""
        if not contexts:
            return False

        mutated = False
        for message in contexts:
            if not isinstance(message, dict):
                continue
            if message.get("role") != "user":
                continue

            content = message.get("content")
            if isinstance(content, str):
                transformed = self._run_pipeline(content, target_scope="user_input")
                if transformed != content:
                    message["content"] = transformed
                    mutated = True
            elif isinstance(content, list):
                for segment in content:
                    if not isinstance(segment, dict):
                        continue
                    if segment.get("type") != "text":
                        continue
                    text_value = segment.get("text", "")
                    transformed_segment = self._run_pipeline(
                        text_value, target_scope="user_input"
                    )
                    if transformed_segment != text_value:
                        segment["text"] = transformed_segment
                        mutated = True

        return mutated

    def _run_pipeline(self, text: str, *, target_scope: str) -> str:
        """按顺序应用匹配 target_scope 的规则。"""
        result = text
        for rule in self._compiled_rules:
            if rule.applies_to(target_scope):
                result = rule.compiled.sub(rule.replacement, result)
        return result

    @staticmethod
    def _normalize_scope(raw_scope: object) -> str:
        if isinstance(raw_scope, str):
            normalized = raw_scope.strip().lower()
            if normalized in _SCOPE_OPTIONS:
                return normalized
        if raw_scope is not None:
            logger.warning(
                "RegexCuttingLab: 未识别的 scope=%r，已自动回退为 ai_output。",
                raw_scope,
            )
        return "ai_output"

    @staticmethod
    def _normalize_flags(raw_flags: object) -> List[str]:
        if isinstance(raw_flags, str):
            tokens = [
                token.strip().upper()
                for token in re.split(r"[|,\s]+", raw_flags)
                if token.strip()
            ]
            return tokens

        if isinstance(raw_flags, Iterable):
            tokens: List[str] = []
            for token in raw_flags:  # type: ignore[assignment]
                normalized = str(token).strip().upper()
                if normalized:
                    tokens.append(normalized)
            return tokens

        return []

    @staticmethod
    def _flags_to_value(tokens: Sequence[str]) -> int:
        value = 0
        for token in tokens:
            flag_value = _FLAG_SYMBOLS.get(token)
            if flag_value is None:
                logger.warning(
                    "RegexCuttingLab: 未识别的正则标志 `%s`，已忽略。", token
                )
                continue
            value |= flag_value
        return value

    @staticmethod
    def _safe_int(value: object, fallback: int) -> int:
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return fallback
