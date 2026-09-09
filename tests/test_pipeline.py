"""Synthetic contract tests; no test scores are published as leaderboard data."""
from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from pipeline.assemble import Registry, assemble
from pipeline.identity import normalize_name, parse_name, taxonomy
from pipeline.render import build_files
from pipeline.sources import (SourceError, collect_aa, metadata_from_zip,
                              normalize_aa, normalize_epoch, number)
from run import main

ROOT = Path(__file__).resolve().parents[1]
ALIASES = {'aa_to_epoch': {}, 'creators': {}, 'names': {}, 'series': {}, 'separate_aa_slugs': []}
CONFIG = {'usd_cny': 7, 'matching': {'date_tolerance_days': 3}}


def aa_row(i='a1', name='Sample Atlas 1 (high)', score=45, cost=.5, date='2026-01-01', creator='Sample Labs'):
    return {'id': i, 'name': name, 'slug': i, 'release_date': date,
            'model_creator': {'name': creator},
            'evaluations': {'artificial_analysis_intelligence_index': score},
            'artificial_analysis_intelligence_index_cost': {'cost_per_task': {'total_cost': cost}}}


def body(rows):
    return {'data': rows, 'intelligence_index_version': 4.3}


def meta(group='Sample Atlas 1', date='2026-01-01', version='Sample Atlas 1 high'):
    return {'model_group': group, 'model_version': version, 'date': date, 'organization': 'Sample Labs'}


