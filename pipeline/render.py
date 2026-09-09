from __future__ import annotations

import csv
import json
import re
from pathlib import Path


def safe_cell(value):
    if isinstance(value, str) and value.startswith(('=', '+', '-', '@', '\t', '\r')):
        return "'" + value
    return value


def build_files(snapshot: dict, destination: Path, web_dir: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    # epoch 内部版本清单只进数据库与审计，不进发布负载。
    slim = dict(snapshot, families=[
        {k: v for k, v in f.items() if k != 'epoch_versions'} for f in snapshot['families']])
    data = json.dumps(slim, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    embedded = data.replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
    values = {'DATA': embedded, 'STYLE': (web_dir/'table.css').read_text(encoding='utf-8'),
              'SCRIPT': (web_dir/'table.js').read_text(encoding='utf-8')}
    template = (web_dir/'table.html').read_text(encoding='utf-8')
    html = re.sub(r'__(DATA|STYLE|SCRIPT)__', lambda m: values[m.group(1)], template)
    (destination/'index.html').write_text(html, encoding='utf-8')
    (destination/'models.json').write_text(data, encoding='utf-8')
    fx = snapshot['meta']['usd_cny']
    headers = ['model', 'creator', 'series', 'release_date', 'configuration', 'AA', 'AA_version',
               'ECI_family', 'USD_per_AA_task', 'CNY_per_USD', 'CNY_per_AA_task',
               'AA_source', 'family_id', 'configuration_id']
    with (destination/'models.csv').open('w', newline='', encoding='utf-8-sig') as file:
        writer = csv.writer(file)
        writer.writerow(headers)
        for family in snapshot['families']:
            for config in family['configurations'] or [{}]:
                cost = config.get('cost_usd')
                rmb = cost * fx if cost is not None else None
                writer.writerow([safe_cell(v) for v in [
                    family['name'], family['creator'], family['series'], family['date'],
                    config.get('effort'), config.get('aa'), config.get('aa_version'), family['eci'],
                    cost, fx, rmb,
                    config.get('source_url'), family['id'], config.get('id'),
                ]])
    (destination/'.nojekyll').touch()
