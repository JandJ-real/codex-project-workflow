"""Passive Python I/O telemetry; records paths and call class, never file bodies."""
import json
import os
import sys
import time

_root = os.environ.get('PW_EVAL_WORKSPACE')
_log = os.environ.get('PW_EVAL_IO_LOG')
_sources = set(json.loads(os.environ.get('PW_EVAL_SOURCES','[]')))
if _root and _log:
    def _audit(event,args):
        if event!='open' or not args or not isinstance(args[0],(str,bytes,os.PathLike)):
            return
        try:
            relative=os.path.relpath(os.path.abspath(os.fsdecode(args[0])),_root).replace('\\','/')
            if relative not in _sources and not relative.startswith('.ai-cache/'):
                return
            kind='cache' if relative.startswith('.ai-cache/') else 'source_read'
            frame=sys._getframe()
            while frame:
                if frame.f_code.co_name=='content_hash':
                    kind='source_hash' if relative in _sources else 'cache_hash'
                    break
                frame=frame.f_back
            with open(_log,'a',encoding='utf-8') as stream:
                stream.write(json.dumps({'time_ns':time.time_ns(),'path':relative,'kind':kind,
                                         'mode':str(args[1]) if len(args)>1 else None})+'\n')
        except Exception:
            pass
    sys.addaudithook(_audit)
