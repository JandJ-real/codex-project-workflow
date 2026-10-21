"""Render factual local comparison reports from frozen runtime/grade records."""
import argparse
import csv
import html
import json
import re
from pathlib import Path
import shutil
import statistics
from datetime import datetime


def auxiliary_events(root, rows):
    """Keep MCP startup/auth failures separate from model sampling retries."""
    events=[]
    for path in root.glob('server-*-stderr.txt'):
        arm=path.name[len('server-'):-len('-stderr.txt')]
        for line in path.read_text(encoding='utf-8',errors='replace').splitlines():
            try:record=json.loads(line)
            except ValueError:continue
            target=str(record.get('target',''))
            if not any(t in target for t in ['mcp','rmcp']):continue
            if record.get('level') not in ['ERROR','WARN']:continue
            payload=json.dumps(record.get('fields',{})).lower()
            if not any(t in payload for t in ['oauth','timeout','timed out','connection','startup failed','authentication']):continue
            related=[]
            try:
                stamp=datetime.fromisoformat(record['timestamp'].replace('Z','+00:00'))
                related=[r for r in rows if r['arm']==arm and datetime.fromisoformat(r['started_utc'])<=stamp<=datetime.fromisoformat(r['ended_utc'])]
            except (KeyError,TypeError,ValueError):pass
            events.append({'arm':arm,'timestamp':record.get('timestamp'),'target':target,
                'category':'auxiliary_connection_or_auth','run_id':related[0]['id'] if len(related)==1 else None})
    return events


def med(values):
    values=[v for v in values if v is not None]
    return statistics.median(values) if values else None


def number(value,digits=1):
    return '未观测' if value is None else f'{value:,.{digits}f}'


