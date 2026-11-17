from pathlib import Path
import datetime
import hashlib
import json
import re
import subprocess
import time

ROOT=Path('/home/likangguo/globalID/globalID2')
EVIDENCE=ROOT/'docs/acceptance/evidence/20261116_fixes'
TMP=Path('/tmp/globalid-fixes-20261116')
base_tests=re.findall(r'tests/unit/test_\w+\.py', (ROOT/'docs/acceptance/REVIEW_v0.20.0-rc.1_20261116.md').read_text().split('结果：')[0])
base_tests += ['tests/unit/test_th_bounded_refresh.py','tests/unit/test_nl_my_missing_revisions.py']
rc7_tests=['tests/unit/'+name+'.py' for name in ('test_priority_country_updates','test_remaining_surveillance_onboarding','test_remaining_who_annual','test_spf_overseas','test_my_who_sources','test_wpro_dengue','test_wpro_dengue_acceptance','test_cr_notifications','test_cr_notifications_acceptance')]
tests=list(dict.fromkeys(base_tests+rc7_tests))
assert len(base_tests)==26 and len(tests)==33,(len(base_tests),len(tests))
paths=set(json.loads((TMP/'batch-paths.json').read_text())['1'])
paths.update(json.loads((TMP/'rc7-start-manifest.json').read_text())['files'])
paths.update(tests)
for pattern in ('src/data/crawlers/*.py','src/data/processors/*.py','src/services/ingestion/countries/*.py','scripts/imports/*.py','scripts/*co*.py','configs/*.json'):
 paths.update(str(p.relative_to(ROOT)) for p in ROOT.glob(pattern))
paths.update(['src/services/ingestion/runner.py','src/services/ingestion/series_rows.py','src/services/ingestion/artifacts.py','src/data/storage/series_observation_store.py','src/services/ingestion/co_update_gate.py','src/core/country_library.py','src/core/remaining_country_sources.py','src/core/source_channels.py','src/core/source_scopes.py','src/services/crawl_service.py'])
paths=sorted(p for p in paths if (ROOT/p).is_file())

def manifest():
 return {'captured_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'files':{p:{'sha256':hashlib.sha256((ROOT/p).read_bytes()).hexdigest(),'mtime_ns':(ROOT/p).stat().st_mtime_ns} for p in paths}}

def write(name,value):
 text=json.dumps(value,ensure_ascii=False,indent=2)
 for folder in (TMP,EVIDENCE):
  (folder/name).write_text(text)

command=['venv/bin/python','-m','pytest','-q',*tests]
write('country-final-command.json',{'base_test_files':26,'rc7_test_files':9,'merged_test_files':len(tests),'command':command,'fingerprinted_files':len(paths)})
start=manifest();write('country-final-start-manifest.json',start)
print(json.dumps({'status':'started','tests':len(tests),'fingerprinted_files':len(paths),'head':start['head'],'captured_at':start['captured_at']}),flush=True)
began=time.monotonic()
log=TMP/'country-final-full.log'
with log.open('w') as out:
 result=subprocess.run(command,cwd=ROOT,stdout=out,stderr=subprocess.STDOUT)
end=manifest();end['changed_paths']=[p for p in paths if end['files'][p]['sha256']!=start['files'][p]['sha256']];write('country-final-end-manifest.json',end)
(EVIDENCE/log.name).write_bytes(log.read_bytes())
write('country-final-result.json',{'exit_code':result.returncode,'elapsed_seconds':round(time.monotonic()-began,2),'changed_paths':end['changed_paths'],'head_before':start['head'],'head_after':end['head'],'test_file_count':len(tests),'fingerprinted_file_count':len(paths)})
print(log.read_text()[-6000:],flush=True)
print(json.dumps({'exit_code':result.returncode,'elapsed_seconds':round(time.monotonic()-began,2),'changed_paths':end['changed_paths']}),flush=True)
raise SystemExit(result.returncode)
