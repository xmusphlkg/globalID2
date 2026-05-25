# Dashboard Features

Feature folders own product workflows. Route files under `app/` should stay as
thin wrappers that import a feature `view.tsx`.

Each feature can expose:

- `view.tsx` for page composition
- `api.ts` for queries and mutations
- `components/` for feature-specific UI
- `types.ts` for feature view models

Shared primitives live under `shared/`.
