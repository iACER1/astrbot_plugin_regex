from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, register

_FLAG_NAME_MAP = {
    "ASCII": re.ASCII,
    "IGNORECASE": re.IGNORECASE,
    "LOCALE": re.LOCALE,
    "MULTILINE": re.MULTILINE,
    "DOTALL": re.DOTALL,
    "VERBOSE": re.VERBOSE,
    "UNICODE": re.UNICODE,
}

_VALID_SCOPES = {"user_input", "ai_output", "both"}


@dataclass(frozen=True)
class CompiledRegexRule:
    """缓存后的正则规则实体。"""

    name: str
    scope: str
    order: int
    pattern: re.Pattern[str]
    replacement: str


@register(
    "astrbot_plugin_regex",
    "Regex Pipeline",
    "按顺序对用户输入与模型输出执行可配置的正则管线。",
    "1.0.0",
)
class RegexPipeline(Star):
    """基于配置顺序对消息执行正则替换的插件。"""

    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config or AstrBotConfig()
        self._compiled_rules: Tuple[CompiledRegexRule, ...] = ()
        self._config_signature: Tuple[Tuple[str, str, str, str, Tuple[str, ...], bool, int], ...] = ()
        self._has_user_rules: bool = False
        self._has_ai_rules: bool = False

    async def initialize(self):
        """插件载入时尝试编译规则，便于首次生效。"""
        self._refresh_rules()

    def _is_enabled(self) -> bool:
        return bool(self.config.get("enabled", True))

    def _refresh_rules(self) -> None:
        """当配置发生变化时重新编译规则。"""
        raw_rules = self.config.get("rules", []) if self.config else []
        if not isinstance(raw_rules, list):
            logger.warning("RegexPipeline: rules 配置应为列表，已忽略非列表值。")
            raw_rules = []

        signature: List[Tuple[str, str, str, str, Tuple[str, ...], bool, int]] = []
        prepared_rules: List[Tuple[int, int, CompiledRegexRule]] = []

        for idx, item in enumerate(raw_rules):
            if not isinstance(item, dict):
                logger.warning("RegexPipeline: 第 %d 条规则格式错误，需为对象。", idx + 1)
                continue

            name = str(item.get("name") or f"Rule #{idx + 1}")
            scope = self._normalize_scope(item.get("scope"))
            pattern = str(item.get("pattern", ""))
            replacement = str(item.get("replacement", ""))
            order = self._coerce_int(item.get("order"), (idx + 1) * 10)
            flag_tokens = self._tokenize_flags(item.get("flags", []))
            enabled = bool(item.get("enabled", True))

            signature.append(
                (
                    name,
                    scope,
                    pattern,
                    replacement,
                    tuple(flag_tokens),
                    enabled,
                    order,
                )
            )

            if not enabled:
                continue

            if not pattern:
                logger.warning(
                    "RegexPipeline: 第 %d 条规则 pattern 为空，已跳过（名称：%s）。",
                    idx + 1,
                    name,
                )
                continue

            flag_value = self._flags_to_int(flag_tokens)
            try:
                compiled = re.compile(pattern, flag_value)
            except re.error as exc:
                logger.error(
                    "RegexPipeline: 第 %d 条规则编译失败，名称=%s，pattern=%r，错误=%s",
                    idx + 1,
                    name,
                    pattern,
                    exc,
                )
                continue

            prepared_rules.append(
                (
                    order,
                    idx,
                    CompiledRegexRule(
                        name=name,
                        scope=scope,
                        order=order,
                        pattern=compiled,
                        replacement=replacement,
                    ),
                )
            )

        signature_tuple = tuple(signature)
        if signature_tuple == self._config_signature:
            return

        prepared_rules.sort(key=lambda item: (item[0], item[1]))
        compiled_rules = tuple(rule for _, __, rule in prepared_rules)

        self._compiled_rules = compiled_rules
        self._config_signature = signature_tuple
        self._has_user_rules = any(
            rule.scope in ("user_input", "both") for rule in compiled_rules
        )
        self._has_ai_rules = any(
            rule.scope in ("ai_output", "both") for rule in compiled_rules
        )

        logger.info(
            "RegexPipeline: 已载入 %d 条可用规则（用户输入：%s / 模型输出：%s）。",
            len(compiled_rules),
            "是" if self._has_user_rules else "否",
            "是" if self._has_ai_rules else "否",
        )

    @filter.on_llm_request()
    async def on_llm_request(
        self, event: AstrMessageEvent, req: ProviderRequest
    ):  # noqa: D401
        """在调用 LLM 前处理用户输入。"""
        if not self._is_enabled():
            return

        self._refresh_rules()
        if not self._has_user_rules or not self._compiled_rules:
            return

        changed = False

        if isinstance(req.prompt, str) and req.prompt:
            new_prompt = self._apply_rules(req.prompt, target_scope="user_input")
            if new_prompt != req.prompt:
                req.prompt = new_prompt
                changed = True

        if self._apply_rules_to_contexts(req.contexts):
            changed = True

        if changed:
            logger.debug(
                "RegexPipeline: 已对会话 %s 的用户输入应用正则规则。",
                event.unified_msg_origin,
            )

    @filter.on_llm_response()
    async def on_llm_response(
        self, event: AstrMessageEvent, resp: LLMResponse
    ):  # noqa: D401
        """在 LLM 返回后处理模型输出。"""
        if not self._is_enabled():
            return

        self._refresh_rules()
        if not self._has_ai_rules or not self._compiled_rules:
            return

        chain_changed = self._apply_rules_to_chain(resp)
        text_changed = False

        if not chain_changed:
            text_changed = self._apply_rules_to_completion(resp)

        if chain_changed or text_changed:
            logger.debug(
                "RegexPipeline: 已对会话 %s 的模型输出应用正则规则。",
                event.unified_msg_origin,
            )

    def _apply_rules_to_chain(self, resp: LLMResponse) -> bool:
        if not resp.result_chain:
            return False

        changed = False
        new_chain = []

        for component in resp.result_chain.chain:
            if isinstance(component, Comp.Plain):
                original_text = component.text or ""
                updated_text = self._apply_rules(
                    original_text, target_scope="ai_output"
                )
                if updated_text != original_text:
                    changed = True
                new_chain.append(Comp.Plain(updated_text))
            else:
                new_chain.append(component)

        if changed:
            resp.result_chain.chain = new_chain
            resp._completion_text = resp.result_chain.get_plain_text()

        return changed

    def _apply_rules_to_completion(self, resp: LLMResponse) -> bool:
        original_text = resp.completion_text
        updated_text = self._apply_rules(original_text, target_scope="ai_output")
        if updated_text == original_text:
            return False

        resp.completion_text = updated_text
        return True

    def _apply_rules_to_contexts(self, contexts: list[dict] | None) -> bool:
        if not contexts:
            return False

        changed = False
        for message in contexts:
            if not isinstance(message, dict):
                continue
            if message.get("role") != "user":
                continue

            content = message.get("content")
            if isinstance(content, str):
                updated = self._apply_rules(content, target_scope="user_input")
                if updated != content:
                    message["content"] = updated
                    changed = True
            elif isinstance(content, list):
                for segment in content:
                    if not isinstance(segment, dict):
                        continue
                    if segment.get("type") != "text":
                        continue
                    text = segment.get("text", "")
                    updated_text = self._apply_rules(text, target_scope="user_input")
                    if updated_text != text:
                        segment["text"] = updated_text
                        changed = True

        return changed

    def _apply_rules(self, text: str, *, target_scope: str) -> str:
        result = text
        for rule in self._compiled_rules:
            if target_scope == "user_input":
                if rule.scope not in ("user_input", "both"):
                    continue
            elif target_scope == "ai_output":
                if rule.scope not in ("ai_output", "both"):
                    continue
            else:
                continue

            result = rule.pattern.sub(rule.replacement, result)

        return result

    @staticmethod
    def _normalize_scope(scope_value: object) -> str:
        if isinstance(scope_value, str):
            normalized = scope_value.strip().lower()
            if normalized in _VALID_SCOPES:
                return normalized
        logger.warning(
            "RegexPipeline: 未识别的 scope=%r，已回退为 ai_output。", scope_value
        )
        return "ai_output"

    @staticmethod
    def _tokenize_flags(raw_flags: object) -> List[str]:
        tokens: List[str] = []

        if isinstance(raw_flags, str):
            parts = re.split(r"[|,]+", raw_flags)
            tokens = [part.strip().upper() for part in parts if part.strip()]
        elif isinstance(raw_flags, Iterable):
            tokens = [
                str(flag).strip().upper()
                for flag in raw_flags  # type: ignore[arg-type]
                if str(flag).strip()
            ]

        return tokens

    @staticmethod
    def _flags_to_int(flag_tokens: Sequence[str]) -> int:
        value = 0
        for token in flag_tokens:
            flag = _FLAG_NAME_MAP.get(token.upper())
            if flag is None:
                logger.warning("RegexPipeline: 未识别的正则标志 `%s`，已忽略。", token)
                continue
            value |= flag
        return value

    @staticmethod
    def _coerce_int(value: object, fallback: int) -> int:
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return fallback
