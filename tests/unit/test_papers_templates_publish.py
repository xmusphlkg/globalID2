import gzip,hashlib
import pytest
from src.services.data_release.papers_templates_publish import publish_candidate
from src.services.data_release.papers_r2 import R2S3Error
from tests.unit.test_prepare_papers_templates import populate
from scripts.prepare_papers_templates import prepare

class Fake:
 def __init__(self):self.objects={};self.writes=[]
 def get_object(self,key):return self.objects.get(key)
 def list_objects(self):return {k:{'bytes':len(v),'etag':hashlib.md5(v,usedforsecurity=False).hexdigest()} for k,v in self.objects.items()}
 def put_object_conditional(self,key,body,*,previous,**kw):
  current=self.objects.get(key)
  if current!=previous:raise R2S3Error('precondition',status=412)
  self.objects[key]=body;self.writes.append(key)

def test_stage_reuses_objects_and_switches_pointer_last(tmp_path):
 src=tmp_path/'src';populate(src,'old'); candidate=tmp_path/'candidate';prepare(src,candidate)
 client=Fake();r=publish_candidate(candidate,client,workers=2)
 assert r['uploaded']>0 and not r['activated'] and 'paper-template-v1/current.json' not in client.objects
 r=publish_candidate(candidate,client,workers=2,activate=True)
 assert r['uploaded']==0 and r['activated'] and client.writes[-1]=='paper-template-v1/current.json'
 assert client.objects['paper-template-v1/current.json']==(candidate/'current.json').read_bytes()

def test_damaged_candidate_or_budget_fails_before_any_mutation(tmp_path):
 src=tmp_path/'src';populate(src,'old'); candidate=tmp_path/'candidate';prepare(src,candidate)
 client=Fake()
 with pytest.raises(R2S3Error,match='budget'):publish_candidate(candidate,client,max_total_bytes=1)
 assert not client.writes
 next((candidate/'records').iterdir()).write_text('{}')
 with pytest.raises(ValueError,match='checksum'):publish_candidate(candidate,client)
 assert not client.writes
