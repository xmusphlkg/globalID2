from datetime import date
from src.services.situation_notices.pipeline import cluster_alerts
from src.services.situation_notices.export import latest_per_place
base = dict(source_id='who_don', disease_id='D014', disease_label='Avian influenza', country_code='KE', admin_area=None,status='ongoing',host_type='animal',count_unit='animals',cases=2,deaths=None,quote='Two cases in wild birds.')
raw = [dict(base,alert_id='old',notice_id='n1',published_date=date(2026,11,1),host_species='Wild birds'),dict(base,alert_id='new',notice_id='n2',published_date=date(2026,11,2),host_species='wild birds')]
clusters=cluster_alerts(raw,as_of=date(2026,11,16))
assert len(clusters)==1,clusters
memberships={a:c['outbreak_id'] for c in clusters for a in c['alert_ids']}
records=[dict(r,outbreak_id=memberships[r['alert_id']],published_date=r['published_date'].isoformat()) for r in raw]
points=latest_per_place(records)
print('clusters',len(clusters))
print('map_points',len(points))
print([(p['alert_id'],p['host_species'],p['place_alert_count']) for p in points])
assert len(points)==2
from src.generation.home_summary import situation_home_summary
print('homepage_recent_places',situation_home_summary({'as_of':'2026-11-16','points':points})['recent_places'])
