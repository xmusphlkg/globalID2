from pathlib import Path
import json,zipfile
from src.data.crawlers.pe import parse_bulletin_pdf,discover_reports
root=Path.cwd();before=json.loads(Path('/tmp/globalid-fixes-20261116/rc6-fixture-before.json').read_text())
keys=('Date','PeriodStart','PeriodEnd','TimeBasis','SourceReportYear','SourceDiseaseCode','Cases','Measure','ReportingBasis','Dimensions','DefinitionVersion','DiseaseCode','GeographyKey')
results=[]
with zipfile.ZipFile(root/'tests/fixtures/ingestion/PE/official_bulletins.zip') as z:
 refs=discover_reports(z.read('full_listing.html'))
 for year,week in [(1993,52),(2013,52),(2019,51)]:
  ref=next(r for r in refs if (r['year'],r['week'])==(year,week))
  rows,audit=parse_bulletin_pdf(z.read(f'{year}w{week}.pdf'),source_url=ref['url'],reference=ref)
  def facts(items):
   result=[]
   for row in items:
    row=dict(row);dims=json.loads(row['Dimensions'])
    for key in ('source_page','source_report_sha256'):dims.pop(key,None)
    row['Dimensions']=json.dumps(dims,sort_keys=True)
    result.append(tuple(str(row.get(k)) for k in keys))
   return sorted(result)
  assert facts(before[str(year)]['rows'])==facts(rows),year
  results.append({'year':year,'rows':len(rows),'semantic_facts_unchanged':True,'fixture_pages':audit['pages']})
print(json.dumps(results,indent=2))
