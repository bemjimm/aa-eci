from __future__ import annotations

import csv
import io
import json
import math
import time
import zipfile
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .identity import clean, dates_compatible, iso_date, parse_name, taxonomy

AA_URL = 'https://artificialanalysis.ai/api/v2/language/models/free'
EPOCH_SCORES_URL = 'https://epoch.ai/data/eci_scores.csv'
EPOCH_METADATA_URL = 'https://epoch.ai/data/benchmark_data.zip'


class SourceError(RuntimeError):
    pass


def download(url: str, headers: dict | None = None, attempts: int = 3) -> bytes:
    if not url.startswith('https://'):
        raise SourceError('只接受 HTTPS 数据源。')
    last_error = None
    for attempt in range(attempts):
        try:
            request = Request(url, headers={'User-Agent': 'AA-ECI-Aggregator/1.0', **(headers or {})})
            with urlopen(request, timeout=45) as response:
                payload = response.read(64 * 1024 * 1024 + 1)
                if len(payload) > 64 * 1024 * 1024:
                    raise SourceError('数据源超过 64 MB 单次限制。')
                return payload
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise SourceError(f'数据源 HTTP {exc.code}；检查 API Key 和接口权限。') from None
            if exc.code not in {429, 500, 502, 503, 504}:
                raise SourceError(f'数据源 HTTP {exc.code}：{url}') from None
            retry = exc.headers.get('Retry-After', '0')
            if retry.isdigit() and int(retry) > 30:
                raise SourceError('数据源额度尚未恢复，本轮不发布。') from None
            last_error = exc
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc
        if attempt + 1 < attempts:
            time.sleep(2 ** attempt)
    raise SourceError(f'下载失败：{url} ({type(last_error).__name__})') from None


