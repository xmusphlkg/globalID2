from src.services.data_release.papers_retention import retain_remote_dependencies


def test_fresh_build_keeps_one_asset_generation_and_bounded_shards():
    shard = 'research/catalogue/diseases/d038/2026010-' + 'a' * 16 + '.json'
    obsolete = 'research/catalogue/studies/model-' + 'b' * 16 + '.json'
    paths = ['_astro/current.js', '_astro/older.js', shard, obsolete, 'unrelated.json']
    previous = {'files': [{'key': p, 'path': p} for p in paths],
                'asset_generations': [['_astro/current.js'], ['_astro/older.js']]}
    local = {'files': [{'key': '_astro/new.js', 'path': '_astro/new.js'}],
             'asset_generations': [['_astro/new.js']]}
    result = retain_remote_dependencies(local, previous, dict.fromkeys(paths, {}), now=1000)
    assert {p['key'] for p in result['files']} == {'_astro/new.js', '_astro/current.js', shard}
    assert result['library_shard_retirements'][shard] == 1000
    again = retain_remote_dependencies(local, result, dict.fromkeys(paths, {}), now=1100)
    assert again['library_shard_retirements'][shard] == 1000
    expired = retain_remote_dependencies(local, result, dict.fromkeys(paths, {}), now=1000 + 48 * 3600)
    assert shard not in {p['key'] for p in expired['files']}
