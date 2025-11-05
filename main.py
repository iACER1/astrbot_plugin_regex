from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse
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


@dataclass(frozen=True)
class CompiledRegexRule:
    """已编译的正则替换规则。"""

    pattern: re.Pattern[str]
    replacement: str


@register(
    "astrbot_plugin_regex",
    "Regex Output Cutter",
    "按顺序应用多条正则规则裁剪大模型输出。",
    "1.0.0",
)
class RegexOutputCutter(Star):
    """按配置顺序裁剪 LLM 输出内容的插件。"""

    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config or AstrBotConfig()
        self._compiled_rules: List[CompiledRegexRule] = []
        self._config_signature: Tuple[Tuple[str, str, Tuple[str, ...], bool], ...] = ()

    async def initialize(self):
        """初始化时预编译一次配置中的正则。"""
        self._refresh_rules()

    def _refresh_rules(self) -> None:
        """检查配置是否发生变化并更新正则缓存。"""
        raw_rules = self.config.get("regex_rules", []) if self.config else []
        if not isinstance(raw_rules, list):
            logger.warning("RegexCutter: regex_rules 配置应为列表，已忽略。")
            raw_rules = []

        signature: List[Tuple[str, str, Tuple[str, ...], bool]] = []
        normalized_rules: List[dict] = []

        for idx, item in enumerate(raw_rules):
            if not isinstance(item, dict):
                logger.warning("RegexCutter: 第 %d 条规则格式错误，应为对象。", idx + 1)
                continue

            pattern = str(item.get("pattern", ""))
            replacement = str(item.get("replacement", ""))
            flag_tokens = self._tokenize_flags(item.get("flags", []))
            enabled = bool(item.get("enabled", True))

            signature.append((pattern, replacement, tuple(flag_tokens), enabled))
            normalized_rules.append(
                {
                    "pattern": pattern,
                    "replacement": replacement,
                    "flags": flag_tokens,
                    "enabled": enabled,
                    "index": idx + 1,
                }
            )

        signature_tuple = tuple(signature)
        if signature_tuple == self._config_signature:
            return

        compiled_rules: List[CompiledRegexRule] = []
        for rule in normalized_rules:
            if not rule["enabled"]:
                continue

            pattern = rule["pattern"]
            if not pattern:
                logger.warning(
                    "RegexCutter: 第 %d 条规则的 pattern 为空，已跳过。",
                    rule["index"],
                )
                continue

            flag_value = self._flags_to_int(rule["flags"])
            try:
                compiled = re.compile(pattern, flag_value)
            except re.error as exc:
                logger.error(
                    "RegexCutter: 第 %d 条规则编译失败，pattern=%r，错误=%s",
                    rule["index"],
                    pattern,
                    exc,
                )
                continue

            compiled_rules.append(
                CompiledRegexRule(pattern=compiled, replacement=rule["replacement"])
            )

        self._compiled_rules = compiled_rules
        self._config_signature = signature_tuple

    def _tokenize_flags(self, raw_flags: object) -> List[str]:
        """将配置中的 flags 字段解析为统一的大写标志列表。"""
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
        else:
            tokens = []

        return tokens

    def _flags_to_int(self, flag_tokens: Sequence[str]) -> int:
        """将标志名称列表转换为 re 标志位。"""
        value = 0
        for token in flag_tokens:
            flag = _FLAG_NAME_MAP.get(token.upper())
            if flag is None:
                logger.warning("RegexCutter: 未识别的正则标志 `%s`，已忽略。", token)
                continue
            value |= flag
        return value

    def _apply_rules(self, text: str) -> str:
        """依次应用所有正则规则。"""
        result = text
        for rule in self._compiled_rules:
            result = rule.pattern.sub(rule.replacement, result)
        return result

    def _apply_to_chain(self, resp: LLMResponse) -> bool:
        """对消息链中的 Plain 组件应用正则替换。"""
        if not resp.result_chain:
            return False

        changed = False
        new_chain = []
        for component in resp.result_chain.chain:
            if isinstance(component, Comp.Plain):
                updated_text = self._apply_rules(component.text or "")
                if updated_text != component.text:
                    changed = True
                new_chain.append(Comp.Plain(updated_text))
            else:
                new_chain.append(component)

        if changed:
            resp.result_chain.chain = new_chain
            resp._completion_text = resp.result_chain.get_plain_text()

        return changed

    def _apply_to_text(self, resp: LLMResponse) -> bool:
        """对纯文本响应应用正则替换。"""
        original_text = resp.completion_text
        updated_text = self._apply_rules(original_text)
        if updated_text == original_text:
            return False

        resp.completion_text = updated_text
        return True

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, resp: LLMResponse):
        """在 LLM 响应返回后执行正则裁剪。"""
        if not self.config.get("enable", True):
            return

        self._refresh_rules()
        if not self._compiled_rules:
            return

        chain_changed = self._apply_to_chain(resp)
        if not chain_changed:
            self._apply_to_text(resp)
