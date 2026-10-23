"""Append complete real evaluations without replacing samples or changing sources."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil


def read(path):return json.loads(path.read_text(encoding='utf-8'))
def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def signature(root):
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*')
            if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}


def merge(roots,output):
    roots=[Path(r).resolve() for r in roots];output=Path(output).resolve()
    if any(output==r or r in output.parents for r in roots):
        raise ValueError('Analysis directory must be outside source evaluations to prevent recursive copying.')
    if output.exists():raise ValueError('Use a new analysis directory; no original evaluation is overwritten.')
    protocols=[read(r/'protocol.json') for r in roots];base=protocols[0]
    reference=signature(roots[0]/'snapshots')
    plans=[];last=0
    for root,protocol in zip(roots,protocols):
        if any(protocol[k]!=base[k] for k in ['model','effort','versions']) or signature(root/'snapshots')!=reference:
            raise ValueError('Model settings or frozen skill snapshots differ.')
        if any(not (root/'runs'/r['id']/'result.json').exists() for r in protocol['schedule']):
            raise ValueError('Evaluation incomplete; preserve it and wait for remaining records.')
        mapping={old:last+i for i,old in enumerate(sorted(protocol['suites'],key=int),1)}
        last+=len(mapping);plans.append((root,protocol,mapping))
    output.mkdir(parents=True)
    shutil.copytree(roots[0]/'snapshots',output/'snapshots',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    schedule=[];suites={};provenance=[];exclusions=[];observations=[]
    for root,protocol,mapping in plans:
        for old,new in mapping.items():
            suites[str(new)]=protocol['suites'][old]
            fixtures=root/'fixtures'/f'R{old}'
            if fixtures.exists():shutil.copytree(fixtures,output/'fixtures'/f'R{new}')
            for group in ['A','B']:
                oldarm=f'R{old}-{group}';newarm=f'R{new}-{group}'
                ws=Path(protocol.get('workspace_overrides',{}).get(oldarm,root/'workspaces'/oldarm))
                if ws.exists():shutil.copytree(ws,output/'workspaces'/newarm,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
                for path in root.glob(f'server-{oldarm}-*'):
                    shutil.copy2(path,output/path.name.replace(f'server-{oldarm}-',f'server-{newarm}-',1))
        for item in protocol['schedule']:
            new=mapping[str(item['repeat'])];identifier=f'{item["stage"]}-R{new}-{item["group"]}'
            schedule.append({**item,'id':identifier,'repeat':new})
            destination=output/'runs'/identifier
            shutil.copytree(root/'runs'/item['id'],destination)
            record=read(destination/'result.json')
            record.update(id=identifier,arm=f'R{new}-{item["group"]}')
            if 'repeat' in record:record['repeat']=new
            write(destination/'result.json',record)
            provenance.append({'id':identifier,'source_root':str(root),'source_id':item['id']})
        if (root/'operational-exclusions.json').exists():
            for e in read(root/'operational-exclusions.json')['pairs']:
                exclusions.append({**e,'repeat':mapping[str(e['repeat'])]})
        if (root/'observations.json').exists():
            for entry in read(root/'observations.json'):
                entry=dict(entry);match=re.fullmatch(r'([A-Z]\d+)-R(\d+)-([AB])',entry['run_id'])
                if match:entry['run_id']=f'{match[1]}-R{mapping[match[2]]}-{match[3]}'
                observations.append(entry)
    write(output/'protocol.json',{**base,'analysis_only':True,'repeats':last,'suites':suites,'schedule':schedule,
                                 'merged_sources':[str(r) for r in roots]})
    write(output/'provenance.json',provenance)
    write(output/'operational-exclusions.json',{'pairs':exclusions})
    write(output/'observations.json',observations)
    return output


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('roots',nargs='+',type=Path);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();print(merge(a.roots,a.output))
