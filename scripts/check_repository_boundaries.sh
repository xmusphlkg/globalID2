#!/usr/bin/env bash

set -euo pipefail

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$repo_root" ]]; then
  echo "Not inside a Git repository." >&2
  exit 1
fi

cd "$repo_root"

generated_paths=(
  "astro-site/src/data/about.json"
  "astro-site/src/data/countries"
  "astro-site/src/data/disease-knowledge"
  "astro-site/src/data/disease-ontology.json"
  "astro-site/src/data/diseases"
  "astro-site/src/data/downloads.json"
  "astro-site/src/data/meta.json"
  "astro-site/src/data/reports"
  "astro-site/src/data/situation"
  "data/current"
  "data/raw"
  "external-data/globalID2_data_download"
)

ignore_probes=(
  "astro-site/src/data/about.json"
  "astro-site/src/data/countries/.globalid-repository-boundary-probe.json"
  "astro-site/src/data/disease-knowledge/.globalid-repository-boundary-probe.json"
  "astro-site/src/data/disease-ontology.json"
  "astro-site/src/data/diseases/.globalid-repository-boundary-probe.json"
  "astro-site/src/data/downloads.json"
  "astro-site/src/data/meta.json"
  "astro-site/src/data/reports/.globalid-repository-boundary-probe.json"
  "astro-site/src/data/situation/.globalid-repository-boundary-probe.json"
  "data/current/.globalid-repository-boundary-probe"
  "data/raw/.globalid-repository-boundary-probe"
  "external-data/globalID2_data_download/.globalid-repository-boundary-probe"
)

tracked="$(git ls-files -- "${generated_paths[@]}")"
if [[ -n "$tracked" ]]; then
  echo "Generated data must not be tracked by the code repository:" >&2
  printf '%s\n' "$tracked" >&2
  exit 1
fi

for probe in "${ignore_probes[@]}"; do
  if ! git check-ignore -q -- "$probe"; then
    echo "Generated path is not protected by .gitignore: $probe" >&2
    exit 1
  fi
done

python3 scripts/check_repository_size.py

echo "Repository boundaries OK: generated data is ignored and new large artifacts are blocked."
