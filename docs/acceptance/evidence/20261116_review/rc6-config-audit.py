"""Read-only config and candidate blob audit; run from the repository root."""
import json
import subprocess
from pathlib import Path
import sys
sys.path.insert(0, str(Path.cwd()))
from scripts.check_repository_size import TrackedBlob, find_violations
from src.ontology.disease import DiseaseOntology, load_disease_ontology
raw = DiseaseOntology.from_file()
expanded = load_disease_ontology()
print(json.dumps({'ontology_valid': True, 'concepts': len(expanded.concept_ids),
                  'groups': len(expanded.group_ids), 'raw_series': len(raw.series_ids),
                  'expanded_series': len(expanded.series_ids)}))
source = Path(__file__).parent / 'workspace-before.json'
if not source.is_file():
    raise SystemExit('workspace-before.json must be beside this audit script')
paths = json.loads(source.read_text())['files']
blobs = [TrackedBlob(row['path'], subprocess.check_output(
    ['git', 'hash-object', '--', row['path']], text=True).strip(), Path(row['path']).stat().st_size)
    for row in paths if Path(row['path']).is_file()]
violations = find_violations(blobs, allowlist=set())
print(json.dumps({'candidate_blob_limit': 5*1024*1024,
                  'violations': [{'path': v.blob.path, 'bytes': v.blob.size, 'reasons': v.reasons}
                                 for v in violations]}, ensure_ascii=False, indent=2))
for row in paths:
    path = Path(row['path'])
    if path.suffix == '.json' and path.is_file():
        json.loads(path.read_text())
print('All still-present initial candidate JSON documents parse successfully')
# Zero confirms execution completed, not that every candidate passed admission.
