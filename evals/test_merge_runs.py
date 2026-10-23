"""Test metadata provenance only; these fixtures are not model benchmark results."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.script=Path(__file__).with_name('merge_runs.py')

    def fixture(self,name):
        root=self.root/name;root.mkdir()
        for arm,version in [('A','2.4.0'),('B','2.5.0')]:
            p=root/'snapshots'/arm;p.mkdir(parents=True)
            (p/'SKILL.md').write_text(version)
            d=root/'runs'/f'D1-R1-{arm}';d.mkdir(parents=True)
            (d/'result.json').write_text(json.dumps({'stage':'D1','arm':f'R1-{arm}','usage':{'inputTokens':13}}))
        p={'versions':{'A':'2.4.0','B':'2.5.0'},'model':'test_metadata_only','effort':'xhigh',
           'repeats':1,'suites':{'1':{'stages':[{'id':'D1'}]}},
           'schedule':[{'id':f'D1-R1-{a}','repeat':1,'group':a,'stage':'D1'} for a in ['A','B']]}
        (root/'protocol.json').write_text(json.dumps(p));return root

    def merger(self):
        self.assertTrue(self.script.exists(),'Provenance-preserving append tool is missing')
        spec=importlib.util.spec_from_file_location('merge_runs',self.script)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module.merge

    def test_append_preserves_original_records_and_disambiguates_repeat_ids(self):
        a,b=self.fixture('main'),self.fixture('supplement')
        original=(b/'runs/D1-R1-A/result.json').read_bytes()
        out=self.root/'combined';self.merger()([a,b],out)
        p=json.loads((out/'protocol.json').read_text())
        self.assertEqual({s['id'] for s in p['schedule']},{'D1-R1-A','D1-R1-B','D1-R2-A','D1-R2-B'})
        r=json.loads((out/'runs/D1-R2-A/result.json').read_text())
        self.assertEqual(r['arm'],'R2-A');self.assertEqual(r['usage']['inputTokens'],13)
        self.assertEqual((b/'runs/D1-R1-A/result.json').read_bytes(),original)
        self.assertEqual(len(json.loads((out/'provenance.json').read_text())),4)

    def test_different_candidate_is_rejected_before_output_is_created(self):
        a,b=self.fixture('main'),self.fixture('supplement')
        (b/'snapshots/B/SKILL.md').write_text('different candidate')
        out=self.root/'combined'
        with self.assertRaises(ValueError):self.merger()([a,b],out)
        self.assertFalse(out.exists())

    def test_output_inside_source_is_rejected_to_prevent_recursive_copy(self):
        a,b=self.fixture('main'),self.fixture('supplement')
        out=a/'workspaces/R1-A/analysis'
        merger=self.merger()
        # The missing guard previously caused real recursive copying. Limit that
        # hazardous dependency side effect if this regression is reintroduced.
        with mock.patch.object(merger.__globals__['shutil'],'copytree',
                               side_effect=RuntimeError('Invalid output must not start copying')):
            with self.assertRaises(ValueError):merger([a,b],out)
        self.assertFalse(out.exists())


if __name__=='__main__':unittest.main()
