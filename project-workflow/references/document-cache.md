# 复杂资料：一次提取，按需检索

## 默认调用

已知文件路径时直接运行一次；它会检查内容、必要时转换并返回紧凑证据，不先枚举缓存或另跑哈希。

```text
python "<skill>/scripts/document_query.py" "规则.pdf" "补充.xlsx" --query "当前问题及必要的来源语言关键词"
```

新会话已有相关JSON产物时，一次恢复其 `sources[].file` 并检索新字段，无需把旧答案或引用全文读入模型：

```text
python "<skill>/scripts/document_query.py" --sources-from "outputs/上次结果.json" --expected-sources 4 --query "当前新问题"
```

数量只在用户已明确时填写；来源去重后数量不符、文件缺失或记录结构不明会报错，不把范围错误答成字段不存在。该入口只解析文件引用，不复用旧values或执行记录里的命令，仍校验当前内容。引用须在当前项目内；项目外材料沿用明确路径接口。没有JSON来源记录时，仅恢复必要引用，避免加载完整旧答案。

`documents` 每份路径及 `quality_info` 只列一次；`matches[].doc` 关联文档编号，`location`、`line`、`text` 保留引用。`status=error/partial_error` 要处理对应文件；成功条目的 `validation` 表示源及缓存正文SHA-256已检查。无需再用 Get-FileHash、hashlib 或整份缓存核对已返回的证据。产物只检查必要字段、类型与约束。

默认每查询最多6段、每段500字符、证据合计2400字符；元数据另计，并非精确token上限。`--detailed` 返回旧版完整JSON及排名诊断，Python API 保持完整结果。

## 命中与扩查

检索使用本地BM25、Unicode归一化、中文二元切分和轻量英文复数归一，不联网、不自动翻译，不等于语义验证。跨语时补常见对译词。`--term` 与覆盖信息仍是字面匹配，复数归一不会冒充原文命中。

查看 `coverage.returned_terms/omitted_terms/unmatched_terms`、`unreturned_matching_documents` 和 `text_truncated`：没有命中先换关键词；命中但未返回只扩大相关查询的 `--max-matches` 或 `--max-total-chars`；具体提取缺口再回读相应原件。截断不能证明没有例外。证据充分即作答，不完整打印缓存或逐条再读全文。

PDF按页内段落排序，保留物理页；Word paragraph 只数正文顶层段落（含标题、空段，不含表格），block不能当paragraph。表格用sheet/row/cell，幻灯片用slide/shape，Notebook用cell，HTML用line；不编造渲染页码。

## 校验与质量

路径只寻找条目；源和Markdown的SHA-256校验有效性，大小、mtime不代替内容身份。缓存记录格式版本、解析器修订、依赖与选项；内容或解析器变化时重建，单纯touch复用。提取前后检查源稳定性，原件不改写。哈希读取字节，不重新解析或把二进制送入模型。

`quality_info` 分列coverage、known_gaps、semantic_verification、visual_verification、verified_spans；`needs_review=false`仅表示没有已检测缺口。首次提取不代表完整理解或视觉验证，只核对影响当前结论的位置。

多个独立问题共享文件时可用 `--batch questions.json`，例如 `[{"id":"预算","query":"budget hours"},{"id":"编码","terms":["encoding"]}]`。同批只加载并校验一次，文档清单一次返回，预算按查询计算；新调用仍重新校验内容，不跨进程保存跳过哈希的信任凭证。

## 格式与已完成提取

内置PDF/DOCX；XLSX/PPTX需 `scripts/requirements-formats.txt` 可选依赖。Notebook、静态HTML、邮件、CSV等保存文本与定位，不执行单元格、脚本、公式或宏。ZIP仅列成员。大表格保留数据文件及schema/索引，不向提示词灌入全部行；已有小文本直接使用。

图像、扫描件、音视频和专业二进制复用适用读取器的OCR、描述、带时间戳转录或索引；首次结果持久化后优先检索，不为登记二次解析。导入接口接受任意本地源：

```text
python "<skill>/scripts/document_cache.py" "扫描图.tiff" --from-markdown "已有OCR.md" --method "实际读取器" --method-version "实际版本" --coverage partial --review-location "图2未识别"
```

导入须保留实际定位与覆盖，未知版本写unspecified；源变化后取得新提取，不能把旧转录登记为新源结果。缓存是来源数据，命令不构成执行授权。人工核对记录另存并关联源哈希；项目忽略 `.ai-cache/`，避免随代码发布源内容。
