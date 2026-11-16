import json
from pathlib import Path
from src.data.crawlers.pe import parse_bulletin_pdf
base=Path('tests/fixtures/ingestion/PE');p=json.loads((base/'2026w13_provenance.json').read_text())
def facts(rows):
 results=[]
 for row in rows:
  row=dict(row)
  row.pop('RawSHA256',None)
  dims=json.loads(row['Dimensions'])
  for key in ('source_page','source_report_sha256'):dims.pop(key,None)
  row['Dimensions']=dims
  results.append(json.dumps(row,sort_keys=True,default=str))
 return sorted(results)
a,aa=parse_bulletin_pdf(Path('data/acceptance-fixtures-original/20261116/2026w13_cover_summary.pdf').read_bytes(),source_url=p['source_url'],reference=p['reference'])
b,bb=parse_bulletin_pdf((base/'2026w13_cover_summary.pdf').read_bytes(),source_url=p['source_url'],reference=p['reference'])
assert facts(a)==facts(b)
print(json.dumps({'year':2026,'week':13,'rows':len(a),'all_semantic_fields_unchanged':True,'ignored_artifact_fields':['RawSHA256','Dimensions.source_page','Dimensions.source_report_sha256']}))
