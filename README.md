# Regex Output Cutter

一个按顺序执行多条正则替换规则的 AstrBot 插件，可用于在消息发出前裁剪或重写 LLM 的输出。

## 功能特性

- 支持自定义多条正则规则，并按照配置中的顺序依次执行。
- 每条规则都可单独启用或禁用，互不影响。
- 支持常用 Python `re` 标志（如 `IGNORECASE`、`DOTALL` 等）。
- 同时兼容纯文本回复与包含富媒体的消息链，仅会修改其中的文本段落。

## 配置说明

插件配置 Schema 位于 [`_conf_schema.json`]，可在 WebUI 中进行可视化编辑。主要字段如下：

| 配置项 | 类型 | 默认值 | 说明 |
| :----- | :--- | :----- | :---- |
| `enable` | `bool` | `true` | 控制是否启用插件裁剪功能。 |
| `regex_rules` | `list` | `[]` | 依次执行的正则替换规则列表。 |
| `regex_rules[].pattern` | `string` | — | Python 正则表达式模式，留空会被忽略。 |
| `regex_rules[].replacement` | `string` | `""` | 替换文本，可结合捕获组引用。 |
| `regex_rules[].flags` | `list[string]` | `[]` | 可选的正则标志，例如 `IGNORECASE`、`DOTALL`、`MULTILINE` 等。 |
| `regex_rules[].enabled` | `bool` | `true` | 该条规则是否启用。 |

### 示例配置

```json
{
  "enable": true,
  "regex_rules": [
    {
      "pattern": "(?s).*?最终答案：",
      "replacement": "",
      "flags": ["IGNORECASE", "DOTALL"],
      "enabled": true
    },
    {
      "pattern": "\\s+",
      "replacement": " ",
      "flags": ["MULTILINE"],
      "enabled": true
    }
  ]
}
```

## 工作原理

插件在 [`on_llm_response()`] 钩子中拦截模型返回结果并执行以下流程：

1. 根据配置预编译所有启用的正则规则，避免重复编译造成的性能损耗。
2. 优先对消息链中的 `Plain` 组件执行替换，以保持图片、文件等富媒体段落不受影响。
3. 若消息链不存在或未发生改变，则回退到对 `completion_text` 执行替换，确保纯文本响应同样被处理。
4. 所有规则均按配置顺序依次执行，后续规则基于前一规则的输出继续处理，从而实现有序裁剪。

## 常见问题

- **规则未生效？** 请确认模式字符串是否为空、规则是否启用，以及是否需要添加 `DOTALL` 等标志以匹配多行文本。
- **富媒体会被破坏吗？** 插件仅操作 `Plain` 文本段，其他消息组件会保持原样。
- **顺序很重要！** 如果结果仍不符合预期，可调整规则顺序或拆分为更多小粒度规则。

## 许可证

本项目使用 [MIT] 许可证。
