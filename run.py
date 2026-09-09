#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from pipeline.assemble import Registry, assemble
from pipeline.render import build_files
from pipeline.sources import (SourceError, collect_aa, csv_rows, download, json_bytes,
                              metadata_from_zip, normalize_aa, normalize_epoch)
from dotenv import load_dotenv
load_dotenv()
ROOT = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='全量聚合 AA × ECI 并生成离线交互表格。')
    parser.add_argument('--config', type=Path, default=ROOT/'config.json')
    parser.add_argument('--aliases', type=Path, default=ROOT/'aliases.json')
    parser.add_argument('--state', type=Path, default=ROOT/'state.sqlite')
    parser.add_argument('--runs', type=Path, default=ROOT/'runs')
    parser.add_argument('--out', type=Path, default=ROOT/'dist')
    parser.add_argument('--aa-json', type=Path,
                        help='本地完整 aa-all.json；省略时使用 API')
    parser.add_argument('--epoch-csv', type=Path, help='本地完整 Epoch 成绩 CSV')
    parser.add_argument('--epoch-metadata', type=Path,
                        help='本地 benchmark_data.zip 或 model_metadata.csv')
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    run_id = now.strftime('%Y%m%dT%H%M%S.%fZ')
    work = args.runs / run_id
    raw = work / 'raw'
    staged = work / 'site'
    registry = None
    try:
        config = json.loads(args.config.read_text(encoding='utf-8'))
        aliases = json.loads(args.aliases.read_text(encoding='utf-8'))
        fx = config.get('usd_cny', 7)
        if not isinstance(fx, (float, int)) or not 0 < fx < 1000000:
            raise SourceError('config.usd_cny 必须为正数。')
        if not args.aa_json and not os.environ.get('AA_API_KEY'):
            raise SourceError('请先设置 AA_API_KEY，或传入 --aa-json 完整数据文件。')
        raw.mkdir(parents=True, exist_ok=True)
        urls = config['sources']
        if args.aa_json:
            aa_bytes = args.aa_json.read_bytes()
            aa_body = json_bytes(aa_bytes)
            (raw/'aa-all.json').write_bytes(aa_bytes)
        else:
            aa_body = collect_aa(os.environ['AA_API_KEY'], raw, url=urls['aa'])
        if args.epoch_csv:
            epoch_bytes = args.epoch_csv.read_bytes()
        else:
            epoch_bytes = download(urls['epoch_scores'])
        (raw/'eci_scores.csv').write_bytes(epoch_bytes)
        if args.epoch_metadata:
            meta_bytes = args.epoch_metadata.read_bytes()
            zipped = args.epoch_metadata.suffix.lower() == '.zip'
        else:
            meta_bytes = download(urls['epoch_metadata'])
            zipped = True
        (raw/('benchmark_data.zip' if zipped else 'model_metadata.csv')
         ).write_bytes(meta_bytes)
        meta_rows = metadata_from_zip(
            meta_bytes) if zipped else csv_rows(meta_bytes)
        scores = csv_rows(epoch_bytes)
        aa = normalize_aa(aa_body, aliases)
        epoch = normalize_epoch(scores, meta_rows, aliases,
                                tolerance=int(config.get('matching', {}).get('date_tolerance_days', 3)))
        registry = Registry(args.state)
        sources = {
            'aa': {'url': urls['aa'], 'rows': len(aa), 'pages': aa_body.get('_collection', {}).get('pages'),
                   'retrieved_at': now.isoformat(), 'input': 'file' if args.aa_json else 'live'},
            'epoch': {'url': urls['epoch_scores'], 'score_rows': len(scores), 'metadata_rows': len(meta_rows),
                      'metadata_url': urls['epoch_metadata'], 'retrieved_at': now.isoformat(),
                      'input': 'file' if args.epoch_csv else 'live'},
        }
        snapshot, audit = assemble(
            aa, epoch, registry, aliases, config, run_id, sources)
        snapshot['meta']['generated_at'] = now.isoformat()
        audit['raw_sha256'] = {p.name: hashlib.sha256(
            p.read_bytes()).hexdigest() for p in raw.iterdir() if p.is_file()}
        (work/'audit.json').write_text(json.dumps(audit,
                                                  ensure_ascii=False, indent=2), encoding='utf-8')
        build_files(snapshot, staged, ROOT/'web')
        registry.snapshot(run_id, snapshot)
        registry.close(True)
        registry = None
        args.out.mkdir(parents=True, exist_ok=True)
        # index.html is self-contained; publish it last as a single atomic replacement.
        for name in ('models.json', 'models.csv', '.nojekyll', 'index.html'):
            temp = args.out/(name + '.new')
            shutil.copyfile(staged/name, temp)
            os.replace(temp, args.out/name)
        stats = snapshot['meta']['stats']
        print(json.dumps({'html': str(args.out/'index.html'),
              'snapshot': run_id, **stats}, ensure_ascii=False, indent=2))
        return 0
    except (SourceError, OSError, ValueError, KeyError, TypeError) as exc:
        if registry:
            registry.close(False)
        if work.exists():
            (work/'failure.json').write_text(json.dumps({'error': str(
                exc), 'type': type(exc).__name__}, ensure_ascii=False), encoding='utf-8')
        print(f'本轮未发布：{exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
