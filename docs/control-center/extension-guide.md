# Extension Guide

Extensions follow one vertical slice. Do not add SQL, Pydantic models, process checks, or orchestration to a router.

## 1. Register navigation

Add an English route node to `dashboard/src/shared/navigation/route-registry.ts` under one of the five workspaces. Use a canonical URL and declare whether country context is required, optional, or absent. Add a permanent redirect for any replaced UI URL.

## 2. Add the backend slice

Recommended layout:

```text
src/control_plane/<feature>.py                 # use case + repository/query adapter
dashboard/api/schemas/<feature>.py             # Pydantic delivery contract
dashboard/api/routers/<feature>.py             # HTTP-only delivery
tests/unit/test_<feature>_use_case.py
tests/integration/test_<feature>_repository.py
```

Application template:

```python
class WidgetQueryRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list(self, query: WidgetQuery) -> list[Widget]:
        # Allowlisted filters/sorts and SQL live here.
        ...


class ListWidgets:
    def __init__(self, repository: WidgetQueryRepository) -> None:
        self.repository = repository

    async def execute(self, query: WidgetQuery) -> list[Widget]:
        return await self.repository.list(query)
```

Router template:

```python
@router.get("/widgets", response_model=DataResponse[list[WidgetOut]])
async def list_widgets(request: Request, db: AsyncSession = Depends(get_db)):
    rows = await ListWidgets(WidgetQueryRepository(db)).execute(...)
    return DataResponse(data=[WidgetOut.model_validate(row) for row in rows], meta=request_meta(request))
```

Use stable external identifiers. Use explicit filter/sort allowlists and `page`/`page_size`. Commands own a transaction boundary and publish a correlated event after commit. Long work returns `202 TaskReferenceOut`; configuration creation returns `201`.

## 3. Update and generate the contract

Include the router in `dashboard/api/main.py`, then run:

```bash
cd dashboard
npm run openapi:generate
```

Commit both `openapi.json` and `src/generated/api.d.ts`. CI should run `npm run openapi:check` and fail on drift.

## 4. Add the frontend feature

```text
dashboard/src/features/<workspace>/<feature>/api.ts
dashboard/src/features/<workspace>/<feature>/view.tsx
dashboard/src/app/<canonical-path>/page.tsx
```

Use `controlPlaneClient` and types from `generated/api`; do not restate response DTOs. React Query owns server state. Put country, filters, sorting, pagination, and selected resource IDs in the URL. Zustand is reserved for sidebar/local UI preferences.

Compose `WorkspacePage`, `PageHeader`, `Tabs`, `MetricStrip`, `ActionList`, `DataTable`, `FilterBar`, `StatusBadge`, `DetailDrawer`, dialogs, `FormField`, `Skeleton`, and Empty/Error states. Add shared primitives instead of copying buttons, tables, or status colors into a page.

## 5. Test the slice

- Unit-test use cases and repository filter/sort allowlists.
- Integration-test PostgreSQL/Redis behavior and migrations.
- Test the RFC 9457 error and success envelope contract.
- Add Vitest + Testing Library + MSW coverage for form, URL, empty, and error states.
- Add Playwright coverage at desktop, tablet, and mobile sizes.
- For asynchronous work, test concurrent claim, cancel, retry, failure recovery, heartbeat expiry, Redis interruption, and SSE resume.

Finish with `npm run lint`, `npm test`, `npm run build`, OpenAPI drift checks, Python tests, migration upgrade/downgrade, and the relevant E2E workflow.
