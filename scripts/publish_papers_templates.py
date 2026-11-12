#!/usr/bin/env python3
"""Stage immutable paper templates; optionally atomically activate the pointer.

Old static paper objects are never deleted. Do not activate until the Worker
has passed its canary and rollback checks on the public hostname.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.publish_papers_r2 import _config_from_env
from src.services.data_release.papers_r2 import R2S3Client
from src.services.data_release.papers_templates_publish import publish_candidate


def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--candidate',type=Path,required=True)
 p.add_argument('--workers',type=int,default=32)
 p.add_argument('--max-total-bytes',type=int,default=8_000_000_000)
 p.add_argument('--activate',action='store_true')
 a=p.parse_args();client=R2S3Client(_config_from_env())
 try: print(json.dumps(publish_candidate(a.candidate,client,workers=a.workers,activate=a.activate,max_total_bytes=a.max_total_bytes),ensure_ascii=False))
 finally:client.close()
if __name__=='__main__':main()
