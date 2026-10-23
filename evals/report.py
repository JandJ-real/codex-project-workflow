"""Render factual local comparison reports from frozen runtime/grade records."""
import argparse
import csv
import html
import json
import re
from pathlib import Path
import shutil
import statistics
from datetime import datetime,timezone,timedelta


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


def background_catalog_events(root,rows):
    """Model-list refresh failures are neither MCP nor sampling retries."""
    events=[]
    for path in root.glob('server-*-stderr.txt'):
        arm=path.name[len('server-'):-len('-stderr.txt')]
        for line in path.read_text(encoding='utf-8',errors='replace').splitlines():
            try:record=json.loads(line)
            except ValueError:continue
            if 'models_manager' not in str(record.get('target','')) or record.get('level') not in ['ERROR','WARN']:continue
            payload=json.dumps(record.get('fields',{})).lower()
            if 'refresh' not in payload or not any(t in payload for t in ['failed','timeout','timed out','connection']):continue
            related=[]
            try:
                stamp=datetime.fromisoformat(record['timestamp'].replace('Z','+00:00'))
                related=[r for r in rows if r['arm']==arm and datetime.fromisoformat(r['started_utc'])<=stamp<=datetime.fromisoformat(r['ended_utc'])]
            except (KeyError,TypeError,ValueError):pass
            events.append({'arm':arm,'timestamp':record.get('timestamp'),'category':'background_model_catalog_refresh',
                'run_id':related[0]['id'] if len(related)==1 else None})
    return events


def med(values):
    values=[v for v in values if v is not None]
    return statistics.median(values) if values else None


def number(value,digits=1):
    return '未观测' if value is None else f'{value:,.{digits}f}'


def query_invoked(command):
    return bool(re.search(r'python(?:[0-9.]*|\.exe)[^;\r\n]{0,500}document_query\.py',command,re.I)) and '--help' not in command


def full_cache_dump_invoked(command):
    """Unbounded cache display hint; explicit numeric line slices are bounded."""
    if not ('Get-Content' in command and '.ai-cache' in command and '.md' in command
            and 'Select-Object' not in command and 'ConvertFrom-Json' not in command):return False
    if command.count('Get-Content')==1:
        assignment=re.search(r'\$(\w+)\s*=\s*Get-Content\b[^;]*\.md',command)
        if assignment:
            tail=command[assignment.end():]
            references=list(re.finditer(r'\$'+re.escape(assignment[1])+r'\b',tail))
            if references and all(re.match(r'\[\s*\d+\s*(?:\.\.\s*\d+\s*)?\]',tail[m.end():]) for m in references):return False
    return True


