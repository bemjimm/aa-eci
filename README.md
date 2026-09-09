# AA × ECI 全量聚合管线

抓取完整模型数据，归入模型族，生成可离线打开的交互 HTML。Python 3.10+；主程序仅使用标准库，无需安装 Python 依赖或前端依赖。

## 本地运行

在项目目录执行：

```bash
export AA_API_KEY="你的 Artificial Analysis API Key"
python3 run.py
```

运行成功后打开 `dist/index.html`。API Key 只用于采集，不写入 HTML、JSON 或 SQLite。

AA Key 在官方 Data API 文档的 “API key management” 入口创建：
https://artificialanalysis.ai/data-api/docs

程序使用 Free 接口，不需要 Pro 数据套餐。

## 运行产物

| 路径 | 用途 |
|---|---|
| `dist/index.html` | 独立 HTML，数据、样式和脚本全部内嵌 |
| `dist/models.json` | 完整模型族、AA 配置和来源关系 |
| `dist/models.csv` | 所有 AA 配置，以及没有 AA 配置的 Epoch 模型族 |
| `state.sqlite` | 持久化身份映射与历史快照；后续运行继续使用 |
| `runs/<时间>/raw/` | 本轮取得的所有原始文件 |
| `runs/<时间>/audit.json` | 输入输出覆盖数量、身份对应、未合并候选、原始文件哈希 |
| `runs/<时间>/site/` | 本轮生成的完整站点副本 |

## 表格使用

初始按 AA 降序显示全部模型族，每族展示最高分配置；同分选更便宜的配置。单击左侧箭头展开族内所有配置。

筛选支持关键词（含厂商与系列名）、可叠加的 AA/ECI 前 N 名次输入（留空不限，AA 默认前 100）和最高单任务成本上限（中文界面为人民币，按构建时汇率折算；英文界面为美元原始值）。两榜相对位置差大的行 ECI 加黄底，量纲与口径说明在列头悬停提示里。点击列标题切换升降序（含发布日期）；族行显示最高分档，行首箭头展开档位，逐配置完整清单用导出 CSV。界面中英双语，默认跟随浏览器语言，页头一键切换。

“族内：最低价档”在已经满足筛选条件的配置中选最低价，不会把一个配置的最高分和另一个配置的最低价拼在一起。导出 CSV 包含当前筛选结果的全部配置，不限于当前分页或展开状态。

当前的筛选、排序和视图状态会同步进网址 hash 并保存在浏览器本地，点页头「复制链接」即可把当前视图原样分享或收藏；再次打开带 hash 的链接会恢复同一视图。按 `/` 直接聚焦搜索框，搜索框内按 Esc 清空。页头显示快照生成时间，超过 3 天未更新时变为琥珀色，说明数据可能已停更。

## 数据范围

AA Free 接口所有页的所有配置 + Epoch 成绩 CSV 全部模型组 + Epoch 元数据包全部模型组。单来源、未取得某项分数、没有价格的记录全部保留。没有 Top N 名单，没有最近年份限制，没有只保留双榜模型的筛选。

Epoch 元数据中的每个 `model_version` 还保留在对应族的 `epoch_versions` 中。网页的可选配置行对应 AA 测量项；Epoch 内部评测设置用于归族，不伪装成有 API 报价的购买档位。

## 数据源

- AA：`https://artificialanalysis.ai/api/v2/language/models/free?page=1`，逐页读取 `pagination`。
- Epoch 成绩入口：`https://epoch.ai/data/eci_scores.csv`。
- Epoch 元数据：`https://epoch.ai/data/benchmark_data.zip` 中的 `model_metadata.csv`。

入口可在 `config.json` 修改。AA 按官方 API 文档实现；Epoch CSV 适配器接受 `eci` / `ECI` 等明确列名，不把任意数字列当作 ECI。优先使用发布成绩，不在每日更新中重新拟合指数。

## 调整少量别名

`aliases.json` 默认可以为空。常见厂商别名和 effort 后缀由程序处理。

有确定的跨榜对应关系时，写入 AA slug → Epoch 的原始 `model_group`：

```json
{
  "creators": {},
  "names": {},
  "series": {},
  "aa_to_epoch": {
    "实际的-aa-slug": "实际的 Epoch model_group"
  },
  "separate_aa_slugs": []
}
```

`names` 的键为 `厂商|原模型名`，值为统一名称；`series` 的键同样是 `厂商|模型名`，值为大族名称。要让某条 AA 配置独立保留，将 slug 加入 `separate_aa_slugs`。

不确定的匹配不会中断正常数据入库：两边先独立显示，候选写入后台 `audit.json`。修改映射后重跑即可，不用改网页代码。

## 离线重建

对已经完整取得的原始文件重建页面：

```bash
python3 run.py \
  --aa-json /path/to/aa-all.json \
  --epoch-csv /path/to/eci_scores.csv \
  --epoch-metadata /path/to/benchmark_data.zip
```

元数据参数也接受解压后的 `model_metadata.csv`。本地 AA 文件必须是完整的合并结果，不能只传 API 第一页。

## 定时运行

自有服务器每天运行一次 `python3 run.py` 即可。保留 `state.sqlite`，不用常驻 Web 后端；发布 `dist` 目录。

项目也带有 GitHub Actions + Pages 工作流。准备：把项目上传到自己的 GitHub 仓库（公开仓库——Pages 与 Actions 免费，Actions 无分钟数限制，也正是获得 star 的前提），在仓库 Secrets 添加 `AA_API_KEY`，在 Pages 设置中把 Source 选为 GitHub Actions，然后手动运行一次 `Update AA ECI table`。访问地址 `https://<用户名>.github.io/<仓库名>/`。此后按每天 UTC 02:23（北京时间 10:23）调度。

该工作流会创建专用 `pipeline-state` 分支保存 SQLite 身份映射，构建成功后发布 `dist` 至 GitHub Pages。抓取、测试或构建失败时不部署新页面。仓库源码目录不存 API Key。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

可选浏览器测试需要 Playwright 和 Chromium：

```bash
python3 tests/browser_smoke.py /tmp/aa-eci-ui-test
```

测试使用 `Sample`、`Synthetic` 等合成模型，只写到指定测试目录，不进入生产 `dist`。

## 本次交付状态

已完成程序、页面模板、覆盖校验、单元测试和桌面/手机交互检查。当前会话没有 AA API Key，运行容器也无法联网，因此没有取得或附送一份“已抓齐”的最新全量榜单。AA 采集按官方接口契约实现；Epoch CSV 的实时字段及两站联采仍需在你的首次联网运行中验证。

具体架构与默认规则见 `DESIGN.md`。
