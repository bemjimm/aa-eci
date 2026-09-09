from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any

CREATORS = {
    'openai': 'OpenAI', 'anthropic': 'Anthropic',
    'google': 'Google', 'googledeepmind': 'Google', 'deepmind': 'Google',
    'meta': 'Meta', 'metaai': 'Meta', 'metallama': 'Meta',
    'zai': 'Z.ai', 'zaizhipuai': 'Z.ai', 'zhipuai': 'Z.ai', 'zhipu': 'Z.ai',
    'moonshot': 'Moonshot', 'moonshotai': 'Moonshot',
    'alibaba': 'Alibaba', 'alibabacloud': 'Alibaba', 'qwen': 'Alibaba',
    'xai': 'xAI', 'deepseek': 'DeepSeek', 'mistral': 'Mistral AI',
    'mistralai': 'Mistral AI', 'nvidia': 'NVIDIA', 'minimax': 'MiniMax',
}
PREFIXES = [
    (r'^(gpt|chatgpt|o[134](?:\b|-))', 'OpenAI', 'GPT'),
    (r'^claude', 'Anthropic', 'Claude'),
    (r'^(gemini|gemma|palm)', 'Google', None),
    (r'^(muse|llama)', 'Meta', None),
    (r'^qwen', 'Alibaba', 'Qwen'),
    (r'^deepseek', 'DeepSeek', 'DeepSeek'),
    (r'^(kimi|moonshot)', 'Moonshot', 'Kimi'),
    (r'^(glm|chatglm)', 'Z.ai', 'GLM'),
    (r'^grok', 'xAI', 'Grok'),
    (r'^minimax', 'MiniMax', 'MiniMax'),
    (r'^(mistral|mixtral|ministral|codestral|magistral)', 'Mistral AI', None),
]
EFFORT = re.compile(
    r'^(?:none|non[- ]reasoning|non[- ]thinking|no thinking|'
    r'low|medium|high|xhigh|max|minimal|think(?:ing)?|reasoning|'
    r'(?:\d+(?:\.\d+)?)[km]?\s*(?:tokens?|budget))(?=$|\b|[,;·/])', re.I
)


def clean(value: Any) -> str:
    return unicodedata.normalize('NFKC', str('' if value is None else value)).strip()


def key(value: str) -> str:
    return ''.join(re.findall(r'[a-z0-9]+', clean(value).lower()))


def normalize_name(value: str) -> str:
    # Keep version numbers and model-size tokens; do not strip mini/pro/flash/distill.
    return ' '.join(re.findall(r'[a-z]+|\d+(?:\.\d+)*', clean(value).lower()))


def iso_date(value: Any) -> str:
    text = clean(value)
    if not text or text.lower() in {'nan', 'nat', 'none', 'null'}:
        return ''
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return ''


def parse_name(name: str, release_date: str = '') -> tuple[str, str, str]:
    name = clean(name)
    effort = ''
    match = re.search(r'\s*\(([^()]*)\)\s*$', name)
    if match:
        # 括号里只要有一段是明确的 effort 档位（max、High Effort、Non-reasoning…），
        # 整个括号就是这个配置的档位标签，从族名移出；模式词（Adaptive Reasoning）
        # 与回退说明留在标签内以便区分同档配置。不含档位词的括号（日期、代号）留在族名。
        parts = [p.strip() for p in match.group(1).split(',') if p.strip()]
        if parts and any(EFFORT.match(p) for p in parts):
            effort = ', '.join(parts)
            name = name[:match.start()].strip()
    match = re.search(r'(?:\s*\(|\s+|[-_])((?:20)\d{2}-\d{2}-\d{2})\)?$', name)
    if match and iso_date(match.group(1)):
        release_date = match.group(1)
        name = name[:match.start()].strip(' -_(')
    return name or clean(name), effort or '默认', release_date


def taxonomy(name: str, creator: str, aliases: dict) -> tuple[str, str, str]:
    creator = clean(creator)
    creator = aliases.get('creators', {}).get(creator, creator)
    creator = CREATORS.get(key(creator), creator)
    series = ''
    for pattern, owner, line in PREFIXES:
        if re.search(pattern, name, re.I):
            if not creator:
                creator = owner
            if line:
                series = line
            else:
                match = re.match(r'[A-Za-z]+', name)
                series = match.group(0).title() if match else name
            break
    creator = creator or '其他'
    series = aliases.get('series', {}).get(f'{creator}|{name}', series)
    if not series:
        match = re.match(r'[A-Za-z]+', name)
        series = match.group(0) if match else name.split()[0]
    if re.match(r'^o[134](?:\b|-)', name, re.I) and creator == 'OpenAI':
        series = 'o 系列'
    name = aliases.get('names', {}).get(f'{creator}|{name}', name)
    return creator, series, name


def dates_compatible(a: str, b: str, tolerance: int = 3) -> bool:
    if not a or not b:
        return True
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days) <= tolerance
