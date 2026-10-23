# 可审计的任务提取与流程选择

用户要求可审计记录、跨阶段交接，或复杂任务存在多项约束时，按 `assets/task-contract.schema.json` 保存一份简短 JSON。沿用项目已有同类状态；普通单步请求只在当前上下文整理，不额外写文件，不增加模型调用。

记录的是任务约定与来源，不是思维链：`goal`、`mode`、`workflow`、`constraints`、`acceptance`、`sources`、`assumptions`、`unknowns`、`research`。明确要求、可逆假设和未知项分开；只改用户变更的字段，保留其余有效约束。来源用真实路径或实际读取的链接。空数组表示没有可记录项，不能填造假的来源或预算。

`mode`、`workflow` 必须使用下列原样字符串，不翻译枚举，不把流程说明填进字段。说明写入 `goal`、`constraints` 或 `acceptance`。按 schema 校验最终记录；用户明确要求的额外字段沿用其交付约定。

| 字段 | 允许值 |
| --- | --- |
| `mode` | `execute`、`discuss`、`options`、`prompt_only` |
| `workflow` | `direct`、`start`、`execute`、`resume`、`research_targeted`、`research_review`、`prompt_only`、`close`、`redirect` |

明确的单步执行使用 `execute`／`direct`；只交付提示词使用 `prompt_only`／`prompt_only`。提出或比较方案时，`research.needed=true`，记录相关论文、开源实现、常见做法和适配边界；某类来源确实不适用、检索无结果或不可访问时，留空并在 `limits` 说明实际情况。不用凑来源数量冒充完成调研。

记录后继续原本已授权的工作，不要求用户批准一份自动生成的 JSON。工具校验只能判断结构，约束保真与流程是否合适仍通过实际行为回归来验证。
