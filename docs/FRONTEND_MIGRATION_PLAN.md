# GlobalID Frontend Migration: React/Next.js + FastAPI

## 现状分析

| 维度 | 当前 | 目标 |
|------|------|------|
| 前端 | Streamlit (Python, 全页重跑) | Next.js 15 + React 19 (局部更新) |
| 图表 | Plotly.js (~3.5MB) | ECharts (~800KB, tree-shakable) |
| API | 无（SQL 直接内嵌在 UI 代码中） | FastAPI REST API |
| 状态管理 | `st.session_state` | TanStack Query + Zustand |
| 国际化 | 自写 i18n.py dict | next-intl |
| 实时推送 | `st_autorefresh` 轮询 | WebSocket / SSE |
| 样式 | 自定义 CSS 注入 | Tailwind CSS + shadcn/ui |

### 已有优势（可直接复用）
- ✅ SQLAlchemy async 引擎 + 连接池已配置（`src/core/database.py`）
- ✅ 完整的 Domain Models（Task, Report, Disease, DiseaseRecord）
- ✅ Service 层（CrawlService, ReportService, TaskManager）
- ✅ FastAPI + Uvicorn 已在 `requirements.txt` 中
- ✅ Redis 缓存层已实现（`src/core/cache.py`）
- ✅ `dashboard/api/` 目录已存在（API 已迁移到此路径）

---

## 整体架构

```
┌──────────────────────────────────────────────────┐
│                    Browser                        │
│  Next.js 15 (App Router) + React 19              │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────┐  │
│  │ ECharts  │ │ shadcn/ui│ │ TanStack Query   │  │
│  └──────────┘ └──────────┘ └──────────────────┘  │
└──────────────────┬───────────────┬───────────────┘
                   │ REST API      │ WebSocket
                   ▼               ▼
┌──────────────────────────────────────────────────┐
│              FastAPI Backend                       │
│  ┌────────┐ ┌────────────┐ ┌──────────────────┐  │
│  │ Routers│ │ Pydantic   │ │ WebSocket Hub    │  │
│  │ (REST) │ │ Schemas    │ │ (task/report)    │  │
│  └───┬────┘ └────────────┘ └──────────────────┘  │
│      │                                            │
│  ┌───▼──────────────────────────────────────────┐ │
│  │ Service Layer (已有)                          │ │
│  │ CrawlService │ ReportService │ TaskManager   │ │
│  └───┬──────────────────────────────────────────┘ │
│      │                                            │
│  ┌───▼──────────┐  ┌────────────────────────────┐ │
│  │ SQLAlchemy   │  │ Redis Cache (已有)          │ │
│  │ Async Engine │  │                             │ │
│  └──────────────┘  └────────────────────────────┘ │
└──────────────────────────────────────────────────┘
                   │
                   ▼
          ┌──────────────┐
          │  PostgreSQL   │
          │  (19 tables)  │
          └──────────────┘
```

---

## 分阶段实施计划

### Phase 0: API 基础设施（3-4天）

**目标**: 搭建 FastAPI 后端，暴露所有 dashboard 需要的数据接口

#### 0.1 API 项目结构

```
dashboard/api/
├── __init__.py
├── main.py              # FastAPI app factory
├── deps.py              # 依赖注入(db session, auth)
├── schemas/             # Pydantic response/request models
│   ├── __init__.py
│   ├── common.py        # 分页、排序通用 schema
│   ├── country.py
│   ├── disease.py
│   ├── disease_record.py
│   ├── report.py
│   └── task.py
├── routers/
│   ├── __init__.py
│   ├── countries.py     # GET /countries, GET /countries/{id}
│   ├── diseases.py      # GET /diseases, GET /diseases/{id}/records
│   ├── overview.py      # GET /overview/kpis, GET /overview/top-diseases, GET /overview/trend
│   ├── analysis.py      # GET /analysis/disease/{id}, GET /analysis/compare  
│   ├── quality.py       # GET /quality/stats, GET /quality/gaps, GET /quality/sources
│   ├── reports.py       # GET /reports, GET /reports/{uuid}, GET /reports/{uuid}/sections
│   ├── tasks.py         # GET /tasks, GET /tasks/{uuid}, POST /tasks, WebSocket /tasks/ws
│   └── explorer.py      # POST /explorer/query (parameterized, allowlisted tables only)
└── services/
    └── query_builder.py # 安全的查询构建器，替代 f-string SQL
```

#### 0.2 API 端点设计

**Overview 页面 — 合并为 1 个接口**
```
GET /api/v1/overview/summary?country_id=1
Response:
{
  "total_diseases": 42,
  "total_records": 15832,
  "latest_date": "2026-03-01",
  "recent_cases_30d": 128456,
  "top_diseases": [
    {"name": "流感", "name_en": "Influenza", "total_cases": 50000, "total_deaths": 120}
  ]
}
```

