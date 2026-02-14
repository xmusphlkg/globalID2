"""AI layout parser wrapper for remote layout-parsing API.

Usage:
  from src.data.parsers.ai_layout_parser import AiLayoutParser
  parser = AiLayoutParser(token=os.getenv('AI_LAYOUT_TOKEN'))
  result = parser.parse_file('path/to/image.jpg', out_dir='output')

The parser will call the remote API, save returned markdown/images to out_dir
and return a dict with parsed markdown text and metadata.
"""
from __future__ import annotations

import os
import base64
import mimetypes
import requests
from typing import Optional, Dict, Any


class AiLayoutParser:
    def __init__(self, token: Optional[str] = None, api_url: Optional[str] = None):
        # API URL and token are read from environment if not provided
        self.api_url = api_url or os.getenv("AI_LAYOUT_API_URL") or "https://v0ccs2aa8310l1i2.aistudio-app.com/layout-parsing"
        self.token = token or os.getenv("AI_LAYOUT_TOKEN")
        if not self.token:
            raise ValueError("AI_LAYOUT_TOKEN not provided (env var AI_LAYOUT_TOKEN or token parameter)")

    def _file_type_from_path(self, path: str) -> int:
        # 0 = PDF, 1 = image
        mime, _ = mimetypes.guess_type(path)
        if mime == 'application/pdf' or path.lower().endswith('.pdf'):
            return 0
        return 1

    def parse_file(self, file_path: str, out_dir: Optional[str] = None, extra_opts: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Send file to layout-parsing API and save results.

        Returns a dict {"status": int, "result": <api result>, "markdown": [..]}
        """
        extra_opts = extra_opts or {}
        if not os.path.isfile(file_path):
            raise FileNotFoundError(file_path)

        with open(file_path, 'rb') as f:
            b = f.read()

        file_b64 = base64.b64encode(b).decode('ascii')
        payload = {
            "file": file_b64,
            "fileType": self._file_type_from_path(file_path),
        }
        # merge optional flags
        payload.update(extra_opts)

        headers = {
            "Authorization": f"token {self.token}",
            "Content-Type": "application/json",
        }

        resp = requests.post(self.api_url, json=payload, headers=headers, timeout=120)
        resp.raise_for_status()
        data = resp.json()

        result = data.get('result') or {}
        out_dir = out_dir or os.path.join(os.getcwd(), 'ai_parse_output')
        os.makedirs(out_dir, exist_ok=True)

        markdown_texts = []
        # save layoutParsingResults -> markdown + images
        for i, res in enumerate(result.get('layoutParsingResults', []) or []):
            md = res.get('markdown', {}).get('text', '')
            markdown_texts.append(md)
            md_fname = os.path.join(out_dir, f"doc_{i}.md")
            with open(md_fname, 'w', encoding='utf-8') as md_f:
                md_f.write(md)

            # markdown images: keys are local paths, values are urls
            for img_path, img_url in (res.get('markdown', {}).get('images') or {}).items():
                full_img_path = os.path.join(out_dir, img_path)
                os.makedirs(os.path.dirname(full_img_path), exist_ok=True)
                # img_url may be a web URL; fetch it
                try:
                    img_bytes = requests.get(img_url, timeout=30).content
                    with open(full_img_path, 'wb') as imf:
                        imf.write(img_bytes)
                except Exception:
                    # skip failing images
                    pass

        # save any outputImages
        for img_name, img_url in (result.get('outputImages') or {}).items():
            try:
                r = requests.get(img_url, timeout=30)
                if r.status_code == 200:
                    fname = os.path.join(out_dir, f"{img_name}.jpg")
                    with open(fname, 'wb') as f:
                        f.write(r.content)
            except Exception:
                pass

        return {"status": resp.status_code, "result": result, "markdowns": markdown_texts, "out_dir": out_dir}


def parse_file_cli():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('file', help='file path to parse')
    p.add_argument('--out', help='output dir', default='ai_parse_output')
    p.add_argument('--token', help='API token (optional)')
    args = p.parse_args()
    token = args.token or os.getenv('AI_LAYOUT_TOKEN')
    parser = AiLayoutParser(token=token)
    res = parser.parse_file(args.file, out_dir=args.out)
    print('Status:', res.get('status'))
    print('Saved outputs to', res.get('out_dir'))


if __name__ == '__main__':
    parse_file_cli()
