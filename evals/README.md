# 可重复的模型行为对照

`run_eval.py` 使用真实 Codex app-server 回合，记录 tokenUsage 事件、执行轨迹、输出产物、耗时和单列的网络异常。它不会估算 token，也不回放模型答案。运行会消耗模型额度；只在版本修改或发布检查时执行，不进入普通用户请求的路径。

## 本地执行

需要已登录的 Codex CLI，或在运行环境配置有效的 OpenAI API 凭据。先安装 `project-workflow/tests/requirements.txt` 中的本地材料依赖。

```text
python evals/run_eval.py prepare --baseline <稳定基线技能文件夹> --candidate project-workflow --output <全新评测目录> --repeats 3 --material-offset 4 --strict-performance
python evals/run_eval.py run --output <同一评测目录>
python evals/run_eval.py grade --output <同一评测目录>
```

prepare 冻结两个版本、材料、预期结果和顺序。默认 3 套分别变化的新合成材料；7 个资料场景各跑 3 次／版本，另有 4 个结构化任务与路由探针各跑 1 次／版本，共 50 回合。两个版本按场景交替先后顺序。

v2.5.4 的发布队列在任何模型回合之前，另预登记两个独立材料集，只测 D5／D6（`--repeats 2 --material-offset 27 --stages D5 D6 --strict-performance`），主队列结束后串行增加 8 回合。主队列使用 `--material-offset 24 --order-offset 1`，预先将首组 T1、D5、D6 排在其余任务之前，以尽早验证已知枚举与来源恢复故障。最终共 58 回合：D5、D6 各 5 对，其余资料场景各 3 对，路由各 1 对；全部保留，不按是否通过继续增加样本。两个队列分别冻结并运行，再用合并器汇总。此前候选的服务中断、机器枚举及来源恢复失败记录均保留，未与本候选混为一组。v2.5.3 完整58回合功能均通过，但复用非缓存输入回退，原判定与报告保留。

场景包括 PDF／Word 首读、同会话追问、跨会话复用、同大小同修改时间的静默更新、XLSX／PPTX／Notebook／HTML 首读及复用、扫描页缺口。路由探针覆盖简单直接执行、只写提示词、竞赛定向调研和正式综述准备。探针使用明确限定的离线证据包，不用它证明真实联网调研质量。

## 判分与限制

- 资料任务：字段值、来源位置和支持引用正确，原件未修改，不执行资料中的命令。扫描页需披露无法提取的缺口。
- 路由探针：区分 mode 与 workflow，约束保留、结构可解析、调研类别记录，以及简单请求不初始化项目文件。
- 性能：分别报告输入、缓存输入、非缓存输入、输出和总 token；按回合累计计数之差计算，推理 token 不重复加总。缺失计数为 unknown。
- 网络：保留全部回合和实际总耗时；另报双方均未观测到网络异常的配对，不估计扣除重试时间。传输错误与明确重试分别记录；日志外的延迟仍无法排除。
- 环境：评测子进程将已配置的 MCP 和提供桌面工具的插件设为 disabled，保留格式技能；从有效配置及线程服务清单确认覆盖生效。只修改子进程参数，不改日常配置。工具清单可能保留已禁用服务的目录项，不把其行数当活动连接数。未知活动服务会停止该测试，不假称隔离成功。
- 网络：报告继续单列模型重试与辅助连接／认证事件。即使隔离后台 MCP，也无法排除提供方提示缓存、负载和日志外网络延迟。
- 后台目录刷新：`codex_models_manager` 的模型列表刷新错误另写 `background-events.json`，不当成MCP或模型采样重试；补充排除可关联刷新异常的复用配对分析，不改原发布门槛。只有错误结束时间可关联，缺少完整请求时段，未关联不证明没有后台干扰。
- 服务错误：同时读取 app-server 的 error 通知，记录 serverOverloaded／额度错误等类别；不把失败自动当作重试，也不重复计入 turn/completed 的相同错误。`run --stop-on-service-error` 在服务不可用时结束并保留部分队列，不替换已完成样本。重新测试须使用新目录，旧用量与被中断回合仍留档。
- 资料缓存：Python 审计记录区分内容哈希读取和其他源读取。后者包含解析及其他 Python 读取，不是系统级全量 I/O 监控。
- 修复：不覆盖旧目录、不替换慢样本或失败样本。若更改候选代码或判分规则，使用新目录并保留原始结果。
- 运行中断：计时跳变、缺失计数及其影响的同线程回合保留原始记录，用 `operational-exclusions.json` 记录配对排除原因；不把它们当作模型内容误答，也不估计扣除暂停时间。需要补足场景时，在新目录预登记受影响阶段（`--stages D1 D2`、`--order-offset 1`），保持同一冻结候选，增加新回合；不得替换旧样本。
- 记录恢复：空白 I/O 日志行不应中断记录，非空坏记录仍报错。模型已完成且原始轨迹、答案与累计用量齐全时，可由实际记录恢复而不重跑；丢失的外层秒表保持未知。另从所有回合的 `turn/completed.durationMs` 统一提取远端计时，包含模型、工具与重试，不含线程启动，与外层秒表分列。原计划尚未执行的回合可在新目录通过 `workspace_overrides` 复用原工作路径和缓存；必须留原记录、固定材料与续跑映射，不换样本。该字段只用于受控评测续跑。
- 合并：`python evals/merge_runs.py <主试验目录> <补充试验目录> --output <全新分析目录>`。合并器先校验模型设置及技能快照一致，重编号追加回合并记录来源，不修改原始目录。完成后在分析目录运行 grade 和 report。合并元数据的单元测试不属于模型性能样本。

