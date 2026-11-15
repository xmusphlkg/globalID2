# Historical input preparation

[cn_merge_diseases_data.py](cn_merge_diseases_data.py) merges historical CN CSV
files and handles the special 2013-01/2013-02 inputs. It writes a local CSV;
it does not import the result into the database.

Run from the repository root. Defaults are relative to the working directory:

```bash
venv/bin/python scripts/imports/cn_merge_diseases_data.py --help
venv/bin/python scripts/imports/cn_merge_diseases_data.py \
  --src-dir data/history/cn --out data/history/cn/history_merged.csv
```

The output path is overwritten. Choose a separate path when comparing a new
merge against a retained baseline. Review the file before an explicit
[historical import](../../docs/operations/DATABASE_REBUILD.md).
Country import commands with existing callers keep their root entrypoints and
are listed in the [script index](../README.md#historical-imports-and-population).
