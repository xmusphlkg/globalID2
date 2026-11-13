# 主站主题 v1 发布与试点

2026-11-13：独立候选构建、冻结计划执行、预览、公开验证与回滚已完成，公共主题入口已发布。
控制面板按实际配置决定执行能力；当前 `globalid-theme` 公共桶、兼容入口和模板覆盖均 ready。

## 范围

`shared/site-theme-v1.json` 定义已有颜色和间距变量。编译器严格限制变量名、
RGB 数值、长度格式及 schema，拒绝任意 CSS、选择器、脚本和外部 URL。
字体文件、DOM、scoped CSS、工具链和客户端脚本变化仍走模板发布。

首页和单个国家首页的中、英、法模板可以通过构建变量
`PUBLIC_GIDS_THEME_V1_URL` 接入。其他页面，包括 Papers 和 Reports，不接入。
默认配置为空，现有页面行为不变。首次开启或修改入口 URL 必须发布模板，
该环境变量纳入共享页面缓存指纹。原有全局样式缓存失效逻辑没有删除。

`global.css` 保留完整基础样式和变量兜底。外部 CSS 仅覆盖变量，采用不分层
规则覆盖 `@layer base`。兼容入口加载失败时仍使用固定资源中的可读布局。
这并不等于保证首屏无闪动；网络、字体、布局偏移仍需部署前的同条件测量。

## 本地构建与检查

```bash
cd astro-site
npm run test:theme
npm run build:theme
# 需要已有 dist 中的三语言首页和 countries/cn/ 及其静态资源：
npm run verify:theme
```

默认产物在 `exports/theme-candidate/`：

- `theme/versions/sha256/<hash>.css`：不可变主题；候选清单包含 SHA-256 和字节数。
- `theme/compat/v1/theme.css`：只包含一条相对 `@import`，指向同代不可变 CSS。
- `theme-candidate.json`：准备阶段记录，明确 `public_verified=false`。

可以指定输入与独立输出目录：

```bash
node scripts/build-theme.mjs ../shared/site-theme-v1.json ../exports/theme-preview
```

编译过程不导入 Astro/Vite，不读取内容投影，不生成 HTML、不部署 Pages。
更新主题后旧不可变 CSS 保留。这里的零 HTML 计数仅说明候选构建的实际工作，
不是生产环境已启用或整个项目已完成迁移的证据。

`verify:theme` 在隔离 Chromium 中使用已有 HTML 样本，只注入固定主题链接，
拦截所有网络请求到本地文件，覆盖三语言、首页/国家、390/1280 宽度及
默认主题、更新主题、入口失败三种状态，共 36 场景。它检查变量覆盖和兜底，
确认源 HTML 未改变，并输出 `exports/theme-candidate/browser-verification.json`。
它不代替新模板实际部署或性能测试；默认兜底断言绑定当前保留的基础主题。

## 发布任务与回滚

`POST /publish/plans` 的 `kind=theme` 已复用现有任务锁、幂等键和阶段收据：
准备时编译并冻结 CSS 及远端兼容入口基线；执行先上传并公开验证不可变 CSS，
再按原基线条件提交兼容入口，最后验证公开可见性并确认任务。内容渲染、HTML 组装、
Pages 和 Worker 部署均为 0。已入队候选冻结目标和字节，暂停新发布不会改变恢复目标。

`GET /publish/theme-history` 提供已验证版本；`POST /publish/rollback-plans`
只准备恢复某一兼容 v1 版本的新计划，仍需通过 `POST /publish/runs` 执行同一受控提交流程。
冲突、候选篡改、已过期计划和未验证回滚目标均拒绝执行。不要新增绕过任务锁的入口写入器。

所需配置（初次接入必须先完成公开基础设施和模板发布）：

```dotenv
GIDS_THEME_PUBLISH_ENABLED=false
GIDS_THEME_R2_BUCKET=
GIDS_THEME_PUBLIC_BASE_URL=
GIDS_THEME_COMPAT_V1_READY=false
PUBLIC_GIDS_THEME_V1_URL=
```

启用前先执行只读就绪检查。它只读取配置并在目标完整时探测兼容 CSS，
不会上传对象、切换入口或修改开关：

```bash
venv/bin/python scripts/check_theme_publication_readiness.py --pretty
```

当前生产配置的检查结果为 `ready=true`：独立公开桶、HTTPS 自定义域名、
兼容入口和模板兼容确认均已配置，发布开关已打开。后续发布仍必须保持
`ready=true`；任何一个原因出现都应 fail-closed。

公开 base 必须是无路径、无查询串的 HTTPS 自定义域名，不接受 r2.dev；
`PUBLIC_GIDS_THEME_V1_URL` 必须等于 `<base>/theme/compat/v1/theme.css`。
确认线上首页与国家页已接入后才将 `GIDS_THEME_COMPAT_V1_READY` 和发布开关设为 true。
S3 凭据复用现有配置，桶必须独立于 Papers/Reports。

计划预览冻结三语言首页与国家代表页及其固定 CSS，注入精确候选主题；
控制台支持 390/1280 视口切换。预览由私有鉴权接口提供，带 no-store/noindex、
CSP 与 sandbox，禁用脚本、表单及导航。缺少本地样本时明确报告不可预览，
不会读取线上可变主题或把预览当成真实加载性能。

公开域名需独立公开 bucket、缓存规则及路由核验；不得直接开放现有混合 bucket。
候选的长缓存/短缓存元数据只是期望策略，不能代替真实 CDN 响应核验。
固定预览可把 `PUBLIC_GIDS_THEME_V1_URL` 指向不可变 CSS；正常接入使用兼容入口。
不为可变入口设置固定 SRI。已打开页面不会主动刷新主题。

正式阶段 2 生产验收还需要：配置真实公开目标、验证页面覆盖率、实际执行与回滚，
桌面/手机与三语言的首屏测量，以及真实颜色更新前后的内容与 HTML 哈希对比。