`project-workflow/evals/intent-cases.jsonl` 是更广的历史行为案例清单；当前自动套件覆盖其中部分性质，不声称已经把每条案例都转成端到端模型测试。新增实际故障时优先扩展可运行场景。

## 自动执行

GitHub Actions 在相关文件变更时运行确定性单元测试。仓库设置 `MODEL_REGRESSION_ENABLED=true` 且提供 `OPENAI_API_KEY` Secret 后，另运行 1 套材料的模型对照。未配置时明确显示未运行，不冒充模型回归已通过。本地正式发布仍应运行完整对照并保留报告。

`--strict-performance` 适用于本次发布：首读D5和复用D6各至少3个正确配对、2个未观测模型传输异常的配对，两种口径的非缓存输入比值中位数均低于1；新版完整缓存打印与手写哈希命令线索为0，且MCP隔离核验通过。时间如实报告，不要求每个请求都更快。云端单套材料只作为正确性冒烟回归，不冒充三套材料的发布性能验证。

## 本轮设计依据

- [Docling Technical Report](https://arxiv.org/abs/2408.09869)：复杂布局与表格需要专门解析；因此保留多格式读取器适配，不把文本缓存当完整视觉验证。
- [MarkItDown](https://github.com/microsoft/markitdown)：借鉴面向语言模型的 Markdown 中间表示与格式适配，保持现有提取结果可登记。
- [SQLite FTS5](https://www.sqlite.org/fts5.html)：借鉴字面检索、排名和片段定位的常见做法；本项目用标准库 BM25 实现小规模检索，不引入向量服务或数据库依赖。
- [OpenAI 技能评测指南](https://developers.openai.com/blog/eval-skills)：从产物和执行轨迹判分，保留负例，按真实失败补回归。

这些来源提供设计参考，不提供本 Skill 在本轮材料上的速度或准确率结论。

本轮另参考 [Lost in the Middle 的摘要](https://arxiv.org/abs/2307.03172)、[Anthropic 工具设计实践](https://www.anthropic.com/engineering/writing-tools-for-agents) 和 [Codex 配置参考](https://developers.openai.com/codex/config-reference/)；分别用于无关上下文控制、紧凑工具返回和进程级 MCP 隔离。论文模型和工程环境与本项目不同，效果仍由本轮真实运行判定。

v2.5.1 按 [SQLite FTS5 的词干处理说明](https://www.sqlite.org/fts5.html#the_porter_tokenizer) 借鉴英语词形归一的做法；本项目只实现轻量复数归一，保留严格字面匹配，没有复刻完整Porter，也不宣称支持全部语言或语义改写。

v2.5.3 的来源集合恢复参考 [LongMemEval 摘要](https://arxiv.org/abs/2410.10813)、[官方开源评测的来源会话标识](https://github.com/xiaowu0162/LongMemEval)及 [Anthropic 的轻量标识与按需上下文实践](https://www.anthropic.com/research/effective-context-engineering-for-ai-agents)。本项目把这一思路用于从现有产物 sources 恢复文件集合；这是适配推断，未引入其完整记忆系统，也未运行或借用其基准性能。