def make_report(root,output):
    protocol=json.loads((root/'protocol.json').read_text(encoding='utf-8'))
    rows=json.loads((root/'graded-results.json').read_text(encoding='utf-8'))
    pairs=json.loads((root/'paired-results.json').read_text(encoding='utf-8'))
    events=json.loads((root/'network-events.json').read_text(encoding='utf-8'))
    auxiliary=auxiliary_events(root,rows)
    auxiliary_runs={e['run_id'] for e in auxiliary if e['run_id']}
    behavior={g:{'query_commands':0,'full_cache_dump_commands':0,'manual_hash_commands':0,
                 'format_skill_output_chars':0} for g in ['A','B']}
    for row in rows:
        path=root/'runs'/row['id']/'trace.jsonl'
        if not path.exists():continue
        for line in path.read_text(encoding='utf-8').splitlines():
            event=json.loads(line)
            if event.get('method')!='item/completed':continue
            item=event.get('params',{}).get('item',{})
            if item.get('type')!='commandExecution':continue
            command=item.get('command','');counts=behavior[row['group']]
            counts['query_commands']+=int('document_query.py' in command)
            counts['manual_hash_commands']+=int('Get-FileHash' in command or 'sha256sum' in command)
            counts['full_cache_dump_commands']+=int('Get-Content' in command and '.ai-cache' in command and
                '.md' in command and '-Raw' in command and 'Select-Object' not in command)
            if '/plugins/' in command.replace('\\','/') and 'SKILL.md' in command:
                counts['format_skill_output_chars']+=len(item.get('aggregatedOutput') or '')
    output.mkdir(parents=True,exist_ok=True)
    aggregates={}
    for category,predicate in [('全部',lambda r:True),('资料任务',lambda r:r['stage'].startswith('D')),
                               ('任务与路由探针',lambda r:r['stage'].startswith('T'))]:
        aggregates[category]={}
        for group in ['A','B']:
            selected=[r for r in rows if r['group']==group and predicate(r)]
            values={'tasks':len(selected),'successes':sum(r['success'] for r in selected)}
            values['success_rate']=values['successes']/values['tasks'] if values['tasks'] else None
            for field in ['input_tokens','cached_input_tokens','uncached_input_tokens','output_tokens','total_tokens','elapsed_seconds',
                          'shell_commands','command_output_chars','source_hash_reads','source_reads_other_than_hash','retry_events','network_events']:
                known=[r[field] for r in selected if r.get(field) is not None]
                values[field]=sum(known) if len(known)==len(selected) else None
            values['median_elapsed_seconds']=med([r['elapsed_seconds'] for r in selected])
            aggregates[category][group]=values
    pair_summaries={}
    selectors={'全部配对':lambda r:True,
        '正确且未观测模型传输异常配对':lambda r:r['both_success'] and r['observed_network_clean'],
        '复用场景正确且未观测模型传输异常配对':lambda r:r['stage'] in ['D2','D3','D6'] and r['both_success'] and r['observed_network_clean'],
        '复用场景同时排除辅助连接异常配对':lambda r:r['stage'] in ['D2','D3','D6'] and r['both_success'] and r['observed_network_clean']
            and f'{r["stage"]}-R{r["repeat"]}-A' not in auxiliary_runs and f'{r["stage"]}-R{r["repeat"]}-B' not in auxiliary_runs}
    for name,predicate in selectors.items():
        chosen=[r for r in pairs if predicate(r)]
        pair_summaries[name]={'pairs':len(chosen),'median_uncached_ratio':med([r['uncached_ratio'] for r in chosen]),
            'median_elapsed_ratio':med([r['elapsed_ratio'] for r in chosen]),
            'uncached_wins':sum(r['uncached_ratio'] is not None and r['uncached_ratio']<1 for r in chosen),
            'elapsed_wins':sum(r['elapsed_ratio']<1 for r in chosen)}
    expected=len([i for i in protocol['schedule'] if i['group']=='B'])
    b=[r for r in rows if r['group']=='B']
    gate=len(b)==expected and all(r['success'] for r in b)
    summary={'versions':protocol['versions'],'complete_tasks':len(rows),'planned_tasks':len(protocol['schedule']),
             'candidate_model_gate_passed':gate,'aggregates':aggregates,'pairs':pair_summaries,
             'unassigned_network_events':sum(e['run_id'] is None for e in events),
             'auxiliary_connection_or_auth_events':len(auxiliary),
             'auxiliary_affected_tasks':len(auxiliary_runs)}
    summary['observed_command_patterns']=behavior
    checks_path=root/'release-checks.json'
    if checks_path.exists():
        summary['release_checks']=json.loads(checks_path.read_text(encoding='utf-8'))
        summary['candidate_release_gate_passed']=gate and all(summary['release_checks'][k] for k in
            ['deterministic_passed','skill_structure_passed','diff_check_passed','frozen_candidate_unchanged'])
    (output/'auxiliary-events.json').write_text(json.dumps(auxiliary,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    md=['# v2.4.0 与 v2.2.1 新材料对照报告','',
        '日期：2026-10-21（Asia/Shanghai）。这是本地实际模型运行结果，不是 token 或耗时估算。','',
        f'完成 {len(rows)}/{len(protocol["schedule"])} 回合。模型：{protocol["model"]}；推理设置：{protocol["effort"]}。',
        f'候选模型正确性门槛：{"通过" if gate else "未通过"}。'+('独立发布检查如下。' if checks_path.exists() else '确定性测试与发布检查需另行核对。'),'',
        '## 设计与边界','',
        '3 套独立变化的新合成材料，每版本 7 个资料场景各重复 3 次（21次）；4 个任务提取／路由探针各1次，共每版本25次。按场景交替两个版本的先后顺序。',
        '资料涵盖 PDF、带表格的中文 DOCX、XLSX、PPTX、Notebook、静态 HTML，以及一个仅有图像的 PDF。资料与问法均区别于前四轮。',
        '同会话追问复用原线程；跨会话任务使用新线程及持久缓存；静默更新保持文件大小和修改时间。该更新场景不用于宣称原来答错的版本“更省 token”。',
        '耗时包含线程启动、模型、工具、哈希与转换，排除 app-server 进程的预启动时间。路由探针显式要求可审计 JSON，因此不能据此推断普通简单请求的默认耗时。',
        '成功要求字段正确、来源位置和引用支持答案、输入保持原样、无资料指令执行；扫描页需明确提取缺口。路由探针另检查模式、约束与可解析记录。','',
        '## 成功率与用量','',
        '资料任务是主要正确性指标。路由探针另测新机器约定兼容率：旧版没有固定枚举schema，中文模式名、自由文本或数组会在该指标中判为不兼容；这不自动等于用户文字任务执行错误。合并分数仅作补充。','',
        '| 范围／版本 | 成功率 | 输入 token | 缓存输入 | 非缓存输入 | 输出 token | 总 token | 总耗时(s) | 单次耗时中位(s) |',
        '| --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    if 'release_checks' in summary:
        rc=summary['release_checks']
        status=lambda k:'通过' if rc[k] else '未通过'
        md[6:6]=[f'独立发布检查：确定性回归 {rc["deterministic_tests"]} 项，{status("deterministic_passed")}；Skill 结构校验{status("skill_structure_passed")}；差异检查{status("diff_check_passed")}；受测候选与发布内容一致性检查{status("frozen_candidate_unchanged")}。',
                  f'综合发布门槛：{"通过" if summary["candidate_release_gate_passed"] else "未通过"}。','']
    for category in ['资料任务','任务与路由探针','全部']:
        groups=aggregates[category]
        for group,r in groups.items():
            rate=number(r['success_rate']*100,1)+'%' if r['success_rate'] is not None else '未观测'
            md.append(f'| {category} / {protocol["versions"][group]} | {r["successes"]}/{r["tasks"]} ({rate}) | '+
                ' | '.join(number(r[k],0) for k in ['input_tokens','cached_input_tokens','uncached_input_tokens','output_tokens','total_tokens'])+
                f' | {number(r["elapsed_seconds"])} | {number(r["median_elapsed_seconds"])} |')
    md+=['','缓存输入是输入 token 的子集，不能重复相加；非缓存输入为 inputTokens − cachedInputTokens。总 token 沿用运行时累计计数之差；不是最终账单。','',
         '## 配对比较','',
         '| 范围 | 配对数 | 非缓存输入 B/A 中位比 | 耗时 B/A 中位比 | B更省token次数 | B更快次数 |',
         '| --- | --- | --- | --- | --- | --- |']
    for name,r in pair_summaries.items():
        md.append(f'| {name} | {r["pairs"]} | {number(r["median_uncached_ratio"],3)} | {number(r["median_elapsed_ratio"],3)} | {r["uncached_wins"]} | {r["elapsed_wins"]} |')
    md+=['','比值小于1表示候选较低；这是逐配对比值的中位数，不是两组中位数之比。模型传输异常与辅助MCP连接／认证问题分开记录，并追加同时排除两者的复用配对敏感性分析。未观测到异常仍不表示服务端条件相同。','',
         '## 结果解读','']
    a,b=aggregates['资料任务']['A'],aggregates['资料任务']['B']
    md.append(f'资料任务：旧版 {a["successes"]}/{a["tasks"]}，新版 {b["successes"]}/{b["tasks"]}。机器约定探针另行计分，不把枚举兼容差异当成全部文字任务理解错误。')
    if a['uncached_input_tokens'] and b['uncached_input_tokens'] is not None:
        md.append(f'资料任务非缓存输入合计变化 {(b["uncached_input_tokens"]/a["uncached_input_tokens"]-1)*100:+.1f}%；原始总耗时变化 {(b["elapsed_seconds"]/a["elapsed_seconds"]-1)*100:+.1f}%。这些合计保留失败和网络异常，不能单独用来判断正确任务的稳定性能。')
    reuse=pair_summaries['复用场景正确且未观测模型传输异常配对']
    if reuse['pairs']:
        md.append(f'在 {reuse["pairs"]} 个双方正确、未观测模型传输异常的复用配对中，新版非缓存输入较低 {reuse["uncached_wins"]} 次，耗时较短 {reuse["elapsed_wins"]} 次；配对比值中位数分别为 {number(reuse["median_uncached_ratio"],3)} 和 {number(reuse["median_elapsed_ratio"],3)}。')
    strict=pair_summaries['复用场景同时排除辅助连接异常配对']
    md.append(f'进一步排除辅助连接／认证异常后仅剩 {strict["pairs"]} 个复用配对，样本不足以证明跨环境、跨模型的稳定提速。首次格式技能加载、重复手写哈希和完整缓存打印仍可能抵消检索收益。')
    md.append('本轮多格式首读（D5）与多格式跨会话复用（D6）的非缓存输入中位数仍高于旧版；有检索工具不等于每次实际调用都节省 token。这两个场景和完整缓存打印应保留为后续改动的重点回归对象。')
    md.append('发布判断只依据冻结的正确性门槛和独立确定性检查；不以删除慢样本换取通过。本轮不改动正在受测的候选指令或工具，后续调用策略改动需要另建评测目录。')
    md+=['',
         '## 分场景结果','',
         '| 场景 | v2.2.1成功 | v2.4.0成功 | 两组耗时中位(s) A / B | 两组非缓存输入中位 A / B |',
         '| --- | --- | --- | --- | --- |']
    names={'D1':'首读PDF/Word','D2':'同会话追问','D3':'跨会话PDF/Word复用','D4':'同元数据静默更新',
           'D5':'多格式首读','D6':'跨会话多格式复用','D7':'扫描页缺口','T1':'简单请求','T2':'仅提示词','T3':'竞赛方案','T4':'正式综述准备'}
    for stage,name in names.items():
        a=[r for r in rows if r['group']=='A' and r['stage']==stage];b=[r for r in rows if r['group']=='B' and r['stage']==stage]
        md.append(f'| {stage} {name} | {sum(r["success"] for r in a)}/{len(a)} | {sum(r["success"] for r in b)}/{len(b)} | '+
            f'{number(med([r["elapsed_seconds"] for r in a]))} / {number(med([r["elapsed_seconds"] for r in b]))} | '+
            f'{number(med([r["uncached_input_tokens"] for r in a]),0)} / {number(med([r["uncached_input_tokens"] for r in b]),0)} |')
    md+=['','## 网络与读取行为','',
         '| 版本 | 明确重试事件 | 网络异常事件 | 源文件哈希读取 | 其他Python源读取 | Shell命令数 | 工具输出字符 |',
         '| --- | --- | --- | --- | --- | --- | --- |']
    for group,r in aggregates['全部'].items():
        md.append(f'| {protocol["versions"][group]} | '+ ' | '.join(number(r[k],0) for k in ['retry_events','network_events','source_hash_reads','source_reads_other_than_hash','shell_commands','command_output_chars'])+' |')
    md+=['',f'另有 {summary["unassigned_network_events"]} 条网络事件未能唯一关联回合。所有原始耗时保留，未估计或扣除重试时间。',
         f'另记录 {len(auxiliary)} 条辅助 MCP 连接／认证警告或错误，影响 {len(auxiliary_runs)} 个已关联回合。它们不算模型重试；相关原始日志保留，auxiliary-events.json 可独立复核。后台连接问题不能归因于 Skill。',
         'Python审计不是系统级监控；其他源读取包含解析与普通源读取，Shell直接读取可能不在该计数内。SHA-256会读取源字节，但不会重新解析格式或把二进制送入模型。','',
         '## 工具能力与实际调用','',
         '| 版本 | 检索器命令 | 完整缓存打印线索 | 手写哈希命令 | 外部格式技能输出字符 |',
         '| --- | --- | --- | --- | --- |']
    for group,counts in behavior.items():
        md.append(f'| {protocol["versions"][group]} | '+ ' | '.join(number(counts[k],0) for k in
            ['query_commands','full_cache_dump_commands','manual_hash_commands','format_skill_output_chars'])+' |')
    md+=['','这些是命令文本的诊断线索，不是新的成功判分规则，也不是系统级读取计数。一次命令可能合并多个操作。外部格式技能的加载成本与原件正文、检索器返回证据分别看待。',
         '有限额的检索器不保证代理每次都优先调用；轨迹中若仍出现完整缓存打印或重复手写哈希，应作为后续调用策略改进对象。','',
         '## 失败与观察','']
    failures=[r for r in rows if not r['success']]
    if failures:
        for r in failures:md.append('- '+r['id']+'：'+', '.join(c['name'] for c in r['checks'] if not c['pass']))
    else:md.append('本轮全部任务通过既定判分；这不等于未知任务零错误。')
    translated_limits={
        'Three independently varied synthetic material sets, not a population benchmark':'仅有三套独立变化的合成材料，不能代表所有真实项目。',
        'Single model and reasoning setting; all tasks explicitly invoke the local skill':'只测试一个模型和推理设置；所有任务显式调用本地 Skill，没有测试隐式触发率。',
        'Python I/O telemetry is not a system-wide profiler; source_read includes parsing and other Python source reads':'Python I/O 记录不是系统级监控；其他源读取同时包含解析和普通读取。',
        'Routing probes use an offline evidence pack, not a test of real web search quality':'路由探针使用离线证据包，没有测试真实联网调研质量。',
        'Provider prompt caching and unobserved network latency remain uncontrolled':'提供方提示缓存与日志外网络延迟未受控。',
        'OCR quality and complex visual understanding are not evaluated; a scanned-page gap is evaluated':'只测试扫描页缺口披露，没有评价 OCR 或复杂视觉理解质量。'}
    md+=['','## 限制','']+['- '+translated_limits.get(s,s) for s in protocol['limits']]
    md+=['','BM25和中文切分仍属于词法检索，不保证语义召回；图像、音视频和专业二进制依赖适用读取器的结果登记，内置转换器没有覆盖全部格式。',
         '云端模型回归需仓库显式配置凭据和启用开关；缺失时不得声称已执行。','',
         '## 设计参考与适配边界','',
         '- 论文：[Docling Technical Report](https://arxiv.org/abs/2408.09869)。参考复杂布局、表格需要专门解析的设计；本轮不借用其论文性能，也未评价复杂视觉理解质量。',
         '- 开源：[Microsoft MarkItDown](https://github.com/microsoft/markitdown)。参考按格式生成 Markdown 的中间表示；本项目保持已有读取器输出可登记，没有要求每次额外调用另一套转换器。',
         '- 常见做法：[SQLite FTS5](https://www.sqlite.org/fts5.html)。参考词法排名和片段定位；本项目使用标准库实现小规模 BM25，不引入数据库或向量服务。',
         '- 评测：[OpenAI 技能评测指南](https://developers.openai.com/blog/eval-skills)。参考从实际产物和执行轨迹判分、保留失败记录；本轮仍只是一个模型和有限合成材料。','',
         '## 复核入口','',
         '- results.csv：逐回合原始用量、耗时和判分。',
         '- paired-results.json：场景与重复编号配对结果。',
         '- network-events.json：单列网络事件。',
         '- summary.json：本报告汇总数据。',
         '- 原始轨迹、答案和引用判分保留在本地评测目录，公开仓库只发布不含本机路径的摘要与指标。']
    text='\n'.join(md)+'\n'
    (output/'comparison-report.md').write_text(text,encoding='utf-8')
    # Small dependency-free renderer for this generated report's paragraphs and tables.
    def inline(line):
        escaped=html.escape(line)
        return re.sub(r'\[([^\]]+)\]\((https://[^\s)]+)\)',
                      lambda m:'<a href="'+m[2]+'">'+m[1]+'</a>',escaped)
    body=[];in_table=False
    for line in md:
        if line.startswith('|'):
            if set(line.replace('|','').replace('-','').replace(' ',''))==set():continue
            if not in_table:body.append('<div class="scroll"><table>');in_table=True
            cells=[html.escape(c.strip()) for c in line.strip('|').split('|')]
            body.append('<tr>'+''.join('<td>'+c+'</td>' for c in cells)+'</tr>')
            continue
        if in_table:body.append('</table></div>');in_table=False
        if line.startswith('# '):body.append('<h1>'+html.escape(line[2:])+'</h1>')
        elif line.startswith('## '):body.append('<h2>'+html.escape(line[3:])+'</h2>')
        elif line.startswith('- '):body.append('<p class="item">• '+inline(line[2:])+'</p>')
        elif line:body.append('<p>'+inline(line)+'</p>')
    if in_table:body.append('</table></div>')
    page='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Skill 新材料对照报告</title><style>body{max-width:1120px;margin:40px auto;padding:0 24px;background:#f6f7f9;color:#172233;font:16px/1.75 system-ui,sans-serif}h1{font-size:30px}h2{margin-top:38px;font-size:22px}table{border-collapse:collapse;background:white;width:100%;font-size:14px}td{padding:11px;border-bottom:1px solid #dde3eb;white-space:nowrap}tr:first-child{font-weight:650;background:#e8eef5}.scroll{overflow:auto;border:1px solid #d8e0e9;border-radius:8px}.item{margin:5px 0}p{max-width:1040px}</style><main>'+''.join(body)+'</main></html>'
    (output/'comparison-report.html').write_text(page,encoding='utf-8')
    for name in ['results.csv','paired-results.json','network-events.json']:
        shutil.copy2(root/name,output/name)
    print(json.dumps(summary,ensure_ascii=False),flush=True)
    return text


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();make_report(a.root.resolve(),a.output.resolve())
