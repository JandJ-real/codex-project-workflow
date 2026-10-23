"""Metric parsing must not count file discovery as an actual query invocation."""
import unittest
import json
from pathlib import Path
import tempfile
import report
import harness
import run_eval
from run_eval import network


class RuntimeMetricTests(unittest.TestCase):
    def test_io_blank_lines_preserve_raw_line_offsets_and_bad_records_fail(self):
        reader=getattr(harness,'read_io_records',None)
        self.assertIsNotNone(reader,'I/O log reader is missing')
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'audit.jsonl'
            path.write_text('{"kind":"cache"}\n\n{"kind":"source_hash"}\n',encoding='utf-8')
            self.assertEqual(reader(path,2),[{'kind':'source_hash'}])
            path.write_text('not-json\n',encoding='utf-8')
            with self.assertRaises(json.JSONDecodeError):reader(path,0)

    def test_missing_elapsed_stays_unknown_and_remote_duration_matches_turn(self):
        ratio=getattr(run_eval,'safe_ratio',None)
        remote=getattr(run_eval,'remote_turn_seconds',None)
        self.assertIsNotNone(ratio);self.assertIsNotNone(remote)
        self.assertIsNone(ratio(1,None));self.assertIsNone(ratio(None,1));self.assertIsNone(ratio(1,0))
        self.assertEqual(ratio(2,4),0.5)
        events=[{'method':'turn/completed','params':{'turn':{'id':'other','durationMs':1000}}},
                {'method':'turn/completed','params':{'turn':{'id':'wanted','durationMs':74904}}}]
        self.assertEqual(remote(events,'wanted'),74.904)
        self.assertIsNone(remote(events,'absent'))

    def test_query_invocation_excludes_listing_and_help(self):
        checker=getattr(report,'query_invoked',None)
        self.assertIsNotNone(checker,'Query invocation classifier is missing')
        self.assertTrue(checker("& 'C:/runtime/python.exe' '.agents/skills/project-workflow/scripts/document_query.py' matrix.xlsx --query hours"))
        self.assertFalse(checker("Get-Content document-cache.md; rg --files -g 'document_query.py'"))
        self.assertFalse(checker("python document_query.py --help"))

    def test_cache_line_slices_are_not_reported_as_whole_cache_printing(self):
        checker=getattr(report,'full_cache_dump_invoked',None)
        self.assertIsNotNone(checker,'Whole-cache display classifier is missing')
        self.assertFalse(checker("$serviceText = Get-Content -LiteralPath '.ai-cache/documents/service.md'; $serviceText[34..63]; $serviceText[204..237]"))
        self.assertTrue(checker("Get-Content -Raw '.ai-cache/documents/service.md'"))
        self.assertTrue(checker("$serviceText = Get-Content '.ai-cache/documents/service.md'; $serviceText"))
        self.assertFalse(checker("Get-Content '.ai-cache/documents/service.md' | Select-Object -Skip 20 -First 10"))

    def rpc_error_records(self,code,retry):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        root=Path(temp.name);run=root/'runs/T1-R1-B';run.mkdir(parents=True)
        (run/'result.json').write_text(json.dumps({'arm':'R1-B','turn_id':'turn-test',
            'started_utc':'2026-10-23T02:07:54+00:00','ended_utc':'2026-10-23T02:08:45+00:00'}),encoding='utf-8')
        error={'message':'test fixture','codexErrorInfo':code}
        events=[{'method':'error','params':{'error':error,'willRetry':retry,'turnId':'turn-test'},
                 '_received_utc':'2026-10-23T10:08:44+08:00'},
                {'method':'turn/completed','params':{'turn':{'id':'turn-test','status':'failed','error':error}}}]
        (run/'trace.jsonl').write_text('\n'.join(json.dumps(e) for e in events),encoding='utf-8')
        return root

    def test_server_capacity_error_is_recorded_without_inventing_retry(self):
        root=self.rpc_error_records('serverOverloaded',False)
        events=network(root)
        self.assertEqual(len(events),1,'RPC error and turn/completed must not be double counted')
        self.assertEqual(events[0]['category'],'service_capacity_error')
        self.assertFalse(events[0]['is_retry'])
        self.assertEqual(events[0]['run_id'],'T1-R1-B')
        result=json.loads((root/'runs/T1-R1-B/result.json').read_text(encoding='utf-8'))
        self.assertFalse(result['observed_network_clean'])

    def test_rpc_explicit_retry_is_distinct_from_terminal_failure(self):
        root=self.rpc_error_records('httpConnectionFailed',True)
        events=network(root)
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]['category'],'connection_error')
        self.assertTrue(events[0]['is_retry'])

    def test_model_catalog_timeout_stays_separate_from_mcp_and_sampling(self):
        collector=getattr(report,'background_catalog_events',None)
        self.assertIsNotNone(collector,'Background catalog classification is missing')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);stamp='2026-10-23T02:08:44Z'
            records=[{'timestamp':stamp,'level':'ERROR','target':'codex_models_manager::manager',
                      'fields':{'message':'failed to refresh available models: request timed out'}},
                     {'timestamp':stamp,'level':'ERROR','target':'rmcp::transport',
                      'fields':{'message':'connection timeout'}},
                     {'timestamp':stamp,'level':'WARN','target':'codex_core::responses_retry',
                      'fields':{'message':'retrying sampling request'}}]
            (root/'server-R1-B-stderr.txt').write_text('\n'.join(json.dumps(r) for r in records),encoding='utf-8')
            rows=[{'arm':'R1-B','id':'T1-R1-B','started_utc':'2026-10-23T02:07:54+00:00','ended_utc':'2026-10-23T02:08:45+00:00'}]
            catalog=collector(root,rows)
            self.assertEqual(len(catalog),1)
            self.assertEqual(catalog[0]['category'],'background_model_catalog_refresh')
            self.assertEqual(catalog[0]['run_id'],'T1-R1-B')
            self.assertEqual(len(report.auxiliary_events(root,rows)),1)


if __name__=='__main__':unittest.main()
