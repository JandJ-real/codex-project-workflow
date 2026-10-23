"""Catch oversized evidence and repeated validation, without trusting stale stat data."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from document_cache import cache_document
from document_query import query_documents


class QueryEfficiencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root/'cache'

    def cli(self, *args, env=None):
        run = subprocess.run([sys.executable, str(SCRIPTS/'document_query.py'), *map(str,args),
                              '--cache-dir',str(self.cache)], cwd=self.root,
                             capture_output=True,text=True,encoding='utf-8',env=env)
        self.assertEqual(run.returncode,0,run.stderr)
        return json.loads(run.stdout),run.stdout

    def test_default_cli_keeps_quotes_locations_and_gaps_without_repeated_paths(self):
        source=self.root/'image.bin';source.write_bytes(b'opaque image')
        md=self.root/'ocr.md';md.write_text('<!-- loc {"page": 3} -->\nBudget is 17 hours.\n',encoding='utf-8')
        cache_document(source,self.cache,from_markdown=md,review_locations=['page 4 unreadable'])
        result,wire=self.cli(source,'--term','Budget','--term','absent')
        self.assertEqual(result['matches'][0].get('doc'),0)
        self.assertEqual(result['matches'][0]['location'],{'page':3})
        self.assertIn('Budget is 17 hours.',result['matches'][0]['text'])
        self.assertIn('page 4 unreadable',result['documents'][0]['quality_info']['known_gaps'])
        self.assertEqual(result['coverage']['unmatched_terms'],['absent'])
        self.assertEqual(wire.count('image.bin'),1)
        self.assertNotIn('score',result['matches'][0])

    def test_page_query_does_not_return_irrelevant_paragraphs_with_its_answer(self):
        source=self.root/'page.bin';source.write_bytes(b'page')
        md=self.root/'page.md'
        md.write_text('## PDF page 2\n\nLive latency is 71 ms. Offline exception is 231 ms.\n\n'+
                      '\n\n'.join('Background note about a historical meeting.' for _ in range(25)),encoding='utf-8')
        cache_document(source,self.cache,from_markdown=md)
        r=query_documents([source],[],self.cache,query='live latency')
        self.assertEqual(r['matches'][0]['location'],{'page':2})
        self.assertIn('Offline exception is 231 ms.',r['matches'][0]['text'])
        self.assertLess(len(r['matches'][0]['text']),160)

    def test_batch_uses_one_checked_snapshot_for_multiple_queries(self):
        source=self.root/'values.txt';source.write_text('Budget is 17 hours.\nLatency is 71 ms.',encoding='utf-8')
        cache_document(source,self.cache)
        batch=self.root/'questions.json';batch.write_text(json.dumps([
            {'id':'budget','terms':['Budget']},{'id':'latency','terms':['Latency']}]),encoding='utf-8')
        env=os.environ.copy();audit_dir=self.root/'audit';audit_dir.mkdir()
        (audit_dir/'sitecustomize.py').write_text('''import json,sys,os
def audit(event,args):
    if event=='open' and args and str(args[0])==os.environ['CHECK_SOURCE']:
        with open(os.environ['CHECK_LOG'],'a') as log:log.write('read\\n')
sys.addaudithook(audit)
''',encoding='utf-8')
        env.update(PYTHONPATH=str(audit_dir),CHECK_SOURCE=str(source),CHECK_LOG=str(self.root/'reads.txt'))
        result,_=self.cli(source,'--batch',batch,env=env)
        self.assertEqual([r['id'] for r in result['results']],['budget','latency'])
        self.assertIn('17',result['results'][0]['matches'][0]['text'])
        self.assertIn('71',result['results'][1]['matches'][0]['text'])
        self.assertEqual((self.root/'reads.txt').read_text().splitlines(),['read'])
        # No persisted trust shortcut: a new invocation detects a same-stat edit.
        stat=source.stat();source.write_text('Budget is 39 hours.\nLatency is 71 ms.',encoding='utf-8')
        os.utime(source,ns=(stat.st_atime_ns,stat.st_mtime_ns))
        new,_=self.cli(source,'--batch',batch)
        self.assertIn('39',new['results'][0]['matches'][0]['text'])
        self.assertNotIn('17',new['results'][0]['matches'][0]['text'])


if __name__=='__main__':unittest.main()
