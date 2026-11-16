# 新增国家的网站目录与公开主页状态

日期：2026-11-16。**本地目录配置已补齐，新增国家公开主页尚未完成，未部署本轮修改。** 后台数据入库和定时刷新验收见 [接入记录](REMAINING_COUNTRIES_20261116.md)，不代表公开页面已发布。

## 已完成的目录修改

- 本轮 18 个国家和地区（ET、KE、UG、GH、TZ、PH、VN、LA、MM、BN、TL、PY、EC、CR、PA、GP、MQ、GF）均纳入静态国家目录；新增原先缺失的 TZ、MM、BN、TL、CR、PA。
- 更新 WHO GHO 年度基线和 SPF ODISSÉ 年度数据的来源链接、报告频率；目录在尚无公开数据时显示“数据待发布”，不生成指向不存在主页的链接。
- 补齐六国 WHO 区域、世行区域和收入分类。WHO Country Dimension 与世界银行 API 的真实响应及哈希记录在 `reports/onboarding/remaining_website_classifications.json`；哥斯达黎加使用当前 HIC 分类，缅甸和东帝汶为 WHO SEAR。

## 尚未完成的公开发布

本地生成的公开 meta 及国家数据文件未包含这 18 个新增区域。国家主页构建依赖真实公开 meta，不能仅增加目录条目便视为主页完成。后台新增事实仍处于源原生 holding 系列，公开发布关闭；仍需完成映射、发布校验和公开数据快照生成，再构建并部署网站。

只读线上核验：`https://globalinfectiousdisease.com/countries/` 返回 200；`/countries/ke/`、`/countries/tz/` 返回 404。线上 `/site-data/meta.json` 为 2026-11-07 快照，共 295 条区域记录，本轮 18 个新增区域全部缺失。

## 验证

- 国家目录、国旗及分类既有测试：11 项通过，见 `reports/onboarding/remaining_website_tests.log`。
- `npm run check`：523 个文件，0 errors、0 warnings、14 hints；样式边界检查通过。
- 本地 Chromium 浏览器核验通过：英文桌面逐项检查全部 18 个条目的待发布状态和无失效主页链接；中文 390px、法文桌面抽查 TZ／CR。三种语言均验证 CR 的 WHO AMR／世行 HIC 组合筛选，见 `reports/onboarding/remaining_website_browser.json`。

开发预览的目录数据读取需要公开快照可用；本地浏览器验证使用未修改的 `src/data/meta.json` 作为请求夹具，不改变任何公开快照、发布门禁或线上资源。
