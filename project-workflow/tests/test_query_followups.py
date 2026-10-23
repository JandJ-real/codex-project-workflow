"""Catch extra queries caused by plural labels and compact output on other drives."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import document_query as query


class FollowupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.source=self.root/'limits.csv'
        self.source.write_text('GPU-hour ceiling,37\nExport encoding,UTF-8\n',encoding='utf-8')

    def test_natural_query_hours_finds_singular_hour_label_without_changing_literal_coverage(self):
        result=query.query_documents([self.source],[],self.root/'cache',query='supplemental budget hours')
        self.assertTrue(result['matches'],'Plural hours must retrieve the GPU-hour row')
        self.assertIn('GPU-hour ceiling',result['matches'][0]['text'])
        self.assertIn('37',result['matches'][0]['text'])
        literal=query.query_documents([self.source],['hours'],self.root/'cache')
        self.assertEqual(literal['coverage']['unmatched_terms'],['hours'])
        self.assertEqual(literal['matches'],[])

    def test_compact_result_uses_absolute_source_when_relative_path_is_unavailable(self):
        result=query.query_documents([self.source],['GPU'],self.root/'cache')
        with mock.patch.object(query.os.path,'relpath',side_effect=ValueError('different drive')):
            try:compact=query.concise(result)
            except ValueError as error:self.fail('Cross-drive evidence must remain readable: '+str(error))
        self.assertEqual(compact['documents'][0]['source'],self.source.as_posix())
        self.assertIn('37',compact['matches'][0]['text'])
        self.assertEqual(compact['matches'][0]['location'],{'row':1})


if __name__=='__main__':unittest.main()