**趋势数据**
```
GET /api/v1/overview/trend?country_id=1&disease_id=D001&interval=365&granularity=month
Response:
{
  "data": [
    {"time_period": "2025-04-01", "cases": 1234, "deaths": 5}
  ]
}
```

**疾病分析**
```
GET /api/v1/diseases?country_id=1
GET /api/v1/diseases/{disease_code}/records?country_id=1&fields=full
GET /api/v1/analysis/compare?country_id=1&diseases=D001,D002,D003
```

**报告**
```
GET /api/v1/reports?country_id=1&status=COMPLETED&limit=50
GET /api/v1/reports/{uuid}
GET /api/v1/reports/{uuid}/sections
GET /api/v1/reports/{uuid}/sections/{id}/conversations
```

**任务**
```
GET  /api/v1/tasks?status=RUNNING,PENDING&type=CRAWL_DATA&limit=50
GET  /api/v1/tasks/{uuid}
GET  /api/v1/tasks/{uuid}/workbook
POST /api/v1/tasks           # 创建新任务
WS   /api/v1/tasks/ws        # 实时任务状态推送
```

**数据质量**
```
GET /api/v1/quality/stats?country_id=1
GET /api/v1/quality/gaps?country_id=1
GET /api/v1/quality/sources?country_id=1
GET /api/v1/quality/completeness?country_id=1&start=2020-01-01&end=2026-03-01
```

#### 0.3 关键实现要点

1. **参数化查询** — 所有 SQL 使用 SQLAlchemy ORM 或 `bindparam()`，杜绝 f-string 拼接
2. **分页** — 统一 `?page=1&per_page=50` 参数，返回 `{data, total, page, per_page}`
3. **缓存** — 使用已有 Redis cache，对 overview/summary 等热点接口设置 5min TTL
4. **CORS** — 允许 `localhost:3000`（Next.js dev server）
5. **健康检查** — `GET /api/v1/health`

---

### Phase 1: Next.js 项目初始化（2天）

#### 1.1 项目创建

```bash
# 在 globalID2/ 下创建前端项目
npx create-next-app@latest dashboard --typescript --tailwind --eslint --app --src-dir
cd dashboard
```

#### 1.2 前端项目结构

```
dashboard/
├── src/
│   ├── app/
│   │   ├── layout.tsx           # Root layout (providers, sidebar)
│   │   ├── page.tsx             # Overview page (/)
│   │   ├── diseases/
│   │   │   └── page.tsx         # Disease analysis (/diseases)
│   │   ├── explorer/
│   │   │   └── page.tsx         # Data explorer (/explorer)
│   │   ├── reports/
│   │   │   ├── page.tsx         # Report list (/reports)
│   │   │   └── [uuid]/
│   │   │       └── page.tsx     # Report detail (/reports/:uuid)
│   │   ├── tasks/
│   │   │   └── page.tsx         # Task monitor (/tasks)
│   │   └── quality/
│   │       └── page.tsx         # Data quality (/quality)
│   ├── components/
│   │   ├── ui/                  # shadcn/ui components
│   │   ├── charts/
│   │   │   ├── BarChart.tsx
│   │   │   ├── LineChart.tsx
│   │   │   ├── AreaChart.tsx
│   │   │   └── PieChart.tsx
│   │   ├── layout/
│   │   │   ├── Sidebar.tsx
│   │   │   ├── Header.tsx
│   │   │   └── CountrySelector.tsx
│   │   ├── KPICard.tsx
│   │   ├── DataTable.tsx        # TanStack Table
│   │   ├── TaskCard.tsx
│   │   └── ReportViewer.tsx     # Markdown 渲染
│   ├── lib/
│   │   ├── api.ts               # fetch wrapper (base URL, error handling)
│   │   ├── hooks/
│   │   │   ├── useOverview.ts   # TanStack Query hooks
│   │   │   ├── useDiseases.ts
│   │   │   ├── useReports.ts
│   │   │   ├── useTasks.ts
│   │   │   └── useTaskWebSocket.ts
│   │   └── utils.ts
│   ├── stores/
│   │   └── app-store.ts         # Zustand: language, country, theme
│   └── i18n/
│       ├── en.json
│       └── zh.json
├── public/
├── tailwind.config.ts
├── next.config.ts
└── package.json
```

#### 1.3 核心依赖

