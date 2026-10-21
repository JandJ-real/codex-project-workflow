# Codex Project Workflow

一个可复用的 Codex Skill：把用户偏口语的项目需求整理成可执行任务，并用轻量状态、分阶段范围、文档缓存和适度验证推进工作。

当前版本：**v2.4.0**

## 主要能力

- 从口语化输入中提取目标、约束、交付物和验收标准，再生成适合当前任务的完整提示词。
- 根据项目阶段选择初始化、继续执行、调研、重定向、收尾或仅生成提示词等工作模式。
- 复杂资料首次读取后保存可检索表示；内置 PDF、DOCX、XLSX、PPTX、Notebook、HTML、邮件、CSV 等文本视图，其他格式可登记专用技能已生成的 OCR／描述／转录与索引。
- 用源内容及缓存正文 SHA-256 校验版本，分开记录覆盖范围、已知缺口和语义／视觉验证状态。
- 用有输出预算的跨文档检索返回片段、定位、已返回／被截断／未命中的字段；支持字面锚点、中文切分和本地 BM25 排序。
- 复杂任务或要求审计时保存结构化任务约定；简单请求不额外写文件或调用模型。
- 区分竞赛、工程和科研调研：竞赛优先官方规则与强基线，科研按需进入正式文献检索与证据链流程。
- 按风险选择验证强度，在关键边界上检查，同时避免无目的地重复检查。
- 每次提出或比较方案时参考相关论文、开源实现和常见做法，说明适配条件与边界。

## 仓库结构

```text
project-workflow/
├── SKILL.md
├── agents/
├── assets/
├── evals/
├── references/
├── scripts/
└── tests/
```

技能入口是 `project-workflow/SKILL.md`。详细模式和规则按需放在 `references/` 中，文档缓存工具位于 `scripts/document_cache.py`。

## 安装

克隆仓库后，将 `project-workflow` 文件夹复制到 Codex 的 Skills 目录。

Windows PowerShell：

```powershell
git clone https://github.com/JandJ-real/codex-project-workflow.git
New-Item -ItemType Directory -Force "$HOME\.codex\skills\project-workflow" | Out-Null
Copy-Item -Path .\codex-project-workflow\project-workflow\* -Recurse -Force -Destination "$HOME\.codex\skills\project-workflow"
```

配置了 `CODEX_HOME` 时，将目标改为该目录下的 `skills/project-workflow`。更新已有安装时复制文件夹内容，避免多嵌套一层同名目录。

如果使用文档缓存脚本，请安装其 Python 依赖：

```powershell
python -m pip install -r .\codex-project-workflow\project-workflow\scripts\requirements.txt
```

需要内置 XLSX／PPTX 读取时，将上面的依赖文件改为 `scripts/requirements-formats.txt`。图像 OCR、音视频转录和其他专业格式复用相应技能的结果；本 Skill 不内置所有专业解析器，也不执行文档里的代码、宏或公式。

重启 Codex 或开始一个新会话后，Skill 会参与自动匹配；也可以显式输入 `$project-workflow` 调用。

## 使用示例

直接用自然语言描述工作即可，例如：

```text
帮我做一个比赛项目，先读规则和数据说明，整理执行计划，然后开始实现。不要做完整论文综述。
```

```text
继续上次的科研项目。先复用已有的 PDF 缓存，再补充与当前假设直接相关的文献证据。
```

```text
把这段口语化需求整理成一条可以直接交给 Codex 执行的完整提示词，先不要执行。
```

## 验证

仓库中的缓存脚本可用以下命令测试：

```powershell
python -m pip install -r .\project-workflow\tests\requirements.txt
python -m unittest discover -s .\project-workflow\tests
```

确定性回归覆盖缓存、检索和格式边界。模型行为回归与版本对照使用 `evals/`，记录真实运行用量、工具轨迹及网络异常；运行方式和限制见该目录说明。

本次版本变更见 [更新记录](CHANGELOG.md)，新材料对照结果见 [v2.4.0 评测报告](docs/evaluation-v2.4.0.md)。性能结论按首读、复用及网络异常分别解释，不保证所有任务都更快或更省 token。

