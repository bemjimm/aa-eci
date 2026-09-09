'use strict';
(() => {
  const data = JSON.parse(document.getElementById('dataset').textContent);
  const $ = id => document.getElementById(id);
  const present = x => typeof x === 'number' && Number.isFinite(x);
  const FX = data.meta.usd_cny;             // 汇率在构建时固定，页面不提供输入
  const DIVERGE = 0.15;                      // 两榜相对位置差超过该比例视为分歧并高亮
  const I18N = {
    zh: {
      docTitle: '大模型选型表 · AA × ECI', h1: '大模型选型表', htmlLang: 'zh-CN', locale: 'zh-CN', langBtn: 'EN',
      copylink: '复制链接', copied: '已复制', export: '导出 CSV', exported: n => `已导出 ${n} 条`,
      searchPh: '搜索模型、厂商、系列、日期…',
      aaPre: 'AA 前', aaPost: '名', eciPre: 'ECI 前', eciPost: '名', costPre: '单任务 ≤ ¥', costPost: '',
      reset: '重置', empty: '没有符合条件的模型', clear: '清除筛选', phAny: '不限', srSearch: '搜索模型',
      pageSize: ['每页 50', '每页 100', '每页 200', '全部'],
      h_name: '模型 / 配置', h_cost: '单任务（¥）', h_date: '发布',
      tt_aa: 'Artificial Analysis 智能指数（0–100）。档位行的 #N 为该配置在全部配置中的名次',
      tt_eci: 'Epoch 能力指数，属于整个模型族而非单个配置；与 AA 量纲不同。# 后为该族在全部有 ECI 成绩中的名次',
      tt_cost: 'AA 实测单任务成本，按构建时汇率折算为人民币；仅覆盖公布了成本的配置',
      updated: s => `更新于 ${s}`, expand: '展开', collapse: '收起', tiers: (n, name) => `（${n} 档）`,
      diverge: p => `两榜相对位置差 ${p}%`,
      counts: (c, total, cfg, filtered) => filtered
        ? `筛选出 ${c} / ${total} 个模型 · ${cfg} 个可展开档位`
        : `${c} 个模型 · ${cfg} 个可展开档位`,
    },
    en: {
      docTitle: 'LLM Selection Table · AA × ECI', h1: 'LLM Selection Table', htmlLang: 'en', locale: 'en-US', langBtn: '中文',
      copylink: 'Copy link', copied: 'Copied', export: 'Export CSV', exported: n => `Exported ${n}`,
      searchPh: 'Search models, creators, series, dates…',
      aaPre: 'AA top', aaPost: '', eciPre: 'ECI top', eciPost: '', costPre: 'Per task ≤ $', costPost: '',
      reset: 'Reset', empty: 'No models match the filters', clear: 'Clear filters', phAny: 'any', srSearch: 'Search models',
      pageSize: ['50 per page', '100 per page', '200 per page', 'All'],
      h_name: 'Model / Tier', h_cost: 'Per task ($)', h_date: 'Released',
      tt_aa: 'Artificial Analysis Intelligence Index (0–100). On tier rows, #N is the tier\u2019s rank among all configurations',
      tt_eci: 'Epoch Capabilities Index \u2014 belongs to the whole model family, not to a single tier; on a different scale than AA. #N is the family\u2019s rank among all families with an ECI score',
      tt_cost: 'AA-measured cost per task in USD, as published; shown only for configurations with a published cost',
      updated: s => `Updated ${s}`, expand: 'Expand', collapse: 'Collapse', tiers: (n, name) => ` (${n} tiers)`,
      diverge: p => `position gap between the two leaderboards: ${p}%`,
      counts: (c, total, cfg, filtered) => filtered
        ? `${c} of ${total} models · ${cfg} expandable tiers`
        : `${c} models · ${cfg} expandable tiers`,
    },
  };
  let lang = 'zh';
  const t = key => I18N[lang][key] ?? I18N.zh[key];
  const state = {sort: 'aa', direction: -1, page: 1, expanded: new Set()};
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const link = u => { try { const x = new URL(u); return x.protocol === 'https:' ? esc(x.href) : ''; } catch { return ''; } };
  $('indexVersion').textContent = 'AA v' + data.meta.aa_version;
  let generated = null;
  if (data.meta.generated_at) {
    const d = new Date(data.meta.generated_at);
    if (!Number.isNaN(d.getTime())) generated = d;
  }
  function setSnapshot() {
    if (!generated) return;
    const parts = new Intl.DateTimeFormat(t('locale'), {timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(generated);
    const part = tp => (parts.find(x => x.type === tp) || {}).value;
    $('snapshot').textContent = t('updated')(`${part('month')}-${part('day')} ${part('hour')}:${part('minute')}`);
    if (Date.now() - generated.getTime() > 3*24*3600*1000) $('snapshot').classList.add('stale');
  }
  function applyLang() {
    document.documentElement.lang = t('htmlLang');
    document.title = t('docTitle');
    for (const el of document.querySelectorAll('[data-i18n]')) el.textContent = t(el.dataset.i18n);
    for (const el of document.querySelectorAll('[data-i18n-placeholder]')) el.placeholder = t(el.dataset.i18nPlaceholder);
    for (const el of document.querySelectorAll('[data-i18n-title]')) el.title = t(el.dataset.i18nTitle);
    $('lang').textContent = t('langBtn');
    const ps = I18N[lang].pageSize;
    [...$('pageSize').options].forEach((o, i) => o.textContent = ps[i] ?? o.textContent);
    setSnapshot();
  }
  const bestAA = f => {
    let m = null;
    for (const c of f.configurations) if (present(c.aa) && (m === null || c.aa > m)) m = c.aa;
    return m;
  };
  // 全局名次：前 N 筛选与分歧计算都基于全量数据，不受当前筛选影响。
  // AA 名次取族内最高分（族行固定显示最高分档，口径一致）。
  const rankMap = vals => {
    const m = new Map();
    [...new Set(vals.filter(present))].sort((a,b)=>b-a).forEach((v,i)=>m.set(v,i+1));
    return m;
  };
  const gAA = rankMap(data.families.map(bestAA));
  const gECI = rankMap(data.families.map(f => f.eci));
  const gCfgAA = rankMap(data.families.flatMap(f => f.configurations.map(c => c.aa)));
  const nAA = data.families.filter(f => present(bestAA(f))).length;
  const nECI = data.families.filter(f => present(f.eci)).length;
  const divergePct = new Map();
  for (const f of data.families) {
    const ra = gAA.get(bestAA(f)), re = gECI.get(f.eci);
    if (ra && re && nAA > 1 && nECI > 1)
      divergePct.set(f.id, Math.abs((ra-1)/(nAA-1) - (re-1)/(nECI-1)));
  }
  const persistKeys = ['search','aaTop','eciTop','costMax','pageSize'];
  function persist() {
    const s = {};
    for (const id of persistKeys) s[id] = $(id).value;
    s.sort = state.sort; s.direction = state.direction; s.lang = lang;
    const json = JSON.stringify(s);
    try { localStorage.setItem('aa-eci-table-v2', json); } catch {}
    try { history.replaceState(null, '', '#s=' + encodeURIComponent(json)); } catch {}
  }
  function restore() {
    let raw = null;
    if (location.hash.startsWith('#s=')) {
      try { raw = decodeURIComponent(location.hash.slice(3)); } catch { raw = null; }
    }
    if (raw === null) { try { raw = localStorage.getItem('aa-eci-table-v2'); } catch {} }
    if (!raw) return;
    let s;
    try { s = JSON.parse(raw); } catch { return; }
    const setSelect = (id, v) => { if (typeof v === 'string' && [...$(id).options].some(o => o.value === v)) $(id).value = v; };
    if (typeof s.search === 'string') $('search').value = s.search;
    for (const id of ['aaTop','eciTop','costMax']) if (typeof s[id] === 'string') $(id).value = s[id];
    setSelect('pageSize', s.pageSize);
    if (['name','aa','eci','cost','date'].includes(s.sort)) state.sort = s.sort;
    if (s.direction === 1 || s.direction === -1) state.direction = s.direction;
    if (s.lang === 'zh' || s.lang === 'en') lang = s.lang;
  }
  restore();
  if (!(lang === 'zh' || lang === 'en')) lang = (navigator.language || '').toLowerCase().startsWith('zh') ? 'zh' : 'en';
  applyLang();
  function filters() {
    const input = id => $(id).value === '' ? null : Number($(id).value);
    return {q:$('search').value.trim().toLocaleLowerCase(),
      aaTop:input('aaTop'),eciTop:input('eciTop'),cost:input('costMax'),
      size:Number($('pageSize').value)};
  }
  function validInputs() {
    return ['aaTop','eciTop','costMax'].every(id => $(id).checkValidity());
  }
  const numericCompare = (a, b, dir) => {
    if (!present(a)) return present(b) ? 1 : 0;
    if (!present(b)) return -1;
    return dir * (a-b);
  };
  const dateCompare = (a, b, dir) => {
    if (!a) return b ? 1 : 0;
    if (!b) return -1;
    return dir * a.localeCompare(b);
  };
  function representative(cs) {
    return [...cs].sort((a,b) =>
      numericCompare(a.aa, b.aa, -1) || numericCompare(a.cost_usd, b.cost_usd, 1) || a.id.localeCompare(b.id)
    )[0] || null;
  }
  // 价格跟随界面货币：中文为 ¥（构建时汇率折算），英文为 $（AA 原始值）。
  const costOf = c => present(c?.cost_usd) ? c.cost_usd * (lang === 'zh' ? FX : 1) : null;
  function value(row, sort) {
    switch(sort) {
      case 'name': return row.f.name;
      case 'aa': return row.c?.aa;
      case 'eci': return row.f.eci;
      case 'cost': return costOf(row.c);
      case 'date': return row.f.date;
    }
  }
  function makeCompare() {
    return (a,b) => {
      const x=value(a,state.sort), y=value(b,state.sort);
      let cmp = state.sort === 'name' ? state.direction*String(x).localeCompare(String(y),'zh-CN')
        : state.sort === 'date' ? dateCompare(x,y,state.direction)
        : numericCompare(x,y,state.direction);
      return cmp || a.f.name.localeCompare(b.f.name) || (a.c?.id || a.f.id).localeCompare(b.c?.id || b.f.id);
    };
  }
  function select(fx) {
    const families=[];
    for (const f of data.families) {
      if (fx.eciTop!==null) { const r=gECI.get(f.eci); if (!r || r>fx.eciTop) continue; }
      if (fx.aaTop!==null) { const r=gAA.get(bestAA(f)); if (!r || r>fx.aaTop) continue; }
      const familyMatches = `${f.name} ${f.creator} ${f.series} ${f.date}`.toLocaleLowerCase().includes(fx.q);
      const cs=f.configurations.filter(c => {
        if (fx.q && !familyMatches && !`${c.raw_name} ${c.effort} ${c.slug}`.toLocaleLowerCase().includes(fx.q)) return false;
        const price=costOf(c);
        if (fx.cost!==null && (price===null||price>fx.cost+1e-12)) return false;
        return true;
      });
      if (!cs.length && (f.configurations.length || fx.cost!==null || (fx.q && !familyMatches))) continue;
      families.push({f,cs,c:representative(cs)});
    }
    families.sort(makeCompare());
    return {families};
  }
  function number(x, kind='score') {
    if (!present(x)) return '<span class="null">—</span>';
    const digits=kind==='score'?1:(Math.abs(x)>0 && Math.abs(x)<.01?6:2);
    return new Intl.NumberFormat(t('locale'),{maximumFractionDigits:digits,minimumFractionDigits:0}).format(x);
  }
  function cellHTML(x, rank, kind='score', diverge=0) {
    if (!present(x)) return '<span class="null">—</span>';
    const v = `<span class="val${diverge>=DIVERGE?' diverge':''}"${diverge>=DIVERGE?` title="${t('diverge')(Math.round(diverge*100))}"`:''}>${number(x,kind)}</span>`;
    // 名次固定占位：数值右边缘不随 #N 位数变化，列内对齐。
    return v + (rank ? `<span class="rk">#${rank}</span>` : '<span class="rk"></span>');
  }
  function rowHTML(row, fx, child=false) {
    const f=row.f,c=row.c;
    const multi=!child&&row.cs.length>1;
    const expanded=state.expanded.has(f.id);
    // 档位数只出现在展开按钮上：它是「里面有东西可看」的提示，不单独占徽标。
    const toggle=!child?(multi
      ?`<button class="toggle" type="button" data-toggle="${esc(f.id)}" aria-label="${expanded?t('collapse'):t('expand')} ${esc(f.name)}${t('tiers')(row.cs.length, esc(f.name))}" aria-expanded="${expanded}"><i>${expanded?'▾':'▸'}</i></button>`
      :'<span class="toggle none" aria-hidden="true"></span>'):'';
    const url=link(c?.source_url);
    const name=child?(c.effort==='默认'?c.raw_name:c.effort):f.name;
    const nameHTML=url?`<a class="name" href="${url}" target="_blank" rel="noopener noreferrer">${esc(name)}</a>`:`<span class="name">${esc(name)}</span>`;
    const owner=!child?`<span class="owner">${esc(f.creator)}</span>`:'';
    const diverge = child ? 0 : (divergePct.get(f.id) || 0);
    const cost=costOf(c);
    const date=child?'':(f.date?`<span class="dt">${esc(f.date)}</span>`:'<span class="null">—</span>');
    const aaRank = child ? gCfgAA.get(c?.aa) : gAA.get(bestAA(f));
    return `<tr class="${child?'child':'family'}" data-family="${esc(f.id)}" ${c?`data-config="${esc(c.id)}"`:''}><td><div class="cellname">${toggle}${nameHTML}${owner}</div></td><td class="num">${cellHTML(c?.aa, aaRank)}</td><td class="num">${child?'<span class="null">—</span>':cellHTML(f.eci, gECI.get(f.eci) || null, 'score', diverge)}</td><td class="num">${cellHTML(cost, null, 'cost')}</td><td class="num dtcell">${date}</td></tr>`;
  }
  function render() {
    if (!validInputs()) return;
    const fx=filters(),selected=select(fx);
    const count=selected.families.length;
    const pages=fx.size?Math.max(1,Math.ceil(count/fx.size)):1;
    state.page=Math.max(1,Math.min(state.page,pages));
    const pageItems=fx.size?selected.families.slice((state.page-1)*fx.size,state.page*fx.size):selected.families;
    let html='';
    for(const row of pageItems) {
      html+=rowHTML(row,fx);
      if(state.expanded.has(row.f.id)) {
        const compare=makeCompare();
        html+=[...row.cs].map(c=>({f:row.f,c,cs:[]})).sort(compare).map(r=>rowHTML(r,fx,true)).join('');
      }
    }
    $('rows').innerHTML=html;
    $('empty').hidden=!!count;
    const configurations=selected.families.reduce((s,r)=>s+r.cs.length,0);
    const nf = new Intl.NumberFormat(t('locale'));
    $('counts').textContent=t('counts')(nf.format(count),nf.format(data.families.length),nf.format(configurations),count!==data.families.length);
    $('pageInfo').textContent=`${state.page} / ${pages}`;
    $('previous').disabled=state.page===1;
    $('next').disabled=state.page===pages;
    for(const th of document.querySelectorAll('th[data-key]')) {
      const active=th.dataset.key===state.sort;
      th.setAttribute('aria-sort',active?(state.direction===1?'ascending':'descending'):'none');
      th.querySelector('i').textContent=active?(state.direction===1?'↑':'↓'):'↕';
    }
    persist();
  }
  function clearFilters() {  // 只清筛选条件，不动排序与展开状态
    for (const id of ['search','aaTop','eciTop','costMax']) $(id).value='';
    state.page=1; render();
  }
  function reset() {
    clearFilters();
    $('aaTop').value='100';
    state.sort='aa';state.direction=-1;state.expanded.clear();render();
  }
  for(const id of ['search','aaTop','eciTop','costMax','pageSize']) {
    $(id).addEventListener(id==='search'||$(id).tagName==='INPUT'?'input':'change',()=>{
      state.page=1;render();
    });
  }
  document.querySelector('thead').addEventListener('click',e=>{
    const b=e.target.closest('[data-sort]');if(!b)return;
    if(state.sort===b.dataset.sort)state.direction*=-1;
    else{state.sort=b.dataset.sort;state.direction=['aa','eci','date'].includes(state.sort)?-1:1;}
    state.page=1;render();
  });
  $('rows').addEventListener('click',e=>{
    const b=e.target.closest('[data-toggle]');if(!b)return;
    if(state.expanded.has(b.dataset.toggle))state.expanded.delete(b.dataset.toggle);else state.expanded.add(b.dataset.toggle);render();
  });
  $('reset').addEventListener('click',reset);$('clearEmpty').addEventListener('click',clearFilters);
  $('lang').addEventListener('click',()=>{lang=lang==='zh'?'en':'zh';applyLang();persist();render();});
  document.addEventListener('keydown',e=>{
    const tag=(document.activeElement||{}).tagName;
    if(e.key==='/'&&tag!=='INPUT'&&tag!=='SELECT'&&tag!=='TEXTAREA'){e.preventDefault();$('search').focus();}
    else if(e.key==='Escape'&&document.activeElement===$('search')){$('search').value='';state.page=1;render();}
  });
  let copyTimer=0;
  $('copylink').addEventListener('click',()=>{
    const feedback=()=>{const b=$('copylink');b.textContent=t('copied');clearTimeout(copyTimer);copyTimer=setTimeout(()=>{b.textContent=t('copylink');},1500);};
    const fallback=()=>{
      const t=document.createElement('textarea');t.value=location.href;t.style.cssText='position:fixed;opacity:0';
      document.body.append(t);t.select();
      let ok=false;try{ok=document.execCommand('copy');}catch{}
      t.remove();if(ok)feedback();
    };
    if(navigator.clipboard&&navigator.clipboard.writeText)navigator.clipboard.writeText(location.href).then(feedback,fallback);
    else fallback();
  });
  $('previous').addEventListener('click',()=>{state.page--;render();$('rows').parentElement.parentElement.scrollTop=0;});
  $('next').addEventListener('click',()=>{state.page++;render();$('rows').parentElement.parentElement.scrollTop=0;});
  const csvValue=v=>{if(v==null)return '""';let s=String(v);if(typeof v==='string'&&/^[=+@\-\t\r]/.test(s))s="'"+s;return '"'+s.replace(/"/g,'""')+'"';};
  let exportTimer=0;
  $('export').addEventListener('click',()=>{
    if(!validInputs())return;
    const fx=filters();
    const all=select(fx).families.flatMap(r=>r.cs.length?r.cs.map(c=>({f:r.f,c})):[r]);
    const rows=[['model','creator','series','release_date','configuration','AA','AA_version','ECI_family','USD_per_AA_task','CNY_per_AA_task','AA_source','snapshot']];
    for(const {f,c} of all){const cost=present(c?.cost_usd)?c.cost_usd*FX:null;
      rows.push([f.name,f.creator,f.series,f.date,c?.effort,c?.aa,c?.aa_version,f.eci,c?.cost_usd,cost,c?.source_url,data.meta.generated_at]);}
    const blob=new Blob(['\uFEFF'+rows.map(r=>r.map(csvValue).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'});
    const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;
    const day=(data.meta.generated_at||new Date().toISOString()).slice(0,10);
    a.download=`AA_ECI_${day}.csv`;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),5000);
    const b=$('export');b.textContent=t('exported')(all.length);clearTimeout(exportTimer);exportTimer=setTimeout(()=>{b.textContent=t('export');},1500);
  });
  render();
})();