```json
{
  "dependencies": {
    "next": "^15.0.0",
    "react": "^19.0.0",
    "@tanstack/react-query": "^5.0.0",
    "@tanstack/react-table": "^8.0.0",
    "echarts": "^5.5.0",
    "echarts-for-react": "^3.0.0",
    "zustand": "^5.0.0",
    "next-intl": "^4.0.0",
    "react-markdown": "^9.0.0",
    "tailwindcss": "^4.0.0",
    "lucide-react": "^0.400.0",
    "date-fns": "^4.0.0"
  }
}
```

#### 1.4 核心基础组件

**API Client (`lib/api.ts`)**
```typescript
const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1';

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  });
  if (!res.ok) throw new ApiError(res.status, await res.text());
  return res.json();
}
```

**TanStack Query Hooks 模式**
```typescript
// hooks/useOverview.ts
export function useOverviewSummary(countryId: number) {
  return useQuery({
    queryKey: ['overview', 'summary', countryId],
    queryFn: () => apiFetch(`/overview/summary?country_id=${countryId}`),
    staleTime: 5 * 60 * 1000,  // 5分钟内不重新请求
    refetchOnWindowFocus: false,
  });
}
```

---

### Phase 2: 逐页迁移（5-7天）

按以下优先级逐页迁移，每完成一页即可上线：

#### 2.1 Overview 页面（1天）

| Streamlit 原实现 | Next.js 新实现 |
|---|---|
| 4 个 `st.metric` + 5 次串行 SQL | 1 个 `useOverviewSummary` hook, 4 个 `<KPICard>` |
| `plot_top_diseases()` Plotly bar | `<BarChart>` ECharts，客户端渲染 |
| `plot_trend_chart()` Plotly line | `<LineChart>` ECharts，支持 zoom/brush |
| 切换时间范围 → 全页重跑 | 切换时间范围 → 仅图表组件 refetch |

**性能收益**:
- 5 次串行查询 → 1 次 API 调用（后端合并）
- 全页重跑 → 仅图表局部更新
- Plotly 3.5MB → ECharts tree-shaken ~300KB

#### 2.2 Disease Analysis 页面（1.5天）

| 功能 | 实现方案 |
|---|---|
| 疾病选择器 | `<Select>` + `useDiseases()` hook |
| 单疾病分析 (KPI + 3 tab charts) | `<Tabs>` + 3 个独立 `<Chart>` 组件 |
| 疾病对比模式 | `<MultiSelect>` + `useCompare()` hook |
| 原始数据表 + 筛选 | `<DataTable>` (TanStack Table) 服务端分页 |
| CSV 下载 | 浏览器端 Blob 下载 or API 流式响应 |

#### 2.3 Task Monitor 页面（1.5天）

| 功能 | 实现方案 |
|---|---|
| 任务列表 | `<DataTable>` + `useTasks()` hook |
| 实时状态更新 | **WebSocket** 替代轮询，`useTaskWebSocket()` |
| 任务详情展开 | `<Accordion>` + `useTaskDetail(uuid)` |
| Workbook 日志 | 虚拟滚动列表 (`@tanstack/virtual`) |
| 进度条 | `<Progress>` shadcn 组件 |

**WebSocket 实现**:
```typescript
// hooks/useTaskWebSocket.ts
export function useTaskWebSocket() {
  const queryClient = useQueryClient();
  
  useEffect(() => {
    const ws = new WebSocket(`${WS_BASE}/tasks/ws`);
    ws.onmessage = (event) => {
      const update = JSON.parse(event.data);
      // 增量更新 TanStack Query 缓存，无需 refetch
      queryClient.setQueryData(['tasks'], (old) => applyUpdate(old, update));
    };
    return () => ws.close();
  }, []);
}
```

#### 2.4 Report Monitor 页面（1.5天）

| 功能 | 实现方案 |
|---|---|
| 报告列表 | `<DataTable>` + 状态 badge |
| 报告详情 | 独立路由 `/reports/:uuid` |
| Section 内容 | `react-markdown` 渲染 Markdown |
| AI 对话历史 | `<ChatView>` 自定义组件 (analyst/writer/reviewer 分色) |
| 质量评分 | JSON → `<ScoreCard>` 可视化 |

#### 2.5 Data Explorer 页面（1天）

| 功能 | 实现方案 |
|---|---|
| 快捷查询模板 | 预定义 template 列表，API 端安全执行 |
| 时间完整性检查 | 独立 API + `<HeatmapChart>` 缺失月份可视化 |
| 表浏览器 | `<DataTable>` 服务端分页 |

#### 2.6 Data Quality 页面（0.5天）

| 功能 | 实现方案 |
|---|---|
| 基础统计 | `<KPICard>` 复用 |
| 零值检查 | `<ProgressBar>` + 百分比 |
| 时间缺口 | `<BarChart>` gap 可视化 |
| 数据源分布 | `<PieChart>` ECharts |

---

### Phase 3: 通用能力完善（2-3天）

