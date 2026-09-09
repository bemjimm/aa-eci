from __future__ import annotations

import difflib
import json
import sqlite3
import uuid
from collections import Counter
from pathlib import Path

from .identity import dates_compatible, normalize_name
from .sources import SourceError


class Registry:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS entities (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, creator TEXT NOT NULL,
            series TEXT NOT NULL, release_date TEXT NOT NULL, signature TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS source_links (
            source_key TEXT PRIMARY KEY, entity_id TEXT NOT NULL REFERENCES entities(id)
        );
        CREATE TABLE IF NOT EXISTS snapshots (
            run_id TEXT PRIMARY KEY, aa_version TEXT NOT NULL, payload TEXT NOT NULL
        );
        ''')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('BEGIN IMMEDIATE')

    def get(self, entity_id: str) -> dict | None:
        row = self.db.execute('SELECT * FROM entities WHERE id=?', (entity_id,)).fetchone()
        return dict(row) if row else None

    def linked(self, source_key: str) -> dict | None:
        row = self.db.execute('SELECT entity_id FROM source_links WHERE source_key=?', (source_key,)).fetchone()
        return self.get(row['entity_id']) if row else None

    def compatible(self, record: dict, entity: dict, tolerance: int) -> bool:
        return (record['creator'] == entity['creator'] and
                dates_compatible(record['date'], entity['release_date'], tolerance))

    def candidates(self, record: dict, tolerance: int) -> list[dict]:
        rows = self.db.execute('SELECT * FROM entities WHERE creator=? AND signature=?',
                               (record['creator'], normalize_name(record['name']))).fetchall()
        result = [dict(r) for r in rows if self.compatible(record, dict(r), tolerance)]
        exact = [r for r in result if record['date'] and r['release_date'] == record['date']]
        return exact or result

    def bind(self, record: dict, forced_id: str | None = None, tolerance: int = 3,
             isolate: bool = False) -> str:
        entity = self.get(forced_id) if forced_id else self.linked(record['source_key'])
        if forced_id and not entity:
            raise SourceError('强制匹配指向不存在的模型族。')
        if entity and not forced_id and not self.compatible(record, entity, tolerance):
            entity = None
        if entity is None and not isolate:
            candidates = self.candidates(record, tolerance)
            if len(candidates) == 1:
                entity = candidates[0]
        if entity is None:
            seed = record['source_key'] + '|' + record['name'] + '|' + record['date']
            eid = str(uuid.uuid5(uuid.NAMESPACE_URL, 'aa-eci:' + seed))
            self.db.execute('INSERT OR IGNORE INTO entities VALUES (?,?,?,?,?,?)',
                            (eid, record['name'], record['creator'], record['series'],
                             record['date'], normalize_name(record['name'])))
            entity = self.get(eid)
        self.db.execute('INSERT INTO source_links VALUES (?,?) ON CONFLICT(source_key) DO UPDATE SET entity_id=excluded.entity_id',
                        (record['source_key'], entity['id']))
        if not entity['release_date'] and record['date']:
            self.db.execute('UPDATE entities SET release_date=? WHERE id=?', (record['date'], entity['id']))
        return entity['id']

    def snapshot(self, run_id: str, snapshot: dict) -> None:
        self.db.execute('INSERT INTO snapshots VALUES (?,?,?)',
                        (run_id, snapshot['meta']['aa_version'], json.dumps(snapshot, ensure_ascii=False)))

    def close(self, success: bool = True) -> None:
        if success:
            self.db.commit()
        else:
            self.db.rollback()
        self.db.close()


def assemble(aa: list[dict], epoch: list[dict], registry: Registry, aliases: dict,
             config: dict, run_id: str, source_info: dict) -> tuple[dict, dict]:
    families: dict[str, dict] = {}
    epoch_ids: dict[str, list[str]] = {}
    source_links = {}
    tolerance = int(config.get('matching', {}).get('date_tolerance_days', 3))

    def family(eid: str, record: dict) -> dict:
        if eid not in families:
            families[eid] = {
                'id': eid, 'name': record['name'], 'creator': record['creator'],
                'series': record['series'], 'date': record['date'], 'eci': None,
                'epoch_groups': [], 'epoch_versions': [], 'epoch_score_rows': [],
                'configurations': [], 'aa_version': '',
            }
        return families[eid]

    for record in sorted(epoch, key=lambda r: (not bool(r['date']), r['source_key'])):
        eid = registry.bind(record, tolerance=0)
        parent = family(eid, record)
        if parent['eci'] is not None and record['eci'] is not None and parent['eci'] != record['eci']:
            raise SourceError('两个不同 Epoch ECI 被归入同一族；请在映射中拆分。')
        if record['eci'] is not None:
            parent['eci'] = record['eci']
        parent['epoch_groups'].append(record['source_key'])
        parent['epoch_versions'].extend(record['versions'])
        parent['epoch_score_rows'].extend(record['score_rows'])
        source_links[record['source_key']] = eid
        epoch_ids.setdefault(record['group'], []).append(eid)

    forced_links = aliases.get('aa_to_epoch', {})
    isolates = set(aliases.get('separate_aa_slugs', []))
    for record in sorted(aa, key=lambda r: (not bool(r['date']), r['date'], r['source_key'])):
        forced = forced_links.get(record['slug']) or forced_links.get(record['source_id'])
        forced_id = None
        if forced:
            targets = list(dict.fromkeys(epoch_ids.get(forced, [])))
            if len(targets) != 1:
                raise SourceError(f'映射目标 {forced!r} 不存在或不唯一。')
            forced_id = targets[0]
        isolate = record['slug'] in isolates
        if isolate:
            independent = dict(record)
            independent['source_key'] += ':separate'
            eid = registry.bind(independent, tolerance=tolerance, isolate=True)
        else:
            eid = registry.bind(record, forced_id, tolerance)
        parent = family(eid, record)
        child = {k: record[k] for k in ('id', 'raw_name', 'effort', 'aa', 'cost_usd', 'source_url', 'aa_version', 'date', 'slug')}
        parent['configurations'].append(child)
        parent['aa_version'] = record['aa_version']
        source_links[record['source_key']] = eid

    expected_aa = {r['id'] for r in aa}
    actual_aa = [c['id'] for f in families.values() for c in f['configurations']]
    expected_epoch = {r['source_key'] for r in epoch}
    actual_epoch = [g for f in families.values() for g in f['epoch_groups']]
    expected_versions = [v['id'] for e in epoch for v in e['versions']]
    actual_versions = [v['id'] for f in families.values() for v in f['epoch_versions']]
    expected_scores = [v for e in epoch for v in e['score_rows']]
    actual_scores = [v for f in families.values() for v in f['epoch_score_rows']]
    if set(actual_aa) != expected_aa or len(actual_aa) != len(aa):
        raise SourceError('AA 配置覆盖校验失败。')
    if set(actual_epoch) != expected_epoch or len(actual_epoch) != len(epoch):
        raise SourceError('Epoch 组覆盖校验失败。')
    if Counter(expected_versions) != Counter(actual_versions) or Counter(expected_scores) != Counter(actual_scores):
        raise SourceError('Epoch 原始记录覆盖校验失败。')
    if len(source_links) != len(aa) + len(epoch):
        raise SourceError('来源身份冲突。')

    result = sorted(families.values(), key=lambda f: (f['creator'], f['series'], f['name'], f['date']))
    for f in result:
        f['configurations'].sort(key=lambda c: (c['aa'] is None, -(c['aa'] or 0), c['cost_usd'] is None,
                                                c['cost_usd'] if c['cost_usd'] is not None else float('inf'), c['id']))
    stats = {
        'aa_input_rows': len(aa), 'aa_output_configurations': len(actual_aa),
        'aa_cost_covered_configs': sum(1 for r in aa if r.get('cost_usd') is not None),
        'epoch_input_groups': len(epoch), 'epoch_output_groups': len(actual_epoch),
        'epoch_score_rows': len(actual_scores), 'epoch_metadata_versions': len(actual_versions),
        'families': len(result),
        'both_sources': sum(bool(f['configurations'] and f['epoch_groups']) for f in result),
        'aa_only': sum(bool(f['configurations'] and not f['epoch_groups']) for f in result),
        'epoch_only': sum(bool(f['epoch_groups'] and not f['configurations']) for f in result),
        'source_rows_lost': 0,
    }
    versions = set(r['aa_version'] for r in aa)
    if len(versions) != 1:
        raise SourceError('AA 评分版本混杂。')
    snapshot = {
        'meta': {'title': '大模型选型表', 'snapshot_id': run_id, 'aa_version': versions.pop(),
                 'usd_cny': config.get('usd_cny', 7),
                 'stats': stats, 'sources': source_info},
        'families': result,
    }
    candidates = []
    for f in result:
        if not f['configurations'] or f['epoch_groups']:
            continue
        options = []
        for e in result:
            if e['epoch_groups'] and e['creator'] == f['creator']:
                ratio = difflib.SequenceMatcher(None, normalize_name(f['name']), normalize_name(e['name'])).ratio()
                if ratio >= .72:
                    options.append({'family_id': e['id'], 'name': e['name'], 'date': e['date'], 'similarity': round(ratio, 3)})
        if options:
            candidates.append({'family_id': f['id'], 'name': f['name'], 'date': f['date'],
                               'candidates': sorted(options, key=lambda v: -v['similarity'])[:3]})
    audit = {'coverage': stats, 'source_links': source_links, 'match_candidates': candidates}
    return snapshot, audit
