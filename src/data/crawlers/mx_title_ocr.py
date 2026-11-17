"""Local OCR evidence for DGE summary captions omitted from the PDF text layer.

Numbers and disease labels are never read from OCR. Two independent render
scales must agree on the complete caption and explicit cutoff before use.
"""
from __future__ import annotations
import hashlib
import io
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .mx import _fold


def capture_summary_captions(payload):
    if not shutil.which('tesseract'):return None
    import pypdfium2
    document=pypdfium2.PdfDocument(payload);entries=[]
    try:
        for index in range(len(document)):
            page=document[index];tp=page.get_textpage();text=tp.get_text_range();tp.close()
            if 'ENFERMEDAD' not in text or 'ENTIDAD' in text or re.search(r'CUADRO\s+\d',text,re.I):
                page.close();continue
            # Reviewed DGE 540x720 layout: graphically drawn table captions
            # lie between the page running header and actual text headers.
            if tuple(page.get_size())!=(540.0,720.0):page.close();continue
            outputs=[];digests=[]
            for scale in (3,4):
                picture=page.render(scale=scale).to_pil().crop((0,60*scale,540*scale,94*scale))
                data=io.BytesIO();picture.save(data,format='PNG');digests.append(hashlib.sha256(data.getvalue()).hexdigest())
                with tempfile.TemporaryDirectory(prefix='mx-title-ocr-') as directory:
                    image=Path(directory)/'caption.png';image.write_bytes(data.getvalue())
                    output=subprocess.run(['tesseract',str(image),'stdout','-l','spa','--psm','6'],check=True,capture_output=True,text=True,timeout=30).stdout.strip()
                outputs.append(output)
            page.close()
            normalized=[' '.join(_fold(v).split()) for v in outputs]
            if normalized[0]!=normalized[1]:continue
            match=re.search(r'cuadro\s+([12])\.?\s+.+hasta\s+la\s+semana\s+(\d{1,2})\s+del\s+(20\d{2})',normalized[0])
            if not match:continue
            entries.append({'page':index+1,'table':match[1],'source_epi_week':int(match[2]),'source_epi_year':int(match[3]),
                'caption':outputs[0],'independent_ocr_texts':outputs,'render_scales':[3,4],
                'crop_points':[0,60,540,94],'rendered_crop_sha256':digests,
                'method':'tesseract_spa_two_scale_exact_caption_agreement',
                'engine_version':subprocess.run(['tesseract','--version'],capture_output=True,text=True,check=True).stdout.splitlines()[0]})
    finally:document.close()
    return {'pdf_sha256':hashlib.sha256(payload).hexdigest(),'captions':entries} if entries else None


def verify_caption_evidence(evidence, *, pdf_sha256, issue_year, issue_week):
    if not isinstance(evidence,dict) or evidence.get('pdf_sha256')!=pdf_sha256:raise ValueError('DGE caption OCR belongs to a different PDF')
    captions={}
    for item in evidence.get('captions',[]):
        texts=item.get('independent_ocr_texts',[])
        if (item.get('method')!='tesseract_spa_two_scale_exact_caption_agreement'
            or item.get('render_scales')!=[3,4] or item.get('crop_points')!=[0,60,540,94]
            or len(texts)!=2 or ' '.join(_fold(texts[0]).split())!=' '.join(_fold(texts[1]).split())
            or texts[0]!=item.get('caption') or len(item.get('rendered_crop_sha256',[]))!=2):raise ValueError('DGE caption OCR agreement evidence is invalid')
        text=_fold(item['caption'])
        match=re.fullmatch(r'cuadro\s+([12])\.?\s+.+hasta\s+la\s+semana\s+(\d{1,2})\s+del\s+(20\d{2})',text)
        if (not match or item.get('table')!=match[1] or item.get('source_epi_week')!=int(match[2])
            or item.get('source_epi_year')!=int(match[3]) or int(match[3])!=issue_year
            or not 1<=int(match[2])<=issue_week):raise ValueError('DGE OCR caption cutoff differs from independently verified cover year/week')
        if type(item.get('page')) is not int or item['page']<1 or item['page'] in captions:raise ValueError('DGE OCR caption page is invalid or duplicated')
        captions[item['page']]=item
    return captions