def make_report(root,output):
    protocol=json.loads((root/'protocol.json').read_text(encoding='utf-8'))
    rows=json.loads((root/'graded-results.json').read_text(encoding='utf-8'))
    pairs=json.loads((root/'paired-results.json').read_text(encoding='utf-8'))
    events=json.loads((root/'network-events.json').read_text(encoding='utf-8'))
    auxiliary=auxiliary_events(root,rows)
    auxiliary_runs={e['run_id'] for e in auxiliary if e['run_id']}
    catalog=background_catalog_events(root,rows)
    catalog_runs={e['run_id'] for e in catalog if e['run_id']}
    indexed={(r['repeat'],r['stage'],r['group']):r for r in rows}
    comparable=set()
    for r in rows:
        other=indexed.get((r['repeat'],r['stage'],'B' if r['group']=='A' else 'A'))
        if (other and r.get('comparison_eligible',True) and other.get('comparison_eligible',True)
                and r['status']=='completed' and other['status']=='completed'
                and r['usage'] is not None and other['usage'] is not None):
            comparable.add(r['id'])
    behavior={g:{'query_commands':0,'source_record_query_commands':0,'full_cache_dump_commands':0,'manual_hash_commands':0,
                 'format_skill_output_chars':0} for g in ['A','B']}
    comparable_behavior={g:dict(v) for g,v in behavior.items()}
    for row in rows:
        path=root/'runs'/row['id']/'trace.jsonl'
        if not path.exists():continue
        for line in path.read_text(encoding='utf-8').splitlines():
            event=json.loads(line)
            if event.get('method')!='item/completed':continue
            item=event.get('params',{}).get('item',{})
            if item.get('type')!='commandExecution':continue
            command=item.get('command','')
            additions={'query_commands':int(query_invoked(command)),
                'source_record_query_commands':int(query_invoked(command) and '--sources-from' in command),
                'manual_hash_commands':int('Get-FileHash' in command or 'sha256sum' in command or
                                           ('hashlib' in command and 'sha256' in command)),
                'full_cache_dump_commands':int(full_cache_dump_invoked(command)),
                'format_skill_output_chars':len(item.get('aggregatedOutput') or '') if
                    '/plugins/' in command.replace('\\','/') and 'SKILL.md' in command else 0}
            for key,value in additions.items():
                behavior[row['group']][key]+=value
                if row['id'] in comparable:comparable_behavior[row['group']][key]+=value
    output.mkdir(parents=True,exist_ok=True)
    aggregates={}
    for category,predicate in [('全部',lambda r:True),('资料任务',lambda r:r['stage'].startswith('D')),
                               ('可比资料配对',lambda r:r['stage'].startswith('D') and r['id'] in comparable),
                               ('任务与路由探针',lambda r:r['stage'].startswith('T'))]:
        aggregates[category]={}
        for group in ['A','B']:
            selected=[r for r in rows if r['group']==group and predicate(r)]
            values={'tasks':len(selected),'successes':sum(r['success'] for r in selected)}
            values['observed_totals']={};values['missing_counts']={}
            values['success_rate']=values['successes']/values['tasks'] if values['tasks'] else None
            for field in ['input_tokens','cached_input_tokens','uncached_input_tokens','output_tokens','total_tokens','elapsed_seconds','remote_turn_seconds',
                          'shell_commands','command_output_chars','source_hash_reads','source_reads_other_than_hash','retry_events','network_events']:
                known=[r[field] for r in selected if r.get(field) is not None]
                values[field]=sum(known) if len(known)==len(selected) else None
                values['observed_totals'][field]=sum(known)
                values['missing_counts'][field]=len(selected)-len(known)
            values['median_elapsed_seconds']=med([r['elapsed_seconds'] for r in selected])
            values['median_remote_turn_seconds']=med([r.get('remote_turn_seconds') for r in selected])
            aggregates[category][group]=values
    pair_summaries={}
    selectors={'全部配对':lambda r:True,
        '正确且未观测模型传输异常配对':lambda r:r['both_success'] and r['observed_network_clean'],
        '复用场景正确且未观测模型传输异常配对':lambda r:r['stage'] in ['D2','D3','D6'] and r['both_success'] and r['observed_network_clean'],
        '多格式首读及复用干净正确配对':lambda r:r['stage'] in ['D5','D6'] and r['both_success'] and r['observed_network_clean'],
        '多格式首读及复用全部正确配对':lambda r:r['stage'] in ['D5','D6'] and r['both_success'],
        '复用场景同时排除辅助连接异常配对':lambda r:r['stage'] in ['D2','D3','D6'] and r['both_success'] and r['observed_network_clean']
            and f'{r["stage"]}-R{r["repeat"]}-A' not in auxiliary_runs and f'{r["stage"]}-R{r["repeat"]}-B' not in auxiliary_runs,
        '复用场景另排除可关联目录刷新异常配对':lambda r:r['stage'] in ['D2','D3','D6'] and r['both_success'] and r['observed_network_clean']
            and f'{r["stage"]}-R{r["repeat"]}-A' not in auxiliary_runs|catalog_runs
            and f'{r["stage"]}-R{r["repeat"]}-B' not in auxiliary_runs|catalog_runs}
    for name,predicate in selectors.items():
        chosen=[r for r in pairs if predicate(r) and (name=='全部配对' or r.get('comparison_eligible',True))]
        pair_summaries[name]={'pairs':len(chosen),'median_uncached_ratio':med([r['uncached_ratio'] for r in chosen]),
            'median_elapsed_ratio':med([r['elapsed_ratio'] for r in chosen]),
            'uncached_wins':sum(r['uncached_ratio'] is not None and r['uncached_ratio']<1 for r in chosen),
            'elapsed_wins':sum(r['elapsed_ratio'] is not None and r['elapsed_ratio']<1 for r in chosen),
            'elapsed_pairs':sum(r['elapsed_ratio'] is not None for r in chosen),
            'remote_elapsed_pairs':sum(r.get('remote_elapsed_ratio') is not None for r in chosen),
            'median_remote_elapsed_ratio':med([r.get('remote_elapsed_ratio') for r in chosen])}
    expected=len([i for i in protocol['schedule'] if i['group']=='B'])
    b=[r for r in rows if r['group']=='B']
    gate=len(b)==expected and all(r['success'] for r in b)
    summary={'versions':protocol['versions'],'complete_tasks':len(rows),'planned_tasks':len(protocol['schedule']),
             'candidate_model_gate_passed':gate,'aggregates':aggregates,'pairs':pair_summaries,
             'unassigned_network_events':sum(e['run_id'] is None for e in events),
             'auxiliary_connection_or_auth_events':len(auxiliary),
             'auxiliary_affected_tasks':len(auxiliary_runs)}
    summary['observed_command_patterns']=behavior
    summary['provider_capacity_errors']=sum(e.get('category')=='service_capacity_error' or e.get('rpc_error_code')=='serverOverloaded' for e in events)
    summary['provider_limit_errors']=sum(e.get('category')=='service_limit_error' or e.get('rpc_error_code')=='usageLimitExceeded' for e in events)
    summary['comparable_command_patterns']=comparable_behavior
    summary['operational_excluded_runs']=[r['id'] for r in rows if not r.get('comparison_eligible',True)]
    configs=[json.loads(p.read_text(encoding='utf-8')) for p in root.glob('server-*-config.json')]
    isolation=bool(configs) and all(c.get('effective_overrides_verified') and c.get('active_mcp_servers')==0 for c in configs) and not auxiliary
    summary['mcp_isolation']={'server_processes':len(configs),'verified':isolation,'auxiliary_events':len(auxiliary)}
    summary['background_catalog']={'events':len(catalog),'affected_tasks':len(catalog_runs)}
    multi=pair_summaries['多格式首读及复用干净正确配对']
    multi_all=pair_summaries['多格式首读及复用全部正确配对']
    no_extra_redundancy=all(comparable_behavior['B'][k]<=comparable_behavior['A'][k] for k in ['full_cache_dump_commands','manual_hash_commands'])
    performance_gate=(multi['pairs']>=4 and multi['median_uncached_ratio'] is not None and
                      multi['median_uncached_ratio']<1 and no_extra_redundancy and isolation)
    stage_pairs={}
    for stage in ['D5','D6']:
        all_pairs=[p for p in pairs if p['stage']==stage and p['both_success'] and p.get('comparison_eligible',True)]
        clean=[p for p in all_pairs if p['observed_network_clean']]
        stage_pairs[stage]={'all_pairs':len(all_pairs),'clean_pairs':len(clean),
            'all_median_uncached_ratio':med([p['uncached_ratio'] for p in all_pairs]),
            'clean_median_uncached_ratio':med([p['uncached_ratio'] for p in clean]),
            'all_median_elapsed_ratio':med([p['elapsed_ratio'] for p in all_pairs]),
            'clean_median_elapsed_ratio':med([p['elapsed_ratio'] for p in clean])}
    summary['multiformat_stage_pairs']=stage_pairs
    if protocol.get('strict_performance'):
        performance_gate=performance_gate and all(p['all_pairs']>=3 and p['clean_pairs']>=2 and
            p['all_median_uncached_ratio'] is not None and p['all_median_uncached_ratio']<1 and
            p['clean_median_uncached_ratio'] is not None and p['clean_median_uncached_ratio']<1
            for p in stage_pairs.values()) and all(behavior['B'][k]==0 for k in ['manual_hash_commands','full_cache_dump_commands'])
    summary['candidate_optimization_gate_passed']=performance_gate
    checks_path=root/'release-checks.json'
    if checks_path.exists():
        summary['release_checks']=json.loads(checks_path.read_text(encoding='utf-8'))
        summary['candidate_release_gate_passed']=gate and performance_gate and all(summary['release_checks'].get(k,False) for k in
            ['deterministic_passed','skill_structure_passed','diff_check_passed','frozen_candidate_unchanged',
             'evaluation_metadata_passed','artifact_audit_passed','user_config_unchanged'])
    (output/'auxiliary-events.json').write_text(json.dumps(auxiliary,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'background-events.json').write_text(json.dumps(catalog,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    dates=[datetime.fromisoformat(r[k]).astimezone(timezone(timedelta(hours=8))).date() for r in rows for k in ['started_utc','ended_utc']]
    date=(str(min(dates)) if min(dates)==max(dates) else f'{min(dates)} 至 {max(dates)}') if dates else '未开始'
    md=[f'# v{protocol["versions"]["B"]} 与 v{protocol["versions"]["A"]} 隔离对照报告','',
        f'日期：{date}（Asia/Shanghai）。这是本地实际模型运行结果，不是 token 或耗时估算。','',
        f'完成 {len(rows)}/{len(protocol["schedule"])} 回合。模型：{protocol["model"]}；推理设置：{protocol["effort"]}。',
        f'候选模型正确性门槛：{"通过" if gate else "未通过"}。'+('独立发布检查如下。' if checks_path.exists() else '确定性测试与发布检查需另行核对。'),'',
        '## 设计与边界','',
        '主试验使用3套独立变化材料，各版本7个资料场景各3次，路由探针各1次，合计50回合。重点场景若另有预登记的追加对照，全部原始回合一并保留，追加不替换旧记录。各场景次数见分场景表。',
        '资料涵盖 PDF、带表格的中文 DOCX、XLSX、PPTX、Notebook、静态 HTML，以及一个仅有图像的 PDF。沿用可复核的场景结构，重新生成不同字段值和标识；不把它当未知真实项目的覆盖证明。',
        '同会话追问复用原线程；跨会话任务使用新线程及持久缓存；静默更新保持文件大小和修改时间。该更新场景不用于宣称原来答错的版本“更省 token”。',
        '耗时包含线程启动、模型、工具、哈希与转换，排除 app-server 进程的预启动时间。路由探针显式要求可审计 JSON，因此不能据此推断普通简单请求的默认耗时。',
        '成功要求字段正确、来源位置和引用支持答案、输入保持原样、无资料指令执行；扫描页需明确提取缺口。路由探针另检查模式、约束与可解析记录。','',
        '## 成功率与用量','',
        '资料任务是主要正确性指标。路由探针单列检查可解析约定、模式与流程枚举；两版使用相同要求，结构不兼容也不自动等于文字任务全部执行错误。合并分数仅作补充。','',
        '| 范围／版本 | 成功率 | 输入 token | 缓存输入 | 非缓存输入 | 输出 token | 总 token | 外层总耗时(s) | 外层单次中位(s) |',
        '| --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    if 'release_checks' in summary:
        rc=summary['release_checks']
        status=lambda k:'通过' if rc[k] else '未通过'
        md[6:6]=[f'独立发布检查：确定性回归 {rc["deterministic_tests"]} 项，{status("deterministic_passed")}；Skill 结构校验{status("skill_structure_passed")}；差异检查{status("diff_check_passed")}；受测候选与发布内容一致性检查{status("frozen_candidate_unchanged")}。',
                  f'评测元数据测试 {rc.get("evaluation_metadata_tests",0)} 项，{"通过" if rc.get("evaluation_metadata_passed") else "未通过"}；引文／日期与冻结文件审计{"通过" if rc.get("artifact_audit_passed") else "未通过"}；用户配置未改变检查{"通过" if rc.get("user_config_unchanged") else "未通过"}。',
                  f'综合发布门槛：{"通过" if summary["candidate_release_gate_passed"] else "未通过"}。','']
    for category in ['可比资料配对','资料任务','任务与路由探针','全部']:
        groups=aggregates[category]
        for group,r in groups.items():
            rate=number(r['success_rate']*100,1)+'%' if r['success_rate'] is not None else '未观测'
            md.append(f'| {category} / {protocol["versions"][group]} | {r["successes"]}/{r["tasks"]} ({rate}) | '+
                ' | '.join(number(r[k],0) for k in ['input_tokens','cached_input_tokens','uncached_input_tokens','output_tokens','total_tokens'])+
                f' | {number(r["elapsed_seconds"])} | {number(r["median_elapsed_seconds"])} |')
    md+=['','缓存输入是输入 token 的子集，不能重复相加；非缓存输入为 inputTokens − cachedInputTokens。总 token 沿用运行时累计计数之差；不是最终账单。','',
         '可比资料表仅排除运行中断及被其影响的同会话配对，不按快慢筛选。原始表包含这些记录；缺失用量标为未观测，不填零，也不推算。超时不自动等同为Skill内容判断错误。',
         '若主机暂停导致计时跳变，原始耗时照录，不估计扣除暂停时间；该配对及无法可靠归属用量的后续同会话配对标为不可比。覆盖与重点场景重复次数按预登记计划呈现。','',
         '## 统一远端回合计时','',
         '统一从每个原始 turn/completed.durationMs 提取，包含该回合内的模型、工具与重试，不含线程启动；与外层秒表不同，不混填。外层计时缺失时保持未知，远端计时也只用实际记录，不估计补齐。',
         '| 范围／版本 | 远端计时记录数／回合数 | 合计(s) | 中位(s) |','| --- | --- | --- | --- |']
    for category in ['资料任务','任务与路由探针','全部']:
        for group,r in aggregates[category].items():
            md.append(f'| {category} / {protocol["versions"][group]} | {r["tasks"]-r["missing_counts"]["remote_turn_seconds"]}/{r["tasks"]} | {number(r["remote_turn_seconds"])} | {number(r["median_remote_turn_seconds"])} |')
    md+=['',
         '## 配对比较','',
         '| 范围 | 配对数 | 非缓存输入 B/A 中位比 | 外层耗时 B/A 中位比 | B更省token次数 | B外层更快次数 | 外层计时配对数 | 远端计时配对数 | 远端耗时B/A中位比 |',
         '| --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    for name,r in pair_summaries.items():
        md.append(f'| {name} | {r["pairs"]} | {number(r["median_uncached_ratio"],3)} | {number(r["median_elapsed_ratio"],3)} | {r["uncached_wins"]} | {r["elapsed_wins"]} | {r["elapsed_pairs"]} | {r["remote_elapsed_pairs"]} | {number(r["median_remote_elapsed_ratio"],3)} |')
    md+=['','比值小于1表示候选较低；这是逐配对比值的中位数，不是两组中位数之比。模型传输异常与辅助MCP连接／认证问题分开记录，并追加同时排除两者的复用配对敏感性分析。未观测到异常仍不表示服务端条件相同。','',
         '## 结果解读','']
    a,b=aggregates['可比资料配对']['A'],aggregates['可比资料配对']['B']
    md.append(f'可比资料配对：旧版 {a["successes"]}/{a["tasks"]}，新版 {b["successes"]}/{b["tasks"]}。机器约定探针及运行异常另行计分，不将中断当成内容误答。')
    if a['uncached_input_tokens'] and b['uncached_input_tokens'] is not None:
        md.append(f'可比资料任务非缓存输入合计变化 {(b["uncached_input_tokens"]/a["uncached_input_tokens"]-1)*100:+.1f}%。该表保留模型网络异常，不能单独证明稳定性能。')
    if a['elapsed_seconds'] and b['elapsed_seconds'] is not None:
        md.append(f'可比资料任务外层总耗时变化 {(b["elapsed_seconds"]/a["elapsed_seconds"]-1)*100:+.1f}%。')
    else:md.append('外层计时记录不齐，不计算外层总耗时变化；逐条缺失保留，不填零。')
    if a['remote_turn_seconds'] and b['remote_turn_seconds'] is not None:
        md.append(f'统一远端计时的资料任务合计变化 {(b["remote_turn_seconds"]/a["remote_turn_seconds"]-1)*100:+.1f}%；仍包含真实重试。')
    reuse=pair_summaries['复用场景正确且未观测模型传输异常配对']
    if reuse['pairs']:
        md.append(f'在 {reuse["pairs"]} 个双方正确、未观测模型传输异常的复用配对中，新版非缓存输入较低 {reuse["uncached_wins"]} 次，耗时较短 {reuse["elapsed_wins"]} 次；配对比值中位数分别为 {number(reuse["median_uncached_ratio"],3)} 和 {number(reuse["median_elapsed_ratio"],3)}。')
    strict=pair_summaries['复用场景同时排除辅助连接异常配对']
    md.append(f'同时排除辅助连接／认证异常后有 {strict["pairs"]} 个复用配对。仍限于单一模型、推理设置与合成材料，不能证明跨环境、跨模型的稳定提速。')
    catalog_clean=pair_summaries['复用场景另排除可关联目录刷新异常配对']
    md.append(f'另排除可关联的后台模型目录刷新异常后，有 {catalog_clean["pairs"]} 个复用配对；非缓存输入比值中位 {number(catalog_clean["median_uncached_ratio"],3)}，耗时比值中位 {number(catalog_clean["median_elapsed_ratio"],3)}。这是补充敏感性分析，不更改预登记发布门槛。')
    md.append(f'重点多格式场景（D5/D6）：{multi["pairs"]} 个干净正确配对，非缓存输入比值中位数 {number(multi["median_uncached_ratio"],3)}，耗时比值中位数 {number(multi["median_elapsed_ratio"],3)}；新版更省 token {multi["uncached_wins"]} 次，更快 {multi["elapsed_wins"]} 次。分场景结果仍需分别看。')
    md.append(f'保留网络异常的多格式正确配对共 {multi_all["pairs"]} 个：非缓存输入比值中位数 {number(multi_all["median_uncached_ratio"],3)}，耗时比值中位数 {number(multi_all["median_elapsed_ratio"],3)}。异常样本的额外检索也保留在此，不将其 token 开销都归因于重试。')
    md.append(f'优化门槛：{"通过" if performance_gate else "未通过"}。要求至少4个干净正确多格式配对、其非缓存输入比值中位数低于1、冗余命令线索不多于基线、MCP隔离检查通过。')
    if protocol.get('strict_performance'):
        md.append('本候选另采用预登记的严格门槛：D5首读与D6复用分别至少3个正确配对、2个干净配对，两种口径的非缓存输入比值中位数都低于1；新版整份缓存打印及手写哈希线索都为0。')
    for stage,p in stage_pairs.items():
        md.append(f'{stage}：全部正确配对 {p["all_pairs"]} 个，非缓存比值中位 {number(p["all_median_uncached_ratio"],3)}；干净配对 {p["clean_pairs"]} 个，比值中位 {number(p["clean_median_uncached_ratio"],3)}。')
    md.append('发布判断依据冻结的正确性、性能门槛和独立确定性检查；不以删除慢样本换取通过。本轮不改动正在受测的候选指令或工具，后续调用策略改动需要另建评测目录。')
    md+=['',
         '## 分场景结果','',
         f'| 场景 | v{protocol["versions"]["A"]}成功 | v{protocol["versions"]["B"]}成功 | 两组耗时中位(s) A / B | 两组非缓存输入中位 A / B |',
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
         f'上述异常分类另含服务端容量错误 {summary["provider_capacity_errors"]} 条、额度错误 {summary["provider_limit_errors"]} 条，类别见 network-events.json；它们不自动计为重试。RPC error 与其重复的 turn/completed 只计一次；相邻同回合的 stderr 重试记录合并。服务不可用与 Skill 内容误答需分别判断。',
         f'MCP隔离：{len(configs)} 个服务进程核验有效配置覆盖及服务清单，当前隔离判定为 {"通过" if isolation else "未通过"}。隔离只作用于评测子进程，格式技能仍可使用；工具目录可能列出已明确禁用的服务，不能把目录行数当作活动连接数。',
         f'另记录 {len(auxiliary)} 条辅助 MCP 连接／认证警告或错误，影响 {len(auxiliary_runs)} 个已关联回合。它们不算模型重试；相关原始日志保留，auxiliary-events.json 可独立复核。后台连接问题不能归因于 Skill。',
         f'后台模型目录刷新另有 {len(catalog)} 条失败记录，关联 {len(catalog_runs)} 个回合，见 background-events.json；不属于MCP或模型采样重试。只按错误结束时间关联，缺少完整请求起止时间，未关联也不证明没有干扰。',
         'Python审计不是系统级监控；哈希读取按缓存工具 content_hash 调用栈识别，其他源读取包含解析、普通读取及手写 hashlib 读取，Shell直接读取可能不在计数内。首次转换前后各校验源内容，用于拒绝转换期间文件变化；表中总数包括必要校验与额外查询，不等同于冗余次数。手写校验另用命令线索记录。SHA-256会读取源字节，但不会重新解析格式或把二进制送入模型。','',
         '## 工具能力与实际调用','',
         '| 版本 | 检索器命令 | 来源恢复合并调用 | 完整缓存打印线索 | 手写哈希命令 | 外部格式技能输出字符 |',
         '| --- | --- | --- | --- | --- | --- |']
    for group,counts in behavior.items():
        md.append(f'| {protocol["versions"][group]} | '+ ' | '.join(number(counts[k],0) for k in
            ['query_commands','source_record_query_commands','full_cache_dump_commands','manual_hash_commands','format_skill_output_chars'])+' |')
    md+=['','这些是命令文本的诊断线索，不是新的成功判分规则，也不是系统级读取计数。一次命令可能合并多个操作。外部格式技能的加载成本与原件正文、检索器返回证据分别看待。',
         '有限额的检索器不保证代理每次都优先调用；轨迹中若仍出现完整缓存打印或重复手写哈希，应作为后续调用策略改进对象。','',
         '## 失败与观察','']
    failures=[r for r in rows if not r['success']]
    if failures:
        for r in failures:md.append('- '+r['id']+'：'+', '.join(c['name'] for c in r['checks'] if not c['pass']))
    else:md.append('本轮全部任务通过既定判分；这不等于未知任务零错误。')
    observations=root/'observations.json'
    if observations.exists():
        for o in json.loads(observations.read_text(encoding='utf-8')):
            md.append('- '+o['run_id']+'：'+o['finding'])
    translated_limits={
        'Three independently varied synthetic material sets, not a population benchmark':'主要资料场景使用三套独立变化的合成材料；重点场景预登记的追加次数见分场景表，仍不能代表所有真实项目。',
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
         '- 论文：[Lost in the Middle 的摘要](https://arxiv.org/abs/2307.03172)。参考减少无关长上下文的动机；论文模型与本轮不同，不借用其效果作为本 Skill 的实测。',
         '- 工具设计：[Anthropic 工具设计实践](https://www.anthropic.com/engineering/writing-tools-for-agents)。参考紧凑／详细输出分档与真实轨迹评测；没有引入额外模型摘要。',
         '- 隔离配置：[Codex 配置参考](https://developers.openai.com/codex/config-reference/)。使用进程级 enabled 覆盖，保留用户日常配置。','',
         '- 来源恢复：[LongMemEval 摘要](https://arxiv.org/abs/2410.10813)与[官方开源评测](https://github.com/xiaowu0162/LongMemEval)。参考把索引、检索、读取区分开及记录证据来源标识的设计；映射到本项目的产物 sources 是适配推断，没有运行或借用其基准性能。',
         '- 按需上下文：[Anthropic 上下文工程实践](https://www.anthropic.com/research/effective-context-engineering-for-ai-agents)。参考轻量文件标识和按需加载；用最近相关产物恢复集合，效果仍由本次真实轨迹验证。','',
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
