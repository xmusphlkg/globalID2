"""Compact existing PE fixtures; originals are retained in ignored local storage.
Requires pypdf 7.1.0 only as a one-off fixture maintenance tool.
"""
from pathlib import Path
from io import BytesIO
import hashlib,json,shutil,zipfile
from pypdf import PdfReader,PdfWriter
from pypdf.constants import ImageType
root=Path.cwd(); fixtures=root/'tests/fixtures/ingestion/PE'
backup=root/'data/acceptance-fixtures-original/20261116';backup.mkdir(parents=True,exist_ok=True)
archive=fixtures/'official_bulletins.zip';w13=fixtures/'2026w13_cover_summary.pdf'
for p in (archive,w13):
 target=backup/p.name
 if not target.exists():shutil.copy2(p,target)

def compact(payload,pages):
 reader=PdfReader(BytesIO(payload));writer=PdfWriter()
 for page in pages:writer.add_page(reader.pages[page-1])
 writer.remove_images(ImageType.XOBJECT_IMAGES | ImageType.INLINE_IMAGES)
 writer.compress_identical_objects(remove_duplicates=True,remove_unreferenced=True)
 out=BytesIO();writer.write(out);return out.getvalue()

summary={'tool':'pypdf 7.1.0','operation':'retain report cover and parsed national evidence pages; remove raster images, preserve vector text/tables',
         'original_archive_sha256':hashlib.sha256((backup/archive.name).read_bytes()).hexdigest(),'documents':{}}
selected={'2013w52.pdf':[1,22],'2019w51.pdf':[1,11]}
out=BytesIO()
with zipfile.ZipFile(backup/archive.name) as source, zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as target:
 for name in source.namelist():
  original=source.read(name);payload=compact(original,selected[name]) if name in selected else original
  info=zipfile.ZipInfo(name,date_time=(2026,11,16,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o644<<16
  target.writestr(info,payload,compresslevel=9)
  summary['documents'][name]={'original_sha256':hashlib.sha256(original).hexdigest(),'fixture_sha256':hashlib.sha256(payload).hexdigest(),'original_bytes':len(original),'fixture_bytes':len(payload),'original_pages':selected.get(name,'all')}
archive.write_bytes(out.getvalue())
original=(backup/w13.name).read_bytes();payload=compact(original,[1,2]);w13.write_bytes(payload)
provenance_path=fixtures/'2026w13_provenance.json';provenance=json.loads(provenance_path.read_text())
provenance.update({'fixture_sha256':hashlib.sha256(payload).hexdigest(),'fixture_transformation':'Raster images removed; cover and original page 23 vector text/tables retained unchanged.','original_fixture_sha256':hashlib.sha256(original).hexdigest()})
provenance_path.write_text(json.dumps(provenance,ensure_ascii=False,indent=2)+'\n')
summary['documents'][w13.name]={'original_sha256':hashlib.sha256(original).hexdigest(),'fixture_sha256':hashlib.sha256(payload).hexdigest(),'original_bytes':len(original),'fixture_bytes':len(payload),'original_pages':[1,23]}
(fixtures/'fixture_provenance.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'archive_before':(backup/archive.name).stat().st_size,'archive_after':archive.stat().st_size,'w13_before':len(original),'w13_after':len(payload)},indent=2))
