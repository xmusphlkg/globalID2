# Dashboard Module Structure

The dashboard has been reorganized into a modular structure for better maintainability and separation of concerns.

## Directory Structure

```
src/dashboard/
├── app.py                  # Main application entry point
├── i18n.py                 # Internationalization (English & Chinese)
├── styles.css              # Dashboard styles
│
├── common/                 # Shared components
│   ├── __init__.py
│   ├── data.py            # Database query utilities
│   └── ui.py              # Shared UI components (sidebar, etc.)
│
├── disease/                # Disease analysis module
│   ├── __init__.py
│   ├── data.py            # Disease-specific data queries
│   ├── plots.py           # Disease visualization charts
│   └── ui.py              # Disease analysis pages (future)
│
└── task/                   # Task management module
    ├── __init__.py
    └── ui.py              # Task management UI
```

## Module Responsibilities

### `app.py`
- Main Streamlit application entry point
- Page routing and layout
- Coordinates all modules

### `i18n.py`
- Translation strings for English and Chinese
- Shared across all modules

### `common/`
Common utilities shared across all dashboard modules.

**data.py:**
- Database connection management
- Generic query execution (`run_query`)
- Caching strategy

**ui.py:**
- Sidebar rendering (`render_sidebar`)
- Shared UI components
- Navigation logic

### `disease/`
Disease analysis and visualization module.

**data.py:**
- Disease-specific queries
- `get_disease_list()` - Fetch diseases for a country

**plots.py:**
- `plot_top_diseases()` - Top diseases bar charts
- `plot_trend_chart()` - Time series trends

**ui.py:** (Future)
- Disease overview page
- Disease comparison page
- Data quality dashboard

### `task/`
Task management module for AI and crawler tasks.

**ui.py:**
- `render_task_center()` - Main task management UI
- Task creation forms
- AI tasks, Crawler tasks, Categories tabs
- Task statistics and monitoring

## Usage Examples

### Importing Modules

```python
# In app.py or other modules
from src.dashboard.common import run_query, render_sidebar
from src.dashboard.disease import get_disease_list, plot_top_diseases
from src.dashboard.task import render_task_center
```

### Adding New Features

**Add a new disease page:**
1. Create function in `disease/ui.py`
2. Import and use in `app.py`

**Add new task types:**
1. Extend `task/ui.py` with new tab or section
2. Update i18n.py with new labels

**Add shared utilities:**
1. Add to `common/data.py` or `common/ui.py`
2. Export in `common/__init__.py`

## Migration Notes

The following files have been reorganized:

- `data.py` → Split into:
  - `common/data.py` (core query utilities)
  - `disease/data.py` (disease-specific queries)

- `plots.py` → `disease/plots.py`

- `ui.py` → `common/ui.py`

- `tasks_ui.py` → `task/ui.py`

Old files remain in place to avoid breaking existing code until full migration is complete.

## Running the Dashboard

```bash
./venv/bin/streamlit run src/dashboard/app.py
```

## Future Improvements

1. Move disease page logic from `app.py` to `disease/ui.py`
2. Add data quality module under `quality/`
3. Add export/reporting module under `export/`
4. Implement caching strategies per module
5. Add unit tests for each module
