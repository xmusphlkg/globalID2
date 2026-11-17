# 2026-11-17 文献链接、样式与分类修复

本次完成工作区代码修复、真实文章预览和回归验证。尚未部署 Worker、发布文献模板，
也未重分类生产数据库；线上当前错误不会因本地测试通过而自动消失。

## 已复现的问题

- 用户提供的 [Ebola haemorrhagic fever in Zaire, 1976.](https://papers.globalinfectiousdisease.com/research/articles/ebola-haemorrhagic-fever-in-zaire-1976-0ab60c75/)
  返回 HTTP 503 和 `{"error":"data_unavailable"}`。中文研究首页当时 28 个不同详情链接中，
  20 个返回 200、8 个历史记录返回 503；这是首页样本，并非全站链接普查。
- 导出端已允许带 `lit_hist_` ID、`historical_baseline=true`、`indexable=true` 且缺少
  `editorial_status` 的历史公开记录；Worker 却要求状态严格等于 `published`，导致协议不一致。
- [PLOS 近期文献示例](https://papers.globalinfectiousdisease.com/zh/research/articles/10-1371-journal-pntd-0014184-3645e0a4/)
  返回 200，但浏览器 `document.styleSheets.length === 0`，正文使用 Times New Roman，
  布局退回默认样式。HTML 根本未引用 CSS，并非已有 CSS 请求返回 404。
  同时研究首页的两个样式文件均返回 200。旧版保留文章模板仍含正常 CSS 引用。
- 疾病与国家误分类的证据、边界和存量修复步骤见
  [分类准确性诊断](2026-11-17-literature-classification-repair.md)。

## 修复

- Worker 与 Python 导出端使用相同的历史公开记录准入条件，继续拒绝草稿、排除记录及
  缺少历史公开标记的记录；更新渲染缓存版本，避免部署后复用旧渲染结果。
- 独立文献构建在写缓存和输出发布目录前执行资源校验；缺少全局 CSS 的构建直接失败。
  校验脚本进入模板指纹，校验规则改变后不复用旧模板身份。
- v3 候选生成检查新编译和复用的文章模板：必须包含样式，真实 GIDS 页面还必须具备
  全局布局和文章布局规则；只有独立主题变量不能通过，引用的 CSS 字节须匹配冻结清单。
- 发布后的抽查同时覆盖普通公开记录和历史公开记录的英、中、法页面，并保留更新文章的
  检查；不能再仅凭一个普通文章样本通过整个文章服务验收。

现有工作区的 Astro 构建路径规范化修复也用于本次真实预览。通过当前构建重新生成模板后，
样式引用正常；已有线上无样式模板仍需要重新编译并发布。

## 验证

- Worker：34 项测试、TypeScript 检查通过；新增历史记录用例在修复前复现 503。
- Python 投影、完整快照、模板样式：43 项测试通过。
- 独立文献构建、缓存与资源检查：7 项测试通过，包含真实 Astro 构建。
- 分类及相关维护、发布检查：101 项测试通过；生产数据库只执行四篇 DOI 的只读试算。
- 用用户提供的 Ebola 文章和 PLOS 文章真实 JSON，编译六个本地 Astro 页面，再通过修复后的
  Worker 投影函数生成实际详情 HTML。Playwright 检查三语言六页均返回 200、CSS 存在、
  正文网格与全局字体正常、构建资源无失败；1440px 桌面与 390px 手机均无横向溢出。

本次预览证据位于未纳入 Git 的
`data/cache/literature-repair-preview-vQMBGA/`：`asset-audit.json`、`browser-check.json`、
`ebola-desktop.png`、`ebola-mobile.png`。预览使用原始公开数据，不代表已纠正所有内容。

## 上线仍需完成

1. 发布修复后的文献 Worker，核查用户提供的链接及历史、普通记录三语言样本。
2. 重新编译并通过既有发布流程发布文章模板与资源，不能只复用线上无样式模板。
3. 分类版本已升至 v6。按分类诊断中的有界重分类流程修复存量记录，复核受错误分类影响的
   历史摘要，再冻结并发布文献 JSON 和文章投影。旧 v5 记录不能直接通过新版发布门禁；
   不能仅修改版本标记，也不能仅部署模板就宣称历史数据已修复。