def json_bytes(payload: bytes) -> Any:
    try:
        return json.loads(payload.decode('utf-8-sig'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceError('数据源未返回有效 JSON。') from exc


def collect_aa(api_key: str, out: Path, get: Callable = download, url: str = AA_URL) -> dict:
    if not api_key:
        raise SourceError('请先设置环境变量 AA_API_KEY。')
    out.mkdir(parents=True, exist_ok=True)
    pages, all_rows, seen, versions = [], [], set(), set()
    announced_pages = None
    expected_items = None
    page = 1
    while True:
        payload = get(url + '?' + urlencode({'page': page}), {'x-api-key': api_key})
        body = json_bytes(payload)
        if not isinstance(body, dict) or not isinstance(body.get('data'), list):
            raise SourceError('AA 响应缺少 data 数组。')
        pagination = body.get('pagination')
        if not isinstance(pagination, dict):
            raise SourceError('AA 响应缺少 pagination，不能确认已取完。')
        if pagination.get('page') != page:
            raise SourceError('AA 返回了重复或错误页码。')
        total = pagination.get('total_pages')
        if not isinstance(total, int) or total < page:
            raise SourceError('AA total_pages 无效。')
        if announced_pages is not None and total != announced_pages:
            raise SourceError('AA 在抓取期间发生分页变化；重新运行本轮。')
        announced_pages = total
        if page == 1:
            expected_items = pagination.get('total_items', pagination.get('total_count'))
        more = pagination.get('has_more')
        if not isinstance(more, bool) or more != (page < total):
            raise SourceError('AA 分页结束标志不一致。')
        if not body['data']:
            raise SourceError('AA 返回空页；本轮不发布。')
        version = body.get('intelligence_index_version')
        if version is None:
            raise SourceError('AA 响应缺少 Intelligence Index 版本。')
        versions.add(str(version))
        if len(versions) != 1:
            raise SourceError('AA 在翻页期间切换了评分版本；重新运行本轮。')
        for row in body['data']:
            if not isinstance(row, dict) or not clean(row.get('id')):
                raise SourceError('AA 记录缺少 id。')
            record_id = str(row['id'])
            if record_id in seen:
                raise SourceError('AA 跨页重复 id；重新运行，避免漏行。')
            seen.add(record_id)
            all_rows.append(row)
        (out / f'aa-page-{page:04d}.json').write_bytes(payload)
        pages.append(page)
        if not more:
            break
        page += 1
        if page > 10000:
            raise SourceError('AA 分页未正常结束。')
    if expected_items is not None and len(all_rows) != expected_items:
        raise SourceError('AA 返回总数与取回记录数不同。')
    result = {'data': all_rows, 'intelligence_index_version': versions.pop(),
              '_collection': {'pages': len(pages), 'rows': len(all_rows), 'complete': True, 'url': url}}
    (out / 'aa-all.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    return result


def number(value: Any, label: str, nonnegative: bool = False) -> float | None:
    if value is None or clean(value).lower() in {'', 'nan', 'nat', 'null', 'none', 'na', 'n/a', '—'}:
        return None
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise SourceError(f'{label} 不是数值：{value!r}') from None
    if not math.isfinite(result) or (nonnegative and result < 0):
        raise SourceError(f'{label} 超出范围：{value!r}')
    return result


def normalize_aa(body: dict, aliases: dict) -> list[dict]:
    if not isinstance(body, dict) or not isinstance(body.get('data'), list) or not body['data']:
        raise SourceError('AA 本地文件需包含非空 data 数组。')
    pagination = body.get('pagination')
    if pagination and (pagination.get('has_more') or pagination.get('total_pages', 1) > 1):
        raise SourceError('本地 AA 文件只包含一页；请使用采集生成的 aa-all.json。')
    version = body.get('intelligence_index_version')
    if version is None:
        raise SourceError('AA 文件缺少 intelligence_index_version。')
    rows, seen = [], set()
    for item in body['data']:
        sid, raw_name = clean(item.get('id')), clean(item.get('name'))
        if not sid or not raw_name or sid in seen:
            raise SourceError('AA id/name 缺失或 id 重复。')
        seen.add(sid)
        released = iso_date(item.get('release_date'))
        base, effort, released = parse_name(raw_name, released)
        owner = item.get('model_creator') or {}
        owner = owner.get('name', '') if isinstance(owner, dict) else owner
        creator, series, base = taxonomy(base, owner, aliases)
        evaluations = item.get('evaluations')
        if 'artificial_analysis_intelligence_index_cost' not in item:
            raise SourceError('AA 任务成本字段缺失。')
        cost = item.get('artificial_analysis_intelligence_index_cost')
        if not isinstance(evaluations, dict):
            raise SourceError('AA 缺少 evaluations 对象。')
        if 'artificial_analysis_intelligence_index' not in evaluations:
            raise SourceError('AA 指数字段缺失。')
        if cost is not None and not isinstance(cost, dict):
            raise SourceError('AA cost 对象格式改变。')
        per_task = (cost or {}).get('cost_per_task') or {}
        slug = clean(item.get('slug'))
        rows.append({
            'id': 'aa:' + sid, 'source_key': 'aa:' + sid, 'source_id': sid, 'slug': slug,
            'raw_name': raw_name, 'name': base, 'creator': creator, 'series': series,
            'date': released, 'effort': effort, 'aa_version': str(version),
            'aa': number(evaluations.get('artificial_analysis_intelligence_index'), 'AA'),
            'cost_usd': number(per_task.get('total_cost'), 'AA 单任务成本', True),
            'source_url': 'https://artificialanalysis.ai/models/' + slug,
        })
    return rows


def csv_rows(payload: bytes) -> list[dict]:
    text = payload.decode('utf-8-sig')
    if text.lstrip().startswith(('<', '{')):
        raise SourceError('Epoch 未返回 CSV。')
    # Epoch 会在 CSV 中插入整行为空（",,,,,,"）的填充行；这些行没有数据，跳过。
    # DictReader 会把多余字段塞进键为 None 的列表，判断空行时只看字符串字段。
    rows = [row for row in csv.DictReader(io.StringIO(text))
            if any(clean(value) for value in row.values() if isinstance(value, str))]
    if not rows:
        raise SourceError('Epoch CSV 没有记录。')
    return rows


def field(row: dict, names: tuple[str, ...], required: bool = False) -> Any:
    for name in names:
        if name in row:
            return row[name]
    if required:
        raise SourceError(f'Epoch CSV 缺少列 {names}；现有列：{list(row)}')
    return None


def metadata_from_zip(payload: bytes) -> list[dict]:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            matches = [n for n in zf.namelist() if Path(n).name == 'model_metadata.csv' and 'additional_eci_data' not in Path(n).parts]
            if len(matches) != 1:
                raise SourceError('Epoch 数据包需有一份 model_metadata.csv。')
            info = zf.getinfo(matches[0])
            if info.file_size > 32 * 1024 * 1024:
                raise SourceError('Epoch 元数据文件过大。')
            rows = csv_rows(zf.read(matches[0]))
    except zipfile.BadZipFile as exc:
        raise SourceError('Epoch 元数据包不是有效 ZIP。') from exc
    expected = {'model_version', 'model_group', 'date'}
    if not expected.issubset(rows[0]):
        raise SourceError('Epoch model_metadata.csv 字段不完整。')
    return rows


def normalize_epoch(scores: list[dict], metadata: list[dict], aliases: dict, tolerance: int = 3) -> list[dict]:
    groups: dict[tuple[str, str], dict] = {}

    def make(group: str, released: str, owner: str = '', display: str = '') -> dict:
        # 上游元数据会把同一模型写成同名但日期相差一两天的多行（如 Qwen3-14B 的
        # 04-28/04-29）；日期在容差内的同名行并入同一组，超出容差的同名组保持分开。
        existing = next((k for k in groups if k[0] == group
                         and (k[1] == released or dates_compatible(k[1], released, tolerance))), None)
        if existing is None:
            base, _, resolved_date = parse_name(group, released)
            creator, series, base = taxonomy(base, owner, aliases)
            groups[(group, released)] = {
                'source_key': 'epoch:' + group + ('@' + resolved_date if resolved_date else ''),
                'group': group, 'name': base, 'creator': creator, 'series': series,
                'date': resolved_date, 'eci': None, 'versions': [], 'score_rows': [],
                'source_url': 'https://epoch.ai/eci?view=graph&tab=leaderboard',
            }
            existing = (group, released)
        elif owner and groups[existing]['creator'] == '其他':
            groups[existing]['creator'], groups[existing]['series'], _ = taxonomy(groups[existing]['name'], owner, aliases)
        return groups[existing]

    for index, item in enumerate(metadata):
        group = clean(item.get('model_group')) or clean(item.get('model_version'))
        if not group:
            raise SourceError('Epoch 元数据记录没有模型身份。')
        released = iso_date(item.get('date'))
        parent = make(group, released, clean(item.get('organization')), clean(item.get('display_name')))
        parent['versions'].append({'id': f'epoch-meta:{index}',
                                   'name': clean(item.get('model_version'))})

    for index, item in enumerate(scores):
        group = clean(field(item, ('model_group', 'model', 'Model', 'model_name', 'Model name', 'name'), True))
        if not group:
            raise SourceError('Epoch 成绩记录没有模型名称。')
        score = number(field(item, ('eci', 'ECI', 'eci_score', 'ECI score', 'ECI Score'), True), 'ECI')
        released = iso_date(field(item, ('date', 'release_date', 'Release date', 'Date')))
        owner = clean(field(item, ('organization', 'Organization', 'creator', 'developer', 'Developer')))
        candidates = [v for (g, d), v in groups.items()
                      if g == group and (not released or not d or dates_compatible(d, released, tolerance))]
        if len(candidates) > 1:
            raise SourceError(f'Epoch 组 {group!r} 需要发布日期来区分。')
        parent = candidates[0] if candidates else make(group, released, owner)
        if parent['eci'] is not None and score is not None and parent['eci'] != score:
            raise SourceError(f'Epoch 同一组出现两个不同 ECI：{group}')
        if score is not None:
            parent['eci'] = score
        parent['score_rows'].append(f'epoch-score:{index}')
    if not any(g['eci'] is not None for g in groups.values()):
        raise SourceError('Epoch 没有读到任何 ECI。')
    return sorted(groups.values(), key=lambda r: r['source_key'])
