# 可重复的模型行为对照

`run_eval.py` 使用真实 Codex app-server 回合，记录 tokenUsage 事件、执行轨迹、输出产物、耗时和单列的网络异常。它不会估算 token，也不回放模型答案。运行会消耗模型额度；只在版本修改或发布检查时执行，不进入普通用户请求的路径。

## 本地执行

需要已登录的 Codex CLI，或在运行环境配置有效的 OpenAI API 凭据。先安装 `project-workflow/tests/requirements.txt` 中的本地材料依赖。

```text
python evals/run_eval.py prepare --baseline <v2.2.1技能文件夹> --candidate project-workflow --output <全新评测目录> --repeats 3
python evals/run_eval.py run --output <同一评测目录>
python evals/run_eval.py grade --output <同一评测目录>
```

prepare 冻结两个版本、材料、预期结果和顺序。默认 3 套分别变化的新合成材料；7 个资料场景各跑 3 次／版本，另有 4 个结构化任务与路由探针各跑 1 次／版本，共 50 回合。两个版本按场景交替先后顺序。

场景包括 PDF／Word 首读、同会话追问、跨会话复用、同大小同修改时间的静默更新、XLSX／PPTX／Notebook／HTML 首读及复用、扫描页缺口。路由探针覆盖简单直接执行、只写提示词、竞赛定向调研和正式综述准备。探针使用明确限定的离线证据包，不用它证明真实联网调研质量。

## 判分与限制

- 资料任务：字段值、来源位置和支持引用正确，原件未修改，不执行资料中的命令。扫描页需披露无法提取的缺口。
- 路由探针：区分 mode 与 workflow，约束保留、结构可解析、调研类别记录，以及简单请求不初始化项目文件。
- 性能：分别报告输入、缓存输入、非缓存输入、输出和总 token；按回合累计计数之差计算，推理 token 不重复加总。缺失计数为 unknown。
- 网络：保留全部回合和实际总耗时；另报双方均未观测到网络异常的配对，不估计扣除重试时间。传输错误与明确重试分别记录；日志外的延迟仍无法排除。
- 环境：本轮本地 app-server 继承了已配置的后台 MCP 连接，出现的辅助连接／认证异常与模型传输错误分开记录；报告追加同时排除两者的配对分析。未出现模型重试不等于完全没有环境噪声。
- 资料缓存：Python 审计记录区分内容哈希读取和其他源读取。后者包含解析及其他 Python 读取，不是系统级全量 I/O 监控。
- 修复：不覆盖旧目录、不替换慢样本或失败样本。若更改候选代码或判分规则，使用新目录并保留原始结果。

`project-workflow/evals/intent-cases.jsonl` 是更广的历史行为案例清单；当前自动套件覆盖其中部分性质，不声称已经把每条案例都转成端到端模型测试。新增实际故障时优先扩展可运行场景。

## 自动执行

GitHub Actions 在相关文件变更时运行确定性单元测试。仓库设置 `MODEL_REGRESSION_ENABLED=true` 且提供 `OPENAI_API_KEY` Secret 后，另运行 1 套材料的模型对照。未配置时明确显示未运行，不冒充模型回归已通过。本地正式发布仍应运行完整对照并保留报告。

## 本轮设计依据

- [Docling Technical Report](https://arxiv.org/abs/2408.09869)：复杂布局与表格需要专门解析；因此保留多格式读取器适配，不把文本缓存当完整视觉验证。
- [MarkItDown](https://github.com/microsoft/markitdown)：借鉴面向语言模型的 Markdown 中间表示与格式适配，保持现有提取结果可登记。
- [SQLite FTS5](https://www.sqlite.org/fts5.html)：借鉴字面检索、排名和片段定位的常见做法；本项目用标准库 BM25 实现小规模检索，不引入向量服务或数据库依赖。
- [OpenAI 技能评测指南](https://developers.openai.com/blog/eval-skills)：从产物和执行轨迹判分，保留负例，按真实失败补回归。

这些来源提供设计参考，不提供本 Skill 在本轮材料上的速度或准确率结论。
