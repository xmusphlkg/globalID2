# External data repositories

This directory is the stable local home for Git repositories that store generated
or archived data outside the GlobalID source-code history.

`globalID2_data_download/` is created and maintained automatically by:

```bash
make site-download-sync
```

It is a nested, ignored Git checkout of the dedicated public download repository.
The publisher clones it once, updates it with `fetch`/`pull --ff-only`, copies only
changed time partitions, removes retired partitions, validates the staged tree,
and pushes one intentional commit. Do not add this nested repository or its data
files to the parent GlobalID repository.
