from src.generation.home_summary import surveillance_home_summary, situation_home_summary


def test_home_summary_keeps_counts_and_only_visible_country_rows():
    countries = [{'code':f'C{i}', 'record_count':i+1, 'data_available':True, 'name_en':str(i),
        'date_range':{'end':f'2026-11-{i+1:02}'}, 'source_info':{'sources':[{'label':'Shared authority','url':'https://example.test','cadence':'weekly'}]},
        'unused_details':['large']*100} for i in range(10)]
    countries += [{'code':'C0-1','record_count':1}, {'code':'empty','data_available':False,'record_count':999}]
    result = surveillance_home_summary({'countries':countries,'diseases':['large']*100,'total_diseases':239})
    assert (result['national_count'],result['subdivision_count'],result['official_source_count']) == (10,1,1)
    assert len(result['recent_countries'])==3 and len(result['featured_countries'])==6
    assert result['recent_countries'][0]['code']=='C9'
    assert 'countries' not in result and 'diseases' not in result
    assert 'unused_details' not in result['featured_countries'][0]


def test_situation_summary_preserves_window_and_deduplicates_places():
    point={'alert_id':'a','outbreak_id':'o','country_code':'JP','disease_id':'d038','admin_area':'Tokyo','published_date':'2026-11-01','status':'active'}
    result=situation_home_summary({'schema_version':'gids.situation_alerts.v1','as_of':'2026-11-12','active_outbreaks':4,'sources_ok':2,'points':[
        point,{**point,'alert_id':'b','admin_area':'Ｔｏｋｙｏ'},
        {**point,'alert_id':'c','published_date':'2026-10-01'},
        {**point,'alert_id':'d','published_date':'2026-11-13'},
        {**point,'alert_id':'e','published_date':'2026-10-13'}]})
    assert result['recent_places']==2
    assert result['active_outbreaks']==4 and result['sources_ok']==2
    assert 'points' not in result
