# Regex Pipeline

一个用于在 AstrBot 中按顺序执行多条正则规则的插件，可同时作用于「用户输入」与「模型输出」，帮助你裁剪或重写任意文本内容。

## 功能特性

- **多条正则管线**：支持配置任意数量的正则规则，并按照顺序依次执行。
- **作用范围可选**：每条规则可选择作用于用户输入、模型输出或两者。
- **灵活排序**：提供显式的 `order` 字段，顺序值越小越先执行，支持拖动或直接修改数值。
- **常见标志支持**：兼容 Python `re` 库的常用标志（如 `IGNORECASE`、`DOTALL` 等）。
- **消息链兼容**：自动只修改模型输出中的纯文本(`Plain`)组件，不会破坏图片、文件等富媒体内容。

## 配置说明

插件的配置 Schema 位于 [`_conf_schema.json`](./_conf_schema.json)。在 AstrBot WebUI 的插件配置界面中可视化管理这些字段：

| 字段 | 类型 | 默认值 | 说明 |
| :--- | :--- | :----- | :---- |
| `enabled` | `bool` | `true` | 是否启用整个插件。 |
| `rules` | `list<object>` | `[]` | 按顺序执行的正则规则集合。 |

### 单条规则字段

| 字段 | 类型 | 默认值 | 说明 |
| :--- | :--- | :----- | :---- |
| `name` | `string` | — | 规则名称，便于识别。 |
| `scope` | `string` | `ai_output` | 作用范围，取值：`user_input`、`ai_output`、`both`。 |
| `pattern` | `string` | — | 正则表达式模式（Python `re` 语法）。为空会跳过。 |
| `replacement` | `string` | `""` | 替换文本，可使用捕获组引用，如 `\1`。 |
| `flags` | `list[string]` | `[]` | 正则标志，支持 `IGNORECASE`、`DOTALL`、`MULTILINE` 等。 |
| `order` | `int` | `100` | 执行顺序，越小的值越先执行。 |
| `enabled` | `bool` | `true` | 是否启用该规则。 |

### 示例配置

```json
{
  "enabled": true,
  "rules": [
    {
      "name": "裁剪开头提示",
      "scope": "ai_output",
      "pattern": "(?s).*?最终答案：",
      "replacement": "",
      "flags": ["IGNORECASE", "DOTALL"],
      "order": 10,
      "enabled": true
    },
    {
      "name": "标准化空白",
      "scope": "both",
      "pattern": "\\s+",
      "replacement": " ",
      "flags": ["MULTILINE"],
      "order": 20,
      "enabled": true
    }
  ]
}
```

## 工作原理

1. 插件加载时预编译所有启用的规则，并按 `order` 和配置顺序排序。
2. 在 `on_llm_request` 钩子中处理用户输入：同时改写 `ProviderRequest.prompt` 以及上下文中的文本段。
3. 在 `on_llm_response` 钩子中处理模型输出：优先改写消息链中的纯文本，若无文本链则回退到 `completion_text`。
4. 多条规则按顺序依次作用，后续规则基于前一次输出，确保顺序调整会带来不同结果。

## 常见问题

- **规则顺序不生效？** 请确认 `order` 数值及排列顺序，数值越小越先执行。
- **没有命中文本？** 检查 `pattern`、`flags` 是否正确，例如跨行匹配需要 `DOTALL`。
- **富媒体被破坏？** 插件仅对纯文本段做替换，其他组件保持原样。
- **控制台警告？** 当发现空 `pattern`、无效 `scope` 或无法编译的正则时，会输出警告日志以便排查。

## 许可证

本项目遵循 [MIT](https://opensource.org/license/mit/) 许可证发布。
