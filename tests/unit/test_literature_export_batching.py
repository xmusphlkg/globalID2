import pytest

from src.domain import LiteratureDiseaseLink
from src.generation.site_data_literature import _load_article_links_in_batches


@pytest.mark.asyncio
async def test_export_link_queries_batch_large_article_sets():
    class Result:
        def __init__(self, values):
            self.values = values

        def scalars(self):
            return self

        def all(self):
            return self.values

    class Session:
        def __init__(self):
            self.batches = []

        async def execute(self, statement):
            params = statement.compile().params
            batch = next(iter(params.values()))
            self.batches.append(batch)
            return Result(batch)

    session = Session()
    ids = [f'lit_{i}' for i in range(20001)]
    assert await _load_article_links_in_batches(session, LiteratureDiseaseLink, ids) == ids
    assert [len(batch) for batch in session.batches] == [10000, 10000, 1]
    assert await _load_article_links_in_batches(session, LiteratureDiseaseLink, []) == []
