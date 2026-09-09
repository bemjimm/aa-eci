"""Optional UI check on synthetic data. Usage: python tests/browser_smoke.py /tmp/qa"""
from __future__ import annotations
import csv
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from run import main
from tests.test_pipeline import aa_row, body, meta, score


def check(destination: Path):
    from playwright.sync_api import sync_playwright
    destination.mkdir(parents=True, exist_ok=True)
    aa_rows=[aa_row('high',score=50,cost=2),aa_row('low','Sample Atlas 1 (low)',40,.1),
             aa_row('free','Free Sample',10,0),aa_row('null','No Price Sample',None,None)]
    aa_rows += [aa_row(f's{i}',f'Synthetic Model {i:03}',round(i*0.33,1),(i+1)/100) for i in range(130)]
    aa_file=destination/'aa.json';aa_file.write_text(json.dumps(body(aa_rows)),encoding='utf-8')
    score_file=destination/'eci.csv'
    with score_file.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['model','eci','date']);w.writeheader()
        w.writerows([score(),score('Synthetic Model 100',160)])
    meta_file=destination/'metadata.csv'
    with meta_file.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['model_group','model_version','date','organization']);w.writeheader()
        w.writerows([meta(),meta('Epoch Only'),meta('Unscored Epoch'),meta('Synthetic Model 100')])
    args=['--aa-json',str(aa_file),'--epoch-csv',str(score_file),'--epoch-metadata',str(meta_file),
          '--out',str(destination/'site'),'--runs',str(destination/'runs'),'--state',str(destination/'state.sqlite')]
    assert main(args)==0
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1440,'height':1000},device_scale_factor=1)
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.set_content((destination/'site/index.html').read_text(encoding='utf-8'))
        if page.locator('h1').inner_text()!='大模型选型表':
            page.locator('#lang').click()  # headless 浏览器默认 en-US，价格断言固定为中文（¥）
        # 默认：AA 前 100，每页 50
        assert page.locator('#aaTop').input_value()=='100'
        assert page.locator('tr.family').count()==50
        page.locator('#pageSize').select_option('0')
        assert page.locator('tr.family').count()==100
        page.locator('#aaTop').fill('')
        assert page.locator('tr.family').count()==135
        page.locator('#aaTop').fill('50')
        assert page.locator('tr.family').count()==50
        page.locator('#aaTop').fill('')
        page.screenshot(path=str(destination/'desktop.png'),full_page=False)
        # 搜索、代表配置与展开
        page.locator('#search').fill('Sample Atlas 1')
        assert page.locator('tr.family').count()==1
        assert page.locator('tr.family td.num:nth-child(2) .val').inner_text()=='50'
        assert page.locator('tr.family td.num:nth-child(2) .rk').inner_text()=='#1'
        assert page.locator('tr.family td.num:nth-child(3) .rk').inner_text()=='#2'
        assert page.locator('tr.family td.num:nth-child(4) .val').inner_text()=='14'
        assert page.locator('tr.family button.toggle').text_content()=='▸'
        page.locator('tr.family button.toggle').click()
        assert page.locator('tr.child').count()==2
        assert page.locator('tr.child td.num:nth-child(2) .rk').count()==2
        assert page.locator('tr.child').first.locator('td.num:nth-child(2) .val').inner_text()=='50'
        assert page.locator('tr.child').first.locator('td.num:nth-child(2) .rk').inner_text()=='#1'
        page.locator('#costMax').fill('1')
        assert page.locator('tr.family td.num:nth-child(2) .val').inner_text()=='40'
        assert page.locator('tr.family td.num:nth-child(4) .val').inner_text()=='0.7'
        page.locator('#costMax').fill('')
        page.locator('#search').fill('')
        # 双榜叠加：AA 前 60 且 ECI 前 1
        page.locator('#aaTop').fill('60')
        page.locator('#eciTop').fill('1')
        assert page.locator('tr.family').count()==1
        assert page.locator('tr.family .name').first.inner_text()=='Synthetic Model 100'
        page.locator('#aaTop').fill('')
        page.locator('#eciTop').fill('')
        # 空态与清除筛选（只清筛选）
        page.locator('#search').fill('Epoch Only')
        page.locator('#aaTop').fill('50')
        assert page.locator('tr.family').count()==0
        page.locator('#clearEmpty').click()
        assert page.locator('tr.family').count()==135
        # ECI 前 N
        page.locator('#eciTop').fill('50')
        assert page.locator('tr.family').count()==2
        page.locator('#eciTop').fill('')
        # 分歧标记（全局分位，与筛选无关）
        row100 = page.locator('tr.family').filter(has_text='Synthetic Model 100')
        assert row100.locator('td.num:nth-child(3) .val.diverge').count()==1
        # 重置恢复默认（AA 前 100）
        page.locator('#reset').click()
        assert page.locator('tr.family').count()==100
        # 排序
        page.locator('[data-sort="eci"]').click()
        assert page.locator('tr.family .name').first.inner_text()=='Synthetic Model 100'
        page.locator('#aaTop').fill('')
        page.locator('[data-sort="cost"]').click()
        assert page.locator('tr.family .name').first.inner_text()=='Free Sample'
        assert page.locator('tr.family td.num:nth-child(4)').first.inner_text()=='0'
        assert page.locator('tr.family td.num:nth-child(4)').last.inner_text().strip()=='—'
        page.locator('[data-sort="cost"]').click()
        assert page.locator('tr.family td.num:nth-child(4)').first.inner_text()!='—'
        assert page.locator('tr.family td.num:nth-child(4)').last.inner_text().strip()=='—'
        # 英文界面价格为 AA 原始美元（Sample Atlas 1 high = $2）
        page.locator('#lang').click()
        assert page.locator('h1').inner_text()=='LLM Selection Table'
        page.locator('#search').fill('Sample Atlas 1')
        assert page.locator('tr.family td.num:nth-child(4) .val').inner_text()=='2'
        page.locator('#lang').click()
        assert page.locator('tr.family td.num:nth-child(4) .val').inner_text()=='14'
        # 导出
        with page.expect_download() as info:
            page.locator('#export').click()
        saved=destination/'export.csv';info.value.save_as(saved)
        exported=list(csv.reader(saved.open(encoding='utf-8-sig')))
        assert len(exported)==137,len(exported)
        assert len(exported[0])==12,exported[0]
        # 分页
        page.locator('#pageSize').select_option('50')
        page.locator('#next').click()
        assert page.locator('#pageInfo').inner_text()=='2 / 3'
        # 移动端
        page.set_viewport_size({'width':390,'height':844})
        page.screenshot(path=str(destination/'mobile.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+1')
        assert not errors,errors
        browser.close()
    receipt={'test_data':'synthetic','unit_tests':28,'browser':'Chromium','desktop':'passed',
             'mobile':'passed','top_n_filters':'passed','representative_and_expand':'passed',
             'clear_filters_scope':'passed','sorting_null_last':'passed','divergence':'passed',
             'csv_all_filtered_configurations':'passed','console_errors':errors,
             'live_collection':'not_run_no_api_key_and_network_unavailable'}
    (destination/'receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    print(json.dumps(receipt,indent=2))


if __name__=='__main__':
    check(Path(sys.argv[1]).resolve())
