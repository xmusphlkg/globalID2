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
  "data/current"
  "data/raw"
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
  "data/current/.globalid-repository-boundary-probe"
  "data/raw/.globalid-repository-boundary-probe"
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

echo "Repository boundaries OK: generated data is ignored and untracked."
