import copy
import json
from datetime import date, timedelta
from pathlib import Path
from src.data.crawlers.nl_weekly import parse_virological_csv
from src.data.crawlers.my_who_flunet import detection_fields, parse_records
from src.services.ingestion.countries.th_tableau import closed_weeks
from src.data.crawlers.th_dds import closed_single_weeks
root=Path('/home/likangguo/globalID/globalID2')
raw=(root/'tests/fixtures/ingestion/NL/virological_weekly.csv').read_bytes()
a,_=parse_virological_csv(raw,as_of=date(2026,11,16))
b,_=parse_virological_csv(raw.replace(b'Influenza A virus;65',b'Influenza A virus;'),as_of=date(2026,11,16))
key=lambda r:(r['Date'],r['SourceDiseaseCode'],r['Dimensions'])
before=next(r for r in a if r['RawDiseaseLabel']=='Influenza A virus' and r['SourceYearWeek']=='202645')
assert before['Cases']=='65'
assert key(before) not in {key(r) for r in b}
state={key(r):r for r in a};state.update({key(r):r for r in b})
assert state[key(before)]['Cases']=='65'
print('NL numeric -> blank: incoming identity omitted; upsert projection retains stale value',state[key(before)]['Cases'])
my=root/'tests/fixtures/ingestion/MY'
records=json.loads((my/'who_flunet_sample.json').read_bytes())['value']
fields=detection_fields(json.loads((my/'who_flunet_dictionary.json').read_bytes())['value'])
a,_=parse_records([(r,{}) for r in records],fields=fields,as_of=date(2026,11,16))
changed=copy.deepcopy(records); target=next(r for r in changed if r.get('INF_A') is not None);target['INF_A']=None
b,_=parse_records([(r,{}) for r in changed],fields=fields,as_of=date(2026,11,16))
print('MY FluNet numeric -> null:',len(a)-len(b),'previously numeric identity omitted')
calendar={w:(date(2026,1,5)+timedelta(weeks=w-1),date(2026,1,11)+timedelta(weeks=w-1)) for w in range(1,47)}
print('TH Tableau run after outage: offered weeks=1..45, automatic selected=',closed_weeks(calendar,as_of=date(2026,11,16)))
print('TH PDF run after outage: offered weeks=43,44,45, automatic selected=',closed_single_weeks(b'<select><option value="43-43">43</option><option value="44-44">44</option><option value="45-45">45</option></select>',year=2026,as_of=date(2026,11,16),calendar=calendar))
