# 公开站用户场景审查 · 2026-11-10

后续修复和验收见 [2026-11-11 闭环报告](PUBLIC_SITE_ISSUE_CLOSURE_2026-11-11.md)。以下保留最初审查时的发现与原始证据。

本轮直接测试线上公开站，并用本地代码定位原因。发现 13 项可复现问题：5 项优先修复，8 项随后打磨。当前更影响用户的是检索、选择状态、来源说明和跨页面连续性。

本轮只新增审查报告、浏览器证据与复现脚本，未修改业务代码或部署。

## 测试范围与边界

| 用户角色 | 实际任务 | 结果 |
| --- | --- | --- |
| 首次访问的中文读者 | 首页理解覆盖范围，搜索“流感”“日本流感” | 普通流感入口遗漏；组合查询无结果 |
| 公共卫生分析人员 | 切换疾病、选择国家、切换语言继续比较 | 已选国家残留；切换语言重置比较对象 |
| 研究人员 | Ask GIDS 查询日本流感，查看曲线、证据摘要与相关文章 | 可得到结果，但来源摘要范围不随选择收窄；目录加载较大 |
| 数据使用者 | 搜索日本数据，选择周期与 CSV，保存文件 | 实际下载成功；目录行数和当前文件行数容易混淆 |
| 持续关注者 | 从日本流感链接进入订阅页面 | JP、influenza、中文正确预选；国家名称仍为英文 |
| 手机与键盘用户 | 390px 浏览、360px 导航、Escape 返回焦点 | 未发现页面横向溢出；菜单与下载弹窗焦点恢复正常 |
| 深色主题、多语言读者 | 主站→论文子站→报告子站 | 语言路径可用；主题没有跨子域保持 |

- 主站：`https://globalinfectiousdisease.com`，并访问 Papers、Reports 子站。
- 主路由检查：21 个路由 × 1440px / 390px，共 42 次。40 次正常内容页返回 200，2 次故意访问不存在路径返回 404；这些检查中没有捕获页面脚本异常、非预期 HTTP 错误或横向溢出。
- 已滚动页面触发延迟加载；额外检查搜索、国家/疾病详情、对比、下载、态势、研究、Ask、图谱、完整性通知、订阅、更新页及法语抽样页面。
- 订阅仅检查配置和预选，没有发送有效订阅或邮件。未验证邮件实际投递。
- 503 场景通过浏览器本地拦截模拟，不是线上故障。
- 这是用户体验与前端行为抽样，不代表全部数据、医学内容和所有浏览器已经验证。

## P1：优先修复

### 01. “流感”搜索找不到普通流感

**复现：** 中文全站搜索输入“流感”；或在对比工具、下载目录中输入相同词。

**实际：** 结果包含流感嗜血杆菌、禽流感、流感并发重症，没有普通流感 `D038 / influenza`。全站搜索显示 100 条结果，其中疾病仅 5 条；“日本流感”全站搜索显示 0 条。同一个“日本流感”问题在 Ask 页面却能识别，入口之间行为不一致。

**原因：** 当前标准名称为“流行性感冒”，不包含连续子串“流感”。对比工具仅匹配名称、编号；搜索索引没有加入常用别名；全站搜索也没有组合词解析。

**建议与验收：** 共用疾病别名索引，覆盖“流感/流行性感冒/influenza/flu”等；精确实体排在相关病原体之前。“日本流感”应至少引导至日本×流感或 Ask，而不是只有 0 条结果。

**定位：** [CompareWorkspace.tsx](../astro-site/src/components/charts/CompareWorkspace.tsx) 第 134 行；[search-index.json.ts](../astro-site/src/pages/site-data/search-index.json.ts) 疾病 aliases；[SearchPage.astro](../astro-site/src/components/SearchPage.astro) `rank()`；[PublicDownloads.astro](../astro-site/src/components/public/PublicDownloads.astro) aliases。

**证据：** [对比搜索截图](benchmarks/public_ux_2026-11-10/compare-flu-search.png)、[交互记录](benchmarks/public_ux_2026-11-10/journeys.json)。

### 02. 切换疾病后，已选国家数量与实际曲线不一致

**复现：** 打开默认对比页，默认是疟疾、澳大利亚、奥地利；添加流行性感冒，再取消疟疾。

**实际：** 国家计数仍为 `2/4`，但可见列表中只勾选澳大利亚。奥地利不在当前疾病可用国家列表中，却继续占一个名额；结果仍渲染为对比图，而实际只剩一个有效国家。

**原因：** 疾病变化更新了 `available`，没有校验 `countries`；图表显示条件使用 `countries.length >= 2`，不是有效序列数。

**建议与验收：** 对失效选择给出提示并允许清除，或在疾病切换时自动移除并明确告知；每张结果图显示有效参与国家数。不得出现“已选 2 个、实际 1 条”却没有提示。

**定位：** [CompareWorkspace.tsx](../astro-site/src/components/charts/CompareWorkspace.tsx) 第 127–145、168 行附近。