def score(group='Sample Atlas 1', eci=140, date='2026-01-01'):
    return {'model': group, 'eci': str(eci), 'date': date}


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
    def tearDown(self):
        self.temp.cleanup()
    def joined(self, aa_rows, scores=None, metadata=None, aliases=None, registry=None):
        aliases = aliases or copy.deepcopy(ALIASES)
        aa = normalize_aa(body(aa_rows), aliases)
        ep = normalize_epoch(scores or [score()], metadata or [meta()], aliases)
        reg = registry or Registry(self.path/'state.sqlite')
        result = assemble(aa, ep, reg, aliases, CONFIG, 'test', {})
        if not registry: reg.close()
        return result

    def test_full_outer_union_and_nulls(self):
        result, audit = self.joined([aa_row(), aa_row('a2', 'AA Only 2', None, None)],
                                  [score(), score('Epoch Only 3', 130)], [meta(), meta('Epoch Only 3')])
        self.assertEqual(len(result['families']), 3)
        self.assertEqual(audit['coverage']['source_rows_lost'], 0)
        self.assertEqual(audit['coverage']['aa_output_configurations'], 2)
        self.assertEqual(audit['coverage']['epoch_output_groups'], 2)

    def test_configurations_not_cross_product(self):
        result, audit = self.joined([aa_row('a1'), aa_row('a2', 'Sample Atlas 1 (max)', 47, 1)])
        self.assertEqual(len(result['families']), 1)
        self.assertEqual(len(result['families'][0]['configurations']), 2)
        self.assertNotIn('eci', result['families'][0]['configurations'][0])

    def test_missing_cost_stays_missing_and_zero_stays_zero(self):
        rows = normalize_aa(body([aa_row('a1', cost=0), aa_row('a2', cost=None)]), ALIASES)
        self.assertEqual(rows[0]['cost_usd'], 0)
        self.assertIsNone(rows[1]['cost_usd'])
        self.assertEqual(number(0, 'x'), 0)

    def test_revision_dates_do_not_merge(self):
        r, _ = self.joined([aa_row(date='2026-06-01')])
        self.assertEqual(len(r['families']), 2)

    def test_small_date_difference_matches(self):
        r, _ = self.joined([aa_row(date='2026-01-02')])
        self.assertEqual(len(r['families']), 1)

    def test_pro_and_flash_are_not_effort(self):
        self.assertNotEqual(normalize_name('Atlas Pro'), normalize_name('Atlas'))
        self.assertNotEqual(normalize_name('GLM 5.3 Flash'), normalize_name('GLM 5.3'))
        self.assertNotEqual(normalize_name('Qwen 3.1 8B'), normalize_name('Qwen 3.1 80B'))
        self.assertNotEqual(normalize_name('Atlas 1.10'), normalize_name('Atlas 1.1'))

    def test_qualifier_and_version_parsing(self):
        self.assertEqual(parse_name('Atlas (max, default fallback)')[1], 'max, default fallback')
        self.assertEqual(parse_name('Atlas (preview)')[0], 'Atlas (preview)')
        self.assertEqual(parse_name('Atlas (2026-01-01) (high)')[:2], ('Atlas', 'high'))
        self.assertEqual(parse_name('Atlas (2026-01-01) (high)')[2], '2026-01-01')

    def test_compound_effort_paren_is_tier_not_family_name(self):
        base, effort, _ = parse_name('Atlas 5 (Adaptive Reasoning, Max Effort, Default Fallback)')
        self.assertEqual(base, 'Atlas 5')
        self.assertEqual(effort, 'Adaptive Reasoning, Max Effort, Default Fallback')
        self.assertEqual(parse_name('Atlas (Non-reasoning, High Effort)')[1], 'Non-reasoning, High Effort')

    def test_compound_paren_without_effort_stays_in_family_name(self):
        self.assertEqual(parse_name('Atlas (March 2025, atlas-latest)')[0], 'Atlas (March 2025, atlas-latest)')
        self.assertEqual(parse_name('Atlas (max, based on Atlas-mini)')[1], 'max, based on Atlas-mini')

    def test_compound_effort_joins_epoch_group(self):
        r, _ = self.joined([aa_row(name='Sample Atlas 1 (Adaptive Reasoning, Max Effort)')],
                           [score()], [meta()])
        self.assertEqual(len(r['families']), 1)
        self.assertEqual(r['families'][0]['eci'], 140)
        self.assertEqual(r['families'][0]['configurations'][0]['effort'], 'Adaptive Reasoning, Max Effort')

    def test_epoch_metadata_date_noise_merges_into_one_family(self):
        r, _ = self.joined([aa_row(date='2026-01-01')],
                           [score(date='2026-01-02')],
                           [meta(date='2026-01-01'), meta(date='2026-01-02')])
        self.assertEqual(len(r['families']), 1)
        self.assertEqual(r['families'][0]['eci'], 140)
        self.assertEqual(len(r['families'][0]['configurations']), 1)
        self.assertEqual(len(r['families'][0]['epoch_versions']), 2)

    def test_epoch_same_name_far_dates_stay_split(self):
        groups = normalize_epoch([score(), score('Atlas 2', 130, '2026-06-01')],
                                 [meta(), meta('Atlas 2', '2026-06-01')], ALIASES)
        self.assertEqual(len(groups), 2)

    def test_creator_aliases(self):
        self.assertEqual(taxonomy('GLM 5.3', 'Zhipu AI', ALIASES)[0], 'Z.ai')
        self.assertEqual(taxonomy('Gemini', 'Google DeepMind', ALIASES)[0], 'Google')

    def test_ambiguous_match_keeps_source_row(self):
        r, audit = self.joined([aa_row(date='')], [score(date='2026-01-01'), score(date='2026-06-01')],
                              [meta(date='2026-01-01'), meta(date='2026-06-01')])
        self.assertEqual(len(r['families']), 3)
        self.assertEqual(audit['coverage']['aa_output_configurations'], 1)

    def test_forced_alias_matches(self):
        aliases = copy.deepcopy(ALIASES)
        aliases['aa_to_epoch']['a1'] = 'Sample Atlas 1'
        r, _ = self.joined([aa_row(name='Other Display Name', date='2026-06-01')], aliases=aliases)
        self.assertEqual(len(r['families']), 1)

    def test_later_epoch_record_keeps_aa_identity(self):
        reg = Registry(self.path/'state.sqlite')
        first, _ = self.joined([aa_row(name='Future Model')], registry=reg)
        first_id = next(f['id'] for f in first['families'] if f['name']=='Future Model')
        reg.close()
        reg = Registry(self.path/'state.sqlite')
        second, _ = self.joined([aa_row(name='Future Model')], [score('Future Model')], [meta('Future Model')], registry=reg)
        second_id = second['families'][0]['id']
        reg.close()
        self.assertEqual(first_id, second_id)
        self.assertEqual(len(second['families']), 1)

    def test_all_metadata_including_unscored_groups_retained(self):
        r, audit = self.joined([aa_row()], [score()], [meta(), meta('Unscored Model')])
        self.assertEqual(len(r['families']), 2)
        self.assertEqual(audit['coverage']['epoch_metadata_versions'], 2)

    def test_api_collects_three_pages(self):
        def get(url, headers):
            page = int(parse_qs(urlparse(url).query)['page'][0])
            self.assertEqual(headers['x-api-key'], 'not-a-real-key')
            return json.dumps({'data':[aa_row(str(page))], 'intelligence_index_version':4.3,
                               'pagination':{'page':page,'total_pages':3,'has_more':page<3,'total_items':3}}).encode()
        result = collect_aa('not-a-real-key', self.path/'raw', get)
        self.assertEqual(len(result['data']), 3)
        self.assertEqual(result['_collection']['pages'], 3)
        self.assertNotIn('not-a-real-key', (self.path/'raw/aa-all.json').read_text())

    def test_duplicate_pagination_is_rejected(self):
        def get(url, headers):
            page = int(parse_qs(urlparse(url).query)['page'][0])
            return json.dumps({'data':[aa_row()], 'intelligence_index_version':4.3,
                               'pagination':{'page':page,'total_pages':2,'has_more':page<2}}).encode()
        with self.assertRaises(SourceError): collect_aa('x', self.path, get)

    def test_mid_fetch_version_change_is_rejected(self):
        def get(url, headers):
            page = int(parse_qs(urlparse(url).query)['page'][0])
            return json.dumps({'data':[aa_row(str(page))], 'intelligence_index_version':4.3 if page==1 else 5,
                               'pagination':{'page':page,'total_pages':2,'has_more':page<2}}).encode()
        with self.assertRaises(SourceError): collect_aa('x', self.path, get)

    def test_single_page_local_export_is_not_called_full(self):
        b=body([aa_row()]);b['pagination']={'total_pages':3,'has_more':True}
        with self.assertRaises(SourceError): normalize_aa(b, ALIASES)

    def test_html_escapes_embedded_script_and_keeps_all_rows(self):
        rows=[aa_row(str(i), f'Synthetic Model {i}') for i in range(651)]
        rows.append(aa_row('evil','</script><script>alert(1)</script>'))
        r, _ = self.joined(rows)
        r['meta']['generated_at']='2026-01-01T00:00:00+00:00'
        build_files(r,self.path/'site',ROOT/'web')
        html=(self.path/'site/index.html').read_text()
        self.assertNotIn('</script><script>alert(1)</script>',html)
        self.assertEqual(len(json.loads((self.path/'site/models.json').read_text())['families']),653)
        self.assertNotIn('__DATA__',html)
        self.assertIn('Synthetic Model 650',html)

    def test_zip_metadata(self):
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w') as z:
            z.writestr('model_metadata.csv','model_version,model_group,date\na,A,2026-01-01\n')
        self.assertEqual(metadata_from_zip(buf.getvalue())[0]['model_group'],'A')

    def test_zip_metadata_skips_blank_padding_rows(self):
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w') as z:
            z.writestr('model_metadata.csv',
                       'model_version,model_group,date\n,,,,,\na,A,2026-01-01\n,,,,,\n')
        rows=metadata_from_zip(buf.getvalue())
        self.assertEqual([r['model_group'] for r in rows],['A'])

    def test_metadata_row_with_content_but_no_identity_is_rejected(self):
        row={'model_version':'','model_group':'','date':'2026-01-01','display_name':'Ghost'}
        with self.assertRaises(SourceError): normalize_epoch([score()],[meta(),row],ALIASES)

    def test_corrupt_epoch_score_is_rejected(self):
        with self.assertRaises(SourceError): normalize_epoch([score(eci='oops')],[meta()],ALIASES)

    def test_family_cost_and_score_are_in_same_configuration(self):
        r, _ = self.joined([aa_row('high', score=50, cost=2), aa_row('low', 'Sample Atlas 1 (low)', 40, .1)])
        f=r['families'][0]
        self.assertEqual(f['configurations'][0]['aa'],50)
        self.assertEqual(f['configurations'][0]['cost_usd'],2)

    def test_failure_leaves_published_html_untouched(self):
        out=self.path/'dist';out.mkdir();(out/'index.html').write_text('previous')
        with patch.dict('os.environ',{'AA_API_KEY':''}):
            code=main(['--out',str(out),'--runs',str(self.path/'runs'),'--state',str(self.path/'state.sqlite')])
        self.assertEqual(code,1)
        self.assertEqual((out/'index.html').read_text(),'previous')


if __name__ == '__main__':
    unittest.main()
