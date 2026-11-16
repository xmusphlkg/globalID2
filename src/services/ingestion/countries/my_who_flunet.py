"""WHO FluNet MYS full-history page and dictionary capture/replay."""
from datetime import datetime,timezone
from src.data.crawlers.my_who_flunet import DEFAULT_SCOPE,DATA_URL,DICTIONARY_URL,MYWHOFluNetCrawler,detection_fields,parse_records
from src.data.processors.my_who_flunet import MYWHOFluNetUpdater
from .my_who_common import capture_pages,replay_pages


async def collect(writer,*,source=DEFAULT_SCOPE,**options):
    MYWHOFluNetUpdater.normalize_source(source)
    crawler=MYWHOFluNetCrawler();retrieved=datetime.now(timezone.utc).isoformat()
    try:
        await capture_pages(writer,crawler,url=DICTIONARY_URL,dataset='flunet_dictionary',retrieved_at=retrieved)
        await capture_pages(writer,crawler,url=DATA_URL,dataset='flunet_mys',retrieved_at=retrieved)
        return writer.artifacts
    finally:crawler.session.close()


def organize(artifacts,*,updater,source=DEFAULT_SCOPE,**options):
    updater.normalize_source(source)
    dictionary=replay_pages(artifacts,url=DICTIONARY_URL,dataset='flunet_dictionary')
    fields=detection_fields([item[0] for item in dictionary])
    records=[(row,{'DownloadURL':artifact.source_url,'RawSHA256':artifact.sha256,'RetrievedAt':artifact.metadata_['retrieved_at'],'SourceRecordIndex':index,'SourceDictionarySHA256':dictionary[0][1].sha256}) for row,artifact,index in replay_pages(artifacts,url=DATA_URL,dataset='flunet_mys')]
    captured_as_of=datetime.fromisoformat(dictionary[0][1].metadata_['retrieved_at'].replace('Z','+00:00')).date()
    rows,audit=parse_records(records,fields=fields,as_of=captured_as_of)
    result=updater.prepare_rows(rows,source=source,**options);result.source_audit.update(audit)
    return result
