# Codex Project Workflow

一个可复用的 Codex Skill：把用户偏口语的项目需求整理成可执行任务，并用轻量状态、分阶段范围、文档缓存和适度验证推进工作。

当前稳定版本：**v2.2.1**

## 主要能力

- 从口语化输入中提取目标、约束、交付物和验收标准，再生成适合当前任务的完整提示词。
- 根据项目阶段选择初始化、继续执行、调研、重定向、收尾或仅生成提示词等工作模式。
- 首次读取 PDF、Word 后生成 Markdown 缓存，后续轮次和新会话优先复用，减少重复解析。
- 区分竞赛、工程和科研调研：竞赛优先官方规则与强基线，科研按需进入正式文献检索与证据链流程。
- 按风险选择验证强度，在关键边界上检查，同时避免无目的地重复检查。

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
Copy-Item -Recurse -Force .\codex-project-workflow\project-workflow "$HOME\.codex\skills\project-workflow"
```

如果使用文档缓存脚本，请安装其 Python 依赖：

```powershell
python -m pip install -r .\codex-project-workflow\project-workflow\scripts\requirements.txt
```

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

发布版本已通过 Skill 结构检查和 14 个文档缓存单元测试。