**证据：** [切换后截图](benchmarks/public_ux_2026-11-10/compare-stale-countries.png)、`journeys.json → compareStaleCountries`。

### 03. 日本流感曲线的来源摘要显示澳大利亚

**复现：** 打开 [Ask 日本流感](https://globalinfectiousdisease.com/zh/research/ask/?q=%E6%97%A5%E6%9C%AC%E6%B5%81%E6%84%9F)，等待答案与监测曲线。

**实际：** 图中选择日本，来源摘要却是 `Australia NINDSS · Australia NINDSS — Australian Capital Territory +119`，覆盖时间从 `1924-01-01` 开始。这里展示的是整个疾病数据集的来源和跨度，容易被读者当成当前日本曲线的来源。

**原因：** `RuntimeChartSource` 按整个疾病 summary 提取全部来源，截取前两项；没有接收当前国家/序列选择。

**建议与验收：** 图下来源、日期范围与当前序列同步；全数据集说明另行标识。选择日本时应展示相应日本来源，不把全局摘要作为选中曲线的直接注释。

**定位：** [RuntimeChartSource.tsx](../astro-site/src/components/charts/RuntimeChartSource.tsx) 第 5–10 行；[SurveillanceExplorer.tsx](../astro-site/src/components/charts/surveillance/components/SurveillanceExplorer.tsx) 第 470 行。

**证据：** [浏览器记录](benchmarks/public_ux_2026-11-10/browser-observations.json)。此项是来源说明范围错误，不是已证明曲线数值错误。

### 04. 切换语言会静默丢失对比条件

**复现：** 打开 `/zh/compare/?d=D038&c=CN,JP&u=count`，从语言菜单选择 English。

**实际：** 跳至 `/compare/`，重新变成 Malaria + Australia + Austria。原先的中国、日本、流感都消失。

**原因：** 静态生成的语言链接读取构建时 `Astro.url.search`，没有在浏览器中带上当前查询参数。

**建议与验收：** 语言切换保留当前有效查询参数和锚点；对比、国家筛选、搜索、Ask、态势深链接分别验收。

**定位：** [Header.astro](../astro-site/src/components/Header.astro) 第 80、102 行。

**证据：** `edge-cases.json → language`。

### 05. Ask 一次读取全部目录，手机流量与解析负担较大

**复现：** 冷启动带“日本流感”问题的 Ask 页面，查看网络请求。

**实际：** 目录请求 223 个，即 manifest + 222 个分片；一次浏览器测量传输约 **20.13 MB**，解码后约 **80.70 MB**。问题只针对一个国家和一种疾病，仍加载全目录后在浏览器匹配。

**原因：** `ask()` 调用不带参数的 `loadResearchCatalogue()`，客户端用 `Promise.all` 获取全部分片。现有客户端虽然支持 scope/limit，Ask 没有使用。

**建议与验收：** 先用轻量实体索引解析问题，再按疾病/地区取必要分片；摘要详情按结果读取。需要泛化查询时可做渐进检索和进度反馈，并确保局部查询没有遗漏相关结果。冷缓存日本流感查询不应再无条件请求全部 222 个分片。

**定位：** [research/ask.astro](../astro-site/src/pages/research/ask.astro) 第 367 行；[research-catalogue-client.ts](../astro-site/src/lib/research-catalogue-client.ts) 第 17、26 行。

单次桌面、不限速、CPU 1× 的 DevTools 冷加载结果：LCP **1,396 ms**，TTFB **185 ms**，CLS **0.01**；LCP 元素是说明段落 `p.ask-lede`，不是检索答案。因此首屏指标不能代替“得到结果”的体验。没有采集 INP 或真实用户分位数据；不把该桌面时间外推成手机时间。主要优化对象是目录载荷，而非 trace 中预计节省 0 ms 的 CSS/图片项。

## P2：随后打磨

| 编号 | 问题与复现结果 | 建议与验收 | 定位 |
| --- | --- | --- | --- |
| 06 | 全站搜索索引模拟 503 后，弹窗显示“未找到匹配内容”；恢复网络并重开弹窗仍如此。把加载失败当成了业务空结果。 | 分开加载中、无匹配、加载失败；失败显示重试，不能缓存成功标记。 | `Header.astro` 第 277–281 行，`loadSearch()` |
| 07 | 首页写“有更新的地点 1,776”，进入态势地图看到“227 地点”。首页是所有 points 数，地图默认最近 30 天再去重，标签未表达范围差异。 | 统一口径或明确“历史通报记录”“近 30 天地点/事件”；指标旁直接显示窗口。 | `SituationMetrics.tsx` 第 9 行；`SituationExplorer.tsx` 第 349 行；`situation/model.ts` 第 27 行 |
| 08 | 下载目录日本行在“当前范围 2026–now”下面显示 108,621 条；弹窗中当前分片是 9,084 条，108,621 是全部周期总数。同一 CSV 在目录显示 6.6 MB，弹窗显示 6.3 MB。 | 分开“当前文件记录数”和“全历史记录数”；统一 MB/MiB 单位。数字应与旁边的下载对象对应。 | `PublicDownloads.astro` 第 40–57 行；`SurveillanceDownloadContents.tsx` |
| 09 | 中文首页报告国家显示 Japan；研究国家筛选显示 Brazil/Japan 等；主题出现 Transmission dynamics；文章研究类型和完整性状态显示 `Mathematical modelling`、`current`；搜索弹窗分类显示 DISEASE。 | 优先翻译操作标签、筛选值和状态枚举。论文原题可保留，不应与 UI 漏翻译混为一谈。 | `PublicHomeRedesign.astro` 第 277 行；`Header.astro` 第 347 行；研究列表、文章模板、订阅选项渲染 |
| 10 | 主站切为深色，进入 Papers 和 Reports 都重新变成浅色。 | 三个公开域保持同一主题偏好；返回主站、刷新和新开页行为一致。 | `Header.astro` 中 origin 独立的 localStorage |
| 11 | Axe 在首页深色态势条检出文字对比度约 2.30:1；订阅页徽标、偏好标签、协议链接约 1.55:1 / 2.48:1。 | 在浅色、深色背景分别检查字体颜色；清除这几项对比度违规。 | `SituationMetrics.tsx` 的 `--text-muted` 与 `PublicHomeRedesign.astro` 第 355 行背景；`subscribe.astro` 的 teal 字体 |
| 12 | 文章“证据关系”称有 13 条可审计关系，实际只显示重复的 `about disease`、`addresses topic` 等词，没有目标实体。 | 展示“疾病：基孔肯雅热”“地区：法国”等关系与目标，可链接实体详情，中文翻译关系类型。 | `research/articles/[slug].astro` 第 329 行仅渲染 `edge.relation` |
| 13 | 首页“连接研究”卡片显示“4 已发布分析”，数值来自监测报告数量；点击后研究雷达显示 31,531 条公开目录、7,532 条研究订阅记录等不同范围。 | 研究入口采用明确的研究指标，或移除该数字；监测报告数量留在报告入口。 | `PublicHomeRedesign.astro` 第 148 行附近 `reportsRaw.length` |

06、10 的浏览器复现位于 [edge-cases.json](benchmarks/public_ux_2026-11-10/edge-cases.json)；11 位于 [route-audit.json](benchmarks/public_ux_2026-11-10/route-audit.json) 的 `axe` 字段。12 的实例为 [贝热拉克基孔肯雅热文章](https://papers.globalinfectiousdisease.com/zh/research/articles/10-1371-journal-pntd-0014184-3645e0a4/)。

## 已通过的重点流程

- 手机 390px 下载日本 `2026-2029` CSV：成功保存 `globalid-jp-2026-2029.csv`，**6,625,805 字节**；筛选后只有 1 个文件卡；弹窗宽 352px，没有页面横向溢出；Escape 关闭后焦点回到下载按钮。
- 360px 手机导航：打开和关闭状态正确，Escape 恢复按钮焦点，没有横向溢出。
- Papers 子站全站搜索可加载主站索引并返回登革热结果；报告 67 的中文页面正常打开。
- 订阅深链接正确预选中文、JP 和 influenza。
- 不存在路径返回真实 404。

下载长流程首次探针有一次等待超时，后续独立探针、手动浏览器检查与实际文件保存都通过，因此没有把该超时列为已确认下载故障。

## 待进一步验证，不计入上述 13 项

- 研究页 Axe 另报 2 个 `target-size` 节点，提示“部分遮挡”。尚未完成逐节点视觉复核，不直接认定为稳定的点击障碍。
- 对比图代码在绘制前过滤 null 数据点，可能跨过缺失期间连线；还需用真实缺失序列与来源口径验证。
- 下载目录的“公开记录”累加国家与疾病两个维度的数据集行数，是否应称为去重记录需要先核对产品定义。
- Ask 中一条 Hib 联合疫苗研究被标作流感精确匹配；需独立核查疾病分类及原文，不凭标题判定医学内容错误。

## 建议实施顺序

1. 先修 01、02、03、04、06：减少查不到、选错对象和错误来源印象；补有意义的交互回归。
2. 修 05：降低 Ask 的目录请求量，单独记录查询到结果的耗时与字节数。
3. 统一 07、08、13 的数字范围、窗口和日期说明。
4. 完成 09、10、11、12 的本地化、主题、对比度与证据关系呈现。

复现脚本与原始结果保存在 [证据目录](benchmarks/public_ux_2026-11-10/)。脚本需要项目已安装的 Playwright、Chromium；从仓库根目录可运行 `node docs/benchmarks/public_ux_2026-11-10/edge-cases.mjs` 等。脚本访问线上只读页面，异常场景拦截仅作用于本地测试浏览器。
