# Regex Cutting Lab

一个为 AstrBot 提供“正则流水线”能力的插件，支持对 **用户输入** 与 **AI 输出** 按配置顺序执行多条正则替换，实现任意文本裁剪、脱敏或重写。

## 功能特性

- **多规则流水线**：自定义多条正则，逐条依次执行，后面的规则基于前面规则的结果继续加工。
- **作用范围可选**：每条规则均可独立选择作用于用户输入、AI 输出或同时作用。
- **可视化排序**：在 WebUI 配置界面可通过拖动或调节顺序数字快速调整执行顺序。
- **标志位支持**：兼容 Python `re` 常用标志（如 `IGNORECASE`、`DOTALL` 等），复杂匹配不再受限。
- **安全处理消息链**：仅改写纯文本(`Plain`)组件，不会破坏图片、音频等富媒体消息。

## 快速上手

1. 在 AstrBot WebUI 打开插件管理，启用 **Regex Cutting Lab**。
2. 进入插件配置页，点击「新增规则」：
   - 填写名称，便于识别。
   - 选择作用范围（用户输入 / AI 输出 / 同时作用）。
   - 编写正则表达式 `pattern` 与 `replacement`。
   - 勾选所需的正则标志（如需要跨行匹配请选择 `DOTALL`）。
   - 设置顺序值（越小越先执行），或直接拖动列表中的辅助排序控件。
3. 保存配置后，新的正则流水线立即生效。

> 示例顺序效果：若规则 1 将所有文本替换为 `123`，规则 2 将 `123` 替换成 `1`，则按「规则 1 → 规则 2」执行的最终结果是 `1`；若顺序取反则输出为 `123`。

## 配置字段

插件的配置 Schema 位于 [`_conf_schema.json`](./_conf_schema.json)，WebUI 会自动渲染配置界面。核心字段如下：

| 字段 | 类型 | 默认值 | 说明 |
| :--- | :--- | :----- | :---- |
| `enabled` | `bool` | `true` | 是否启用插件。 |
| `rules` | `list<object>` | `[]` | 正则规则集合，支持新增/编辑/删除/排序。 |

### 单条规则

| 字段 | 类型 | 默认值 | 说明 |
| :--- | :--- | :----- | :---- |
| `name` | `string` | — | 规则名称。 |
| `scope` | `string` | `ai_output` | 取值：`user_input`、`ai_output`、`both`。 |
| `pattern` | `string` | — | 正则表达式（Python `re` 语法）。为空会跳过。 |
| `replacement` | `string` | `""` | 替换文本，可使用捕获组（如 `\1`）。 |
| `flags` | `list[string]` | `[]` | 正则标志，支持 `IGNORECASE`、`DOTALL`、`MULTILINE` 等。 |
| `order` | `int` | `100` | 执行顺序，值越小越靠前；拖动时会自动调整。 |
| `enabled` | `bool` | `true` | 是否启用该规则。 |

### 示例配置

```json
{
  "enabled": true,
  "rules": [
    {
      "name": "裁剪冗余前缀",
      "scope": "ai_output",
      "pattern": "(?s).*?最终答案：",
      "replacement": "",
      "flags": ["IGNORECASE", "DOTALL"],
      "order": 10,
      "enabled": true
    },
    {
      "name": "合并空白",
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

## 工作流程

1. 插件加载时预编译所有启用的规则，减少运行时开销。
2. `on_llm_request` 钩子裁剪用户输入，包括 prompt 与历史上下文中的文本段。
3. `on_llm_response` 钩子裁剪模型输出，优先处理消息链中的纯文本，若无消息链则回退到 `completion_text`。
4. 正则流水线严格按照顺序执行，后续规则将基于前一条规则的输出继续处理。

## 常见问题

- **顺序不起作用？** 请检查 `order` 字段，数值越小越先执行；确保保存后刷新页面查看最新排序。
- **匹配不到文本？** 校验正则表达式是否正确、是否需要 `DOTALL`/`MULTILINE` 等标志。
- **富媒体被破坏？** 插件不会改写非文本组件，如仍异常请检查其他插件或消息平台兼容性。
- **出现警告日志？** 当发现空 pattern、无效 scope 或正则编译失败时，插件会输出日志帮助快速定位问题。

## 许可证

本项目遵循 [MIT](https://opensource.org/license/mit/) 许可证发布。
