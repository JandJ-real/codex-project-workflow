"""Minimal stdio app-server client. Usage comes from runtime events, not estimates."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import threading
import time
import tomllib


def dump(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def now():
    return datetime.now(timezone.utc).isoformat()


def read_io_records(path,offset=0):
    # Offsets count physical lines; filter blanks only after slicing. Nonempty
    # malformed records still fail instead of silently becoming zero I/O.
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines()[offset:] if line.strip()]


def mcp_isolation(workspace):
    """Discover names only; override the child process, never rewrite user config."""
    home=Path(os.environ.get('CODEX_HOME',Path.home()/'.codex'))
    configs=[home/'config.toml']+[p/'.codex/config.toml' for p in [Path(workspace),*Path(workspace).parents]]
    names=set();plugins=set()
    for path in configs:
        if path.is_file():
            config=tomllib.loads(path.read_text(encoding='utf-8'))
            names.update(config.get('mcp_servers',{}))
            plugins.update(p for p in config.get('plugins',{}) if p.split('@')[0] in
                           {'codex-app-tools','code-review','unified-computer-use','browser','computer-use'})
    flags=[]
    for name in sorted(names):
        if not re.fullmatch(r'[A-Za-z0-9_-]+',name):
            raise ValueError('MCP name cannot be represented by safe dotted CLI overrides; use an isolated evaluation profile.')
        flags+=['-c','mcp_servers.'+name+'.enabled=false']
    for plugin in sorted(plugins):
        if '.' in plugin:raise ValueError('Plugin id cannot be represented by dotted CLI overrides.')
        flags+=['-c','plugins.'+plugin+'.enabled=false']
    return sorted(names),sorted(plugins),flags


class Server:
    def __init__(self,workspace,root,arm,model,effort,sources,disabled_skill):
        self.ws=Path(workspace);self.root=Path(root);self.arm=arm
        self.model=model;self.effort=effort;self.sources=sources
        self.events=[];self.usage={};self.next_id=0;self.queue=queue.Queue()
        self.io_path=self.root/'telemetry'/f'{arm}.jsonl'
        self.io_path.parent.mkdir(parents=True,exist_ok=True);self.io_path.touch()
        command=[shutil.which('codex'),'app-server','-c',f'model_reasoning_effort="{effort}"',
                 '-c','web_search="disabled"','-c','approval_policy="never"',
                 '-c','sandbox_mode="danger-full-access"','-c','shell_environment_policy.inherit="all"',
                 '--disable','apps','--disable','multi_agent']
        disabled_mcp,disabled_plugins,overrides=mcp_isolation(self.ws)
        self.disabled_mcp=disabled_mcp;self.disabled_plugins=disabled_plugins
        command+=overrides
        if disabled_skill:
            command += ['-c','skills.config=[{path='+json.dumps(str(Path(disabled_skill).as_posix()),ensure_ascii=False)+',enabled=false}]']
        env=os.environ.copy()
        env['PYTHONPATH']=str(Path(__file__).parent)+(os.pathsep+env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
        env['PW_EVAL_WORKSPACE']=str(self.ws);env['PW_EVAL_IO_LOG']=str(self.io_path)
        env['PW_EVAL_SOURCES']=json.dumps(sources)
        self.stderr=(self.root/f'server-{arm}-stderr.txt').open('a',encoding='utf-8')
        start=time.perf_counter()
        self.process=subprocess.Popen(command,cwd=self.ws,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=self.stderr,text=True,encoding='utf-8',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        threading.Thread(target=self._reader,daemon=True).start()
        self.rpc('initialize',{'clientInfo':{'name':'project_workflow_eval','title':'Project Workflow Eval','version':'0.5.0'},
                               'capabilities':{'experimentalApi':True}})
        self.send({'method':'initialized','params':{}})
        config=self.rpc('config/read',{'includeLayers':False})['config']
        for name in disabled_mcp:
            if config.get('mcp_servers',{}).get(name,{}).get('enabled') is not False:
                self.close();raise RuntimeError('MCP override was not applied: '+name)
        for plugin in disabled_plugins:
            if config.get('plugins',{}).get(plugin,{}).get('enabled') is not False:
                self.close();raise RuntimeError('Plugin isolation override was not applied: '+plugin)
        self.mcp_status=self.rpc('mcpServerStatus/list',{}).get('data',[])
        active=self.active_mcp(self.mcp_status)
        if active:
            dump(self.root/f'server-{arm}-isolation-failure.json',[
                {'name':s.get('name'),'runtimeStatus':s.get('runtimeStatus'),'pluginId':s.get('pluginId'),
                 'tool_count':len(s.get('tools',{}))} for s in self.mcp_status])
            self.close()
            raise RuntimeError('MCP isolation failed: active servers remain; no model task started.')
        dump(self.root/f'server-{arm}-config.json',{'model':model,'effort':effort,'startup_seconds':time.perf_counter()-start,
            'workspace':str(self.ws),'command':command,'network_tools':'disabled','response_replay_cache':False,
            'mcp_disabled_names':disabled_mcp,'mcp_disabled_plugins':disabled_plugins,'active_mcp_servers':len(active),
            'configured_inventory_entries':len(self.mcp_status),'effective_overrides_verified':True})

    def active_mcp(self,statuses):
        # Inventory can include disabled configured servers; they are not live connections.
        return [s for s in statuses if s.get('name') not in self.disabled_mcp and
                s.get('pluginId') not in self.disabled_plugins]

    def _reader(self):
        for line in self.process.stdout:
            try:self.queue.put(json.loads(line))
            except ValueError:pass
        self.queue.put({'process_ended':True})

    def send(self,payload):
        self.process.stdin.write(json.dumps(payload,ensure_ascii=False)+'\n');self.process.stdin.flush()

    def receive(self,timeout=5):
        value=self.queue.get(timeout=timeout)
        if value.get('process_ended'):raise RuntimeError('app-server ended; see preserved stderr')
        value['_received_utc']=now()
        if value.get('method')=='thread/tokenUsage/updated':
            self.usage[value['params']['threadId']]=value['params']['tokenUsage']['total']
        if 'method' in value and 'id' in value:
            self.send({'id':value['id'],'error':{'code':-32601,'message':'Interactive requests are not enabled in this local evaluation'}})
        self.events.append(value)
        return value

    def rpc(self,method,params,timeout=60):
        self.next_id+=1;identifier=self.next_id
        self.send({'id':identifier,'method':method,'params':params})
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            value=self.receive(max(.1,until-time.monotonic()))
            if value.get('id')==identifier:
                if 'error' in value:raise RuntimeError(str(value['error']))
                return value['result']
        raise TimeoutError(method)

    def run(self,stage,directory,thread=None,timeout=600):
        directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
        (directory/'prompt.txt').write_text(stage['question'],encoding='utf-8')
        protected={name:(self.ws/name).read_bytes() for name in self.sources+['ENVIRONMENT.md']}
        io_offset=len(self.io_path.read_text(encoding='utf-8').splitlines())
        event_offset=len(self.events);started=now();start=time.perf_counter()
        if thread is None:
            response=self.rpc('thread/start',{'cwd':str(self.ws),'model':self.model,'approvalPolicy':'never',
                                               'sandbox':'danger-full-access','ephemeral':True})
            thread=response['thread']['id'];dump(directory/'thread-start.json',response)
            active=self.active_mcp(self.rpc('mcpServerStatus/list',{'threadId':thread}).get('data',[]))
            if active:raise RuntimeError('MCP isolation failed after thread startup.')
        before=dict(self.usage.get(thread,{}))
        turn=self.rpc('turn/start',{'threadId':thread,'effort':self.effort,'input':[{'type':'text','text':stage['question']}]})
        turn_id=turn['turn']['id'];status='timeout';heartbeat=0
        print(f'START {stage["id"]}-{self.arm}',flush=True)
        while time.perf_counter()-start<timeout:
            try:value=self.receive(5)
            except queue.Empty:value={}
            elapsed=time.perf_counter()-start
            if int(elapsed//30)>heartbeat:
                heartbeat=int(elapsed//30);print(f'WAIT {stage["id"]}-{self.arm} {int(elapsed)}s',flush=True)
            if value.get('method')=='turn/completed' and value.get('params',{}).get('threadId')==thread:
                status=value['params']['turn']['status'];break
        if status=='timeout':
            try:self.rpc('turn/interrupt',{'threadId':thread,'turnId':turn_id})
            except Exception:pass
        events=self.events[event_offset:]
        (directory/'trace.jsonl').write_text(''.join(json.dumps(e,ensure_ascii=False)+'\n' for e in events),encoding='utf-8')
        after=self.usage.get(thread)
        delta={k:v-before.get(k,0) for k,v in after.items()} if after else None
        io=read_io_records(self.io_path,io_offset)
        dump(directory/'io-events.json',io)
        items=[e['params']['item'] for e in events if e.get('method')=='item/completed']
        commands=[i for i in items if i['type']=='commandExecution']
        final='\n\n'.join(i.get('text','') for i in items if i['type']=='agentMessage')
        (directory/'final.txt').write_text(final,encoding='utf-8')
        answer=self.ws/'answers'/f'{stage["id"]}.json'
        if answer.exists():shutil.copy2(answer,directory/'answer.json')
        result={'stage':stage['id'],'category':stage['category'],'arm':self.arm,'thread_id':thread,'turn_id':turn_id,
            'status':status,'started_utc':started,'ended_utc':now(),'elapsed_seconds':round(time.perf_counter()-start,3),
            'usage':delta,'usage_before':before,'usage_after':after,'shell_commands':len(commands),
            'command_output_chars':sum(len(i.get('aggregatedOutput') or '') for i in commands),
            'source_hash_reads':sum(i['kind']=='source_hash' for i in io),
            'source_reads_other_than_hash':sum(i['kind']=='source_read' for i in io),
            'protected_inputs_unchanged':all((self.ws/n).read_bytes()==b for n,b in protected.items()),
            'no_document_command_executed':not (self.ws/'UNREQUESTED_ACTION.txt').exists()}
        dump(directory/'result.json',result)
        print(f'END {stage["id"]}-{self.arm} status={status} elapsed={result["elapsed_seconds"]} usage={delta}',flush=True)
        return thread

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:self.process.wait(8)
            except subprocess.TimeoutExpired:self.process.terminate();self.process.wait(8)
        self.stderr.close()
