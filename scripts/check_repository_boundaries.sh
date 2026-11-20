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

ignored="$(git check-ignore -- "${ignore_probes[@]}")" || [[ $? -eq 1 ]]
missing_ignores=0
for probe in "${ignore_probes[@]}"; do
  if [[ $'\n'"$ignored"$'\n' != *$'\n'"$probe"$'\n'* ]]; then
    printf 'Generated path is not protected by .gitignore: %s\n' "$probe" >&2
    missing_ignores=1
  fi
done
if (( missing_ignores )); then
  exit 1
fi

python3 scripts/check_repository_size.py "$@"

echo "Repository boundaries OK: generated data is ignored and new large artifacts are blocked."