#### 3.1 国际化 (i18n)
- 将现有 `i18n.py` 的 dict 导出为 `en.json` / `zh.json`
- 使用 `next-intl` 的 `useTranslations()` hook
- URL 结构: `/en/...` 和 `/zh/...`（或 cookie-based）

#### 3.2 响应式布局
- Sidebar → 移动端变为 bottom nav 或 hamburger menu
- 图表自适应容器宽度（ECharts `resize` 事件）
- DataTable 移动端横向滚动

#### 3.3 深色模式
- Tailwind `dark:` 类 + `next-themes`
- ECharts 深色主题

#### 3.4 Loading & Error 状态
- Next.js `loading.tsx` / `error.tsx` 约定式文件
- `<Skeleton>` 占位组件（shadcn/ui 内置）
- TanStack Query 的 `isLoading`, `isError` 状态

---

### Phase 4: 部署与切换（1-2天）

#### 4.1 Docker 化

```yaml
# docker-compose.yml 新增
services:
  api:
    build:
      context: .
      dockerfile: docker/Dockerfile.api
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql+asyncpg://globalid:globalid_dev_password@db:5432/globalid
    depends_on:
      - db

  dashboard:
    build:
      context: ./dashboard
      dockerfile: Dockerfile
    ports:
      - "3000:3000"
    environment:
      - NEXT_PUBLIC_API_URL=http://api:8000/api/v1
    depends_on:
      - api
```

#### 4.2 Nginx 反向代理

```nginx
server {
    listen 80;
    
    location / {
        proxy_pass http://dashboard:3000;
    }
    
    location /api/ {
        proxy_pass http://api:8000/api/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";  # WebSocket 支持
    }
}
```

#### 4.3 渐进式切换策略

```
第1周: Streamlit (:8501) 和 Next.js (:3000) 并行运行
第2周: 默认流量切到 Next.js，Streamlit 作为回退
第3周: 下线 Streamlit，删除 src/dashboard/
```

---

## 性能对比预估

| 指标 | Streamlit (现状) | Next.js + FastAPI (预期) |
|------|---|---|
| 首次加载 | ~3-5s (执行完整 Python 脚本) | ~0.5-1s (SSR + code splitting) |
| 切换筛选条件 | ~1-3s (全页重跑 + 多次 SQL) | ~100-300ms (局部 refetch) |
| 图表库体积 | ~3.5MB (Plotly) | ~300KB (ECharts tree-shaken) |
| 并发用户 | ~5-10 (Streamlit 单线程 per session) | ~100+ (无状态 API) |
| 实时更新 | 5s 轮询 | WebSocket 即时推送 |
| 移动端 | 基本不可用 | 完全响应式 |

---

## 实施时间线

```
Week 1 (Day 1-5):
  ├── Day 1-2: Phase 0 — FastAPI 路由 + Pydantic schemas
  ├── Day 3:   Phase 0 — WebSocket + 缓存 + 测试
  ├── Day 4:   Phase 1 — Next.js 初始化 + 基础组件
  └── Day 5:   Phase 2.1 — Overview 页面迁移

Week 2 (Day 6-10):
  ├── Day 6-7:  Phase 2.2 — Disease Analysis 迁移
  ├── Day 8:    Phase 2.3 — Task Monitor 迁移
  ├── Day 9:    Phase 2.4 — Report Monitor 迁移
  └── Day 10:   Phase 2.5+2.6 — Explorer + Quality

Week 3 (Day 11-14):
  ├── Day 11-12: Phase 3 — i18n, 深色模式, 响应式
  ├── Day 13:    Phase 4 — Docker 化 + Nginx
  └── Day 14:    集成测试 + 切换上线
```

---

## 风险与应对

| 风险 | 影响 | 应对策略 |
|------|------|----------|
| ECharts 与 Plotly 图表效果差异 | 中 | 提前对比两者 API，复杂图表用 ECharts option 自定义 |
| Report Markdown 渲染差异 | 低 | `react-markdown` + `remark-gfm` 完善支持 |
| WebSocket 连接断开 | 中 | 自动重连 + 降级轮询 fallback |
| SQL 注入修复改变查询结果 | 低 | 参数化迁移前后用测试用例对比结果 |
| 开发周期超出预期 | 中 | 按页面优先级交付，Overview 先上线即可使用 |

---

## 下一步

建议从 **Phase 0 (FastAPI API 层)** 开始实施，因为：
1. 后端到位后，前端可以并行开发
2. API 独立于 UI，未来不管用什么前端框架都可复用
3. 同时修复了现有的 SQL 注入安全隐患
4. Streamlit dashboard 可以在迁移期间继续使用

准备好后告诉我，我将从 `dashboard/api/` 开始编写第一批 API 端点。
