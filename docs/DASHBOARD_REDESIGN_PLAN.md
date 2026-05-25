# Dashboard Redesign Plan

## Goal

Rebuild the management dashboard around stable product areas and replace
patch-driven pages with feature modules. Existing API behavior and user
workflows should remain available during the migration.

## Current Pain Points

- Route pages are too large and mix UI, form state, data fetching, and business
  rules.
- Similar pages exist under multiple routes, such as legacy top-level data
  paths and newer grouped paths.
- Shared UI is useful but still page-shaped; common workflows like filters,
  drawers, task status, source selection, and confirmation actions are repeated.
- Navigation is hand-authored separately from page ownership, breadcrumbs, and
  future permission boundaries.
- Frontend source/country metadata can drift from backend registries.

## Target Information Architecture

The dashboard should be organized by operational workflow rather than database
table names.

1. Operations
   - Source flow
   - Crawl tasks
   - Automation jobs
   - Worker/runtime health

2. Data
   - Overview dashboard
   - Diseases
   - Data quality
   - Explorer
   - Knowledge
   - Release

3. Reports
   - Generated reports
   - Report review/status

4. AI
   - Models
   - Agent runs
   - AI tasks
   - Disease audit
   - Interactions

5. Admin
   - Settings
   - Subscriptions
   - Notifications

## Target Frontend Structure

```text
dashboard/src/
  app/
    (workbench)/
      layout.tsx
      page.tsx
      operations/
      data/
      reports/
      ai/
      admin/
  features/
    operations/
      sources/
      tasks/
      automation/
      runtime/
    data/
      overview/
      diseases/
      quality/
      explorer/
      knowledge/
      release/
    reports/
    ai/
    admin/
  shared/
    api/
    config/
    i18n/
    layout/
    navigation/
    query/
    ui/
    utils/
```

Each feature owns:

- `api.ts`: typed queries and mutations for that feature.
- `components/`: domain components, not page components.
- `types.ts`: local DTO/view model types.
- `routes.ts`: feature route metadata for navigation and breadcrumbs.
- `view.tsx`: page-level composition without low-level implementation details.

## Data Boundary

Use React Query for all server state. Zustand should keep only durable UI state:

- language
- active country
- sidebar state
- user display preferences

Filters, pagination, selected task IDs, and tab state should move to URL search
params so pages are shareable and refresh-safe.

Country/source options must come from backend config endpoints such as
`/sources/config`. Frontend fallback labels can stay, but should only be a
display fallback, not the source of truth.

## Navigation Boundary

Replace hand-coded sidebar groups with a route registry:

```ts
export interface RouteNode {
  id: string;
  href: string;
  labelKey: string;
  icon: LucideIcon;
  section: "operations" | "data" | "reports" | "ai" | "admin";
  status?: "stable" | "beta" | "hidden";
}
```

The same registry should power:

- sidebar
- breadcrumbs
- command/search palette later
- route redirects for legacy URLs
- future role/permission filtering

## UI System

Keep the dashboard utilitarian and dense. Build shared primitives for repeated
workflows:

- `PageFrame`
- `ModuleTabs`
- `EntityToolbar`
- `FilterStateBar`
- `DataGrid`
- `MetricStrip`
- `StatusPill`
- `ActionMenu`
- `ConfirmDialog`
- `FormDrawer`
- `RuntimeBanner`
- `TaskTimeline`

Avoid nested cards and marketing-style layouts. Pages should use full-width
workflow bands and compact panels.

## Migration Strategy

Use a strangler migration: build the new shell and feature modules beside the
old pages, then redirect or wrap legacy routes one by one.

### Phase 1: Foundation

- Add route registry and new app shell.
- Move shared UI into `shared/ui`.
- Add URL search-param helpers.
- Add feature-level API modules that wrap existing hooks.
- Keep all current routes working.

### Phase 2: Operations Module

Rebuild the most patch-heavy area first:

- Source flow
- Crawl tasks
- Automation jobs
- Worker/runtime status

This creates the template for future modules because it exercises tables,
drawers, forms, live task state, filters, and backend registries.

### Phase 3: Data Module

Rebuild:

- data overview
- diseases
- quality
- explorer
- knowledge
- release

Unify chart and table behavior, and standardize country-aware empty states.

### Phase 4: AI, Reports, Admin

Move remaining pages into feature modules and remove duplicate legacy routes.

### Phase 5: Hardening

- Add smoke tests for route rendering.
- Add tests for route registry and source option derivation.
- Run TypeScript checks in CI.
- Remove dead hooks, backup pages, and old route wrappers.

## First Implementation Slice

Start with Operations because it currently carries the highest maintenance
cost. The first slice should introduce the new foundation without changing
backend contracts:

1. Create `shared/navigation/route-registry.ts`.
2. Create `shared/layout/AppShell.tsx` and adapt sidebar/topbar to route nodes.
3. Create `features/operations/sources/api.ts` from existing source hooks.
4. Rebuild `/sources/flow`, `/sources/tasks`, and `/sources/automation` as thin
   wrappers over `features/operations`.
5. Keep old URLs active until all internal links are migrated.

## Success Criteria

- Existing user-visible workflows still work.
- New country/source support requires backend registry changes only, not page
  edits.
- Each route page is mostly composition and stays small.
- Feature code can be moved, tested, or deleted independently.
- Future modules can register navigation, breadcrumbs, and permissions without
  editing layout components.

## Implementation Status

Completed in the first restructuring pass:

- Main dashboard pages were moved from `app/**/page.tsx` into `features/**/view.tsx`.
- Existing URLs remain active through thin `app` wrappers.
- `shared/navigation/route-registry.ts` now owns sidebar/home navigation metadata.
- `shared/layout/AppShell.tsx` is the root dashboard shell.
- `shared/ui`, `shared/api`, `shared/config`, `shared/i18n`, `shared/query`, and
  `shared/utils` provide stable import boundaries.
- Feature API facades were added for operations, data, reports, AI, and admin.
- A visual redesign pass introduced the dark operational sidebar, route-aware
  top bar, refreshed global palette, shared panel/control classes, denser data
  tables, stronger metric tiles, and a new workbench home view.
- Common panel styling was rolled through data, operations, AI, reports, and
  admin feature pages so the dashboard reads as one product surface.
- Navigation was regrouped by workflow: collection/tasks, data assets, AI
  production, publishing, and system configuration.
- Route metadata now declares `countryScope` (`required`, `optional`, `none`),
  letting the header show the country selector only on pages where it affects
  the current workflow.
- Frontend type check and production build both pass.
