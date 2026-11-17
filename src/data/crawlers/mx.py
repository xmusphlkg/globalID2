"""Mexico DGE annual morbidity: linked national source tables, never summed strata."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import unicodedata
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from .base import BaseCrawler, CrawlerResult

DEFAULT_SCOPE = "mx_dge_annual"
DEFAULT_SOURCE_NAME = "Mexico DGE Annual Morbidity"
ONTOLOGY_SOURCE_ID = "SRC_MX_DGE_ANNUAL"
LANDING_URL = "https://epidemiologia.salud.gob.mx/anuario/html/index.html"
NATIONAL_URL = urljoin(LANDING_URL, "morbilidad_nacional.html")
INCIDENCE_URL = urljoin(LANDING_URL, "incidencia_casos.html")
HISTORY_START_YEAR = 1984


class MexicoDGEContractError(ValueError):
    """A published table differs from the reviewed national annual contract."""


def _text(value):
    return " ".join(str(value).split())


def _fold(value):
    return "".join(c for c in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(c))


def discover_national_tables(payload, *, incidence_payload=None, start_year=HISTORY_START_YEAR, end_year=None):
    """Resolve only actual year options and their per-edition table options.

    5000/5001 mean 2003 female/male, not calendar years. Option labels own the
    calendar and sex; values own the URL path. lxml closes legacy option tags.
    """
    soup = BeautifulSoup(payload, "lxml")
    selector = soup.find("select", attrs={"name": "anio"})
    if selector is None:
        raise MexicoDGEContractError("DGE year selector missing")
    editions, refs = {}, {}
    for option in selector.find_all("option"):
        label = _text(option.get_text(" ", strip=True))
        match = re.fullmatch(r"(19\d{2}|20\d{2})(?:\s+(General|Femenino|Masculino))?", label, re.I)
        if not match:
            raise MexicoDGEContractError(f"Unrecognized DGE edition: {label!r}")
        year = int(match[1]); sex = {"femenino": "female", "masculino": "male"}.get((match[2] or "").casefold(), "all")
        if year < int(start_year) or (end_year is not None and year > int(end_year)):
            continue
        path = str(option.get("value", ""))
        if not path.isdigit():
            raise MexicoDGEContractError("DGE edition path must be source numeric token")
        editions[path] = {"year": year, "sex": sex, "edition_path": path, "edition_label": label}
        div = soup.find("div", id="A_" + path)
        if div is None:
            raise MexicoDGEContractError(f"Missing national edition menu {label}")
        selected = []
        for opt in div.find_all("option"):
            name = str(opt.get("value", ""))
            if "grupo_edad" in name and "casos_nuevos" in name and "incidencia" not in name:
                if not re.fullmatch(r"[a-z_]+", name):
                    raise MexicoDGEContractError("Unexpected national table path")
                url = urljoin(NATIONAL_URL, f"../{path}/morbilidad/nacional/{name}.pdf")
                refs[url] = {**editions[path], "url": url, "table": "national_cases_age", "label": _text(opt.get_text(" ", strip=True))}
                selected.append(url)
        if not selected:
            # The official 2002 national menu omits an age/count table. Its
            # source-notification distribution still publishes national totals.
            fallback=[opt for opt in div.find_all('option') if 'casos_nuevos' in str(opt.get('value','')) and 'fuente_notificacion' in str(opt.get('value',''))]
            if len(fallback)==1:
                opt=fallback[0];url=urljoin(NATIONAL_URL,f"../{path}/morbilidad/nacional/{opt['value']}.pdf")
                refs[url]={**editions[path],'url':url,'table':'national_cases_institution','label':_text(opt.get_text(' ',strip=True))};selected.append(url)
        if len(selected) != 1:
            raise MexicoDGEContractError(f"Expected one national age/count table for {label}; got {selected}")
    if incidence_payload is not None:
        soup = BeautifulSoup(incidence_payload, "lxml")
        selector = soup.find("select", attrs={"name": "anio"})
        if selector is None:
            raise MexicoDGEContractError("DGE incidence year selector missing")
        for option in selector.find_all("option"):
            path = str(option.get("value", "")); token = path.split("/")[0]
            if token not in editions:
                continue
            if not re.fullmatch(r"\d+/incidencia/[a-z_]+", path):
                raise MexicoDGEContractError("Unexpected incidence table path")
            url = urljoin(INCIDENCE_URL, "../" + path + ".pdf")
            refs[url] = {**editions[token], "url": url, "table": "national_incidence_age", "label": _text(option.get_text(" ", strip=True))}
    if not refs:
        raise MexicoDGEContractError("No national tables discovered in requested years")
    return sorted(refs.values(), key=lambda ref: (ref["year"], ref["sex"], ref["table"]))


def _lines(words, tolerance=1.8):
    lines=[]
    for word in sorted(words,key=lambda w:(w['top'],w['x0'])):
        match=next((line for line in reversed(lines[-3:]) if abs(line[-1]['top']-word['top'])<=tolerance),None)
        if match is None:lines.append([word])
        else:match.append(word)
    return [sorted(line,key=lambda w:w['x0']) for line in lines]


def _groups(words, gap=5):
    result=[]
    for word in sorted(words,key=lambda w:w['x0']):
        if not result or word['x0']-result[-1][-1]['x1']>gap:
            result.append([word])
        else:result[-1].append(word)
    return [{"text":_text(' '.join(w['text'] for w in g)),"x0":g[0]['x0'],"x1":g[-1]['x1'],"top":min(w['top'] for w in g),"bottom":max(w['bottom'] for w in g)} for g in result]


def _age(value):
    value=_fold(value).replace(' ','').replace('anos','').replace('ano','')
    if value in {'<1','<de1','-1','menoresde1'}:return 'lt1'
    if value in {'ign.','ign','noesp.','noesp','n.e.','ne'}:return 'unknown'
    if value in {'65y+','65ymas','65+','65ymayores','65y>'}:return '65plus'
    m=re.fullmatch(r'(\d{1,2})[-a](\d{1,2})',value)
    return str(int(m[1]))+'to'+str(int(m[2])) if m else None


def _number(value, *, rate):
    raw=_text(value); plain=raw.replace(' ','')
    if plain.upper() in {'N.A.','N.A','NA','N/A','N.D.','ND','S/D','-','—',''}:
        return '', 'not_applicable' if plain.upper() in {'N.A.','N.A','NA','N/A'} else 'source_missing'
    if rate and ',' in plain and '.' not in plain and re.search(r',\d{1,2}$',plain):
        plain=plain.replace(',','.')
    else:plain=plain.replace(',','')
    integer_part=re.split(r'[.,]',raw)[0] if rate else raw.replace(',',' ')
    if ' ' in integer_part and not re.fullmatch(r'\d{1,3}(?: \d{3})+',integer_part):
        raise MexicoDGEContractError(f'Invalid thousands grouping, possibly merged source cells: {raw!r}')
    if not re.fullmatch(r'\d+(?:\.\d+)?' if rate else r'\d+',plain):
        raise MexicoDGEContractError(f"Invalid {'rate' if rate else 'count'} source cell {raw!r}")
    return plain,''


def _parse_national_pdf(payload, *, reference, source_url=None, retrieved_at=None):
    """Geometry-based offline parsing with explicit headings and cell identities.

    Count table rates are preserved as provenance; the separate incidence table
    owns numeric rate observations. Historical institution columns stay archived
    and are not mixed with independent age counts or the national total.
    """
    import pdfplumber
    if payload[:4]!=b'%PDF':raise MexicoDGEContractError('National artifact is not PDF')
    year=int(reference['year']);sex=reference['sex'];rate=reference['table']=='national_incidence_age'
    digest=hashlib.sha256(payload).hexdigest();rows=[];audits=[];seen={};row_failures=[]
    with pdfplumber.open(io.BytesIO(payload)) as document:
        for page_number,original_page in enumerate(document.pages,1):
            page=original_page.dedupe_chars(tolerance=.7,extra_attrs=())
            words=page.extract_words(x_tolerance=1,y_tolerance=2,keep_blank_chars=False)
            text=page.extract_text() or ''
            folded=_fold(text)
            if len(words)<100 and any(x in folded for x in ('no aplica','tasa por','proyecciones')):
                audits.append({'page':page_number,'status':'footnotes_only','source_rows':0});continue
            # Source header owns the geography and year; do not infer a state total.
            compact=re.sub(r'\s+','',folded)
            if 'estadosunidosmexicanos' not in compact and 'estadosunidosmexicanos'.replace('mexicanos','mexicanos') not in compact:
                # The official 2003 header uses Méxicanos (fold normalizes it).
                raise MexicoDGEContractError(f'National geography header missing on page {page_number}')
            header_years=re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)',text[:400])
            if str(year) not in compact and header_years:
                raise MexicoDGEContractError(f'DGE year header differs from edition {year}')
            # A footer denominator may say "habitantes femeninas" even on a
            # General page. Only the actual population header can prove sex.
            population_header=folded[:300]
            if sex=='female' and not any(x in population_header for x in ('poblacion femenina','femeninos','femenino','mujeres')):raise MexicoDGEContractError('Female edition has no female header')
            if sex=='male' and not any(x in population_header for x in ('poblacion masculina','masculinos','masculino','hombres')):raise MexicoDGEContractError('Male edition has no male header')
            header_words=[w for w in words if w['top']<page.height*.3]
            code_groups=[g for line in _lines(header_words) for g in _groups(line) if re.match(r'^cie(?:\d|revision|$)',re.sub(r'[.\s]','',_fold(g['text'])))]
            if not code_groups:code_groups=[g for line in _lines(header_words) for g in _groups(line) if re.match(r'^(codigo de la lista|10a\. rev)',_fold(g['text']))]
            code_reported=bool(code_groups)
            if not code_groups:code_groups=[g for line in _lines(header_words) for g in _groups(line) if _fold(g['text']) in {'padecimiento','padecimientos','causa'}]
            if not code_groups:raise MexicoDGEContractError(f'Disease/code header missing on page {page_number}')
            code_header=min(code_groups,key=lambda g:g['top'])
            totals=[w for w in header_words if _fold(w['text']) in ('total','incidencia*','tasa*','incidencia','tasa') and abs(w['top']-code_header['top'])<18 and w['x0']>code_header['x0']]
            wanted=[w for w in totals if (_fold(w['text'])!='total')==rate]
            if not wanted:raise MexicoDGEContractError(f'National {"incidence" if rate else "total"} column missing')
            total=min(wanted,key=lambda w:(w['top'],w['x0']))
            # Repeated TOTAL under institutions is explicitly excluded.
            if not rate:total=min((w for w in totals if _fold(w['text'])=='total'),key=lambda w:w['x0'])
            ages=[]
            for line in _lines(header_words):
                groups=_groups(line)
                candidate=[(g,_age(g['text'])) for g in groups if _age(g['text']) and g['x0']>code_header['x1']]
                if len(candidate)>len(ages):ages=candidate
            if len(ages)<5 and reference['table']!='national_cases_institution':raise MexicoDGEContractError(f'National age headers not recognized on page {page_number}')
            # IGN occasionally sits one line above the remaining age labels.
            if ages and not any(age=='unknown' for _,age in ages):
                unknown=[w for w in header_words if _age(w['text'])=='unknown' and w['x0']>ages[-1][0]['x1'] and abs(w['top']-ages[-1][0]['top'])<14]
                if unknown:ages.append((unknown[0],'unknown'))
            if any(age=='unknown' for _,age in ages):
                ages=ages[:next(i for i,(_,age) in enumerate(ages) if age=='unknown')+1]
            header_bottom=max([g['bottom'] for g,_ in ages]+[total['bottom']])
            # A header wraps in older editions; first data values start below it.
            # Include "C. I. E." legacy headings, not just a single CIE word.
            code_center=(code_header['x0']+code_header['x1'])/2
            # Use disease/code numeric boundaries, never text token counts (spaces
            # are thousands separators in newer source editions).
            body=[w for w in words if w['top']>header_bottom+1]
            footers=[w['top'] for w in body if _fold(w['text']).startswith(('fuente:','fuente','nota:'))]
            bottom=min(footers,default=page.height-25)
            body=[w for w in body if w['top']<bottom-1]
            fields=[('all',total)]+[(age,g) for g,age in ages]
            if not rate:
                embedded=[w for w in totals if _fold(w['text'])!='total' and w['x0']>total['x1']]
                if embedded:fields.append(('_embedded_rate',min(embedded,key=lambda w:w['x0'])))
            fields.sort(key=lambda x:x[1]['x0'])
            left=(code_center+fields[0][1]['x0'])/2
            numeric_pattern=r'(?:\d[\d.,\s]*|N\.A\.?|N/A|—|-)'
            candidates=[]
            for line in _lines(body):
                groups=[g for g in _groups([w for w in line if w['x0']>=left],gap=2.5) if re.fullmatch(numeric_pattern,g['text'],re.I)]
                if len(groups)>=max(1,min(4,len(fields)-2)):candidates.append((sum(w['top'] for w in line)/len(line),groups))
            representative=next((groups for _,groups in candidates if len(groups)>=len(fields)),None)
            if representative is None:raise MexicoDGEContractError(f'No complete numeric row to verify source columns on page {page_number}')
            positions=[(g['x0']+g['x1'])/2 for g in representative[:len(fields)]]
            bounds=[left]+[(a+b)/2 for a,b in zip(positions,positions[1:])]+[positions[-1]+max(15,(positions[-1]-positions[-2])/2) if len(positions)>1 else positions[-1]+30]
            # Learn from actual source cells: some official PDF headers are
            # displaced by 15–20pt from their numeric columns (e.g. 2004).
            anchors=[]
            for top,groups in candidates:
                assigned={}
                for g in groups:
                    center=(g['x0']+g['x1'])/2
                    fi=next((i for i in range(len(fields)) if bounds[i]<=center<bounds[i+1]),None)
                    if fi is None:continue
                    if fi in assigned:assigned['_conflict']=True
                    assigned[fi]=g
                ti=next(i for i,(key,_) in enumerate(fields) if key=='all')
                if ti in assigned:anchors.append((top,assigned))
            if not anchors:raise MexicoDGEContractError(f'No national data cells on page {page_number}')
            # Disease/code boundary from source labels' common right margin and
            # code header. The gap midpoint is robust to wide CIE headings.
            for index,(top,assigned) in enumerate(anchors):
                try:
                    if assigned.get('_conflict'):raise MexicoDGEContractError('Two numeric cells map to one source column')
                    prior=anchors[index-1][0] if index else header_bottom
                    following=anchors[index+1][0] if index+1<len(anchors) else bottom
                    y0=(prior+top)/2 if index else header_bottom+1
                    y1=(top+following)/2
                    line_words=[w for w in body if y0<=w['top']<y1]
                    # ICD text ends before total. A source label may be multiline.
                    prefix=[w for w in line_words if w['x1']<left]
                    # ICD words include S/C and source numeric or alphanumeric codes.
                    candidates=[w for w in prefix if w['x0']>code_center-70 and (re.match(r'^[A-Z]?\d',w['text']) or w['text'].strip('().') in {'S/C','S.C','P.F.A','-'} )]
                    if not code_reported:code_start=left
                    elif candidates:code_start=min(w['x0'] for w in candidates)-.5
                    else:
                        # TOTAL is an aggregate across diseases, not a disease fact.
                        label=_text(' '.join(w['text'] for w in prefix))
                        if _fold(label).startswith(('total','fuente')):continue
                        raise MexicoDGEContractError(f'ICD code missing: page {page_number}, y={top:.1f}, {label}')
                    # A preceding wrapped ICD line belongs to this source row only
                    # if it lies in the same code region; preserve complete code.
                    code_words=[w for w in prefix if w['x0']>=code_start]
                    label_words=[w for w in prefix if w['x0']<code_start]
                    label=_text(' '.join(w['text'] for w in sorted(label_words,key=lambda w:(round(w['top']/2)*2,w['x0']))))
                    code=_text(' '.join(w['text'] for w in sorted(code_words,key=lambda w:(round(w['top']/2)*2,w['x0']))))
                    if not code_reported:code='source_not_reported'
                    if label=='0' and code=='0' and all(re.fullmatch(r'[0.\s]+',w['text']) for w in line_words):
                        # Actual 2003 PDF has two blank spreadsheet template rows
                        # filled entirely with zeroes, including disease and ICD.
                        continue
                    if not label or not code:raise MexicoDGEContractError('Empty source disease/code')
                    # Numeric rows align by source baseline even when label/code wraps.
                    values=[]
                    for fi,(age,_) in enumerate(fields):
                        if age=='_embedded_rate':continue
                        val,missing=_number(assigned.get(fi,{}).get('text',''),rate=rate);values.append((age,val,missing))
                    if not rate and ages:
                        national=next(item for item in values if item[0]=='all')
                        age_values=[item for item in values if item[0]!='all']
                        if national[1] and sum(int(item[1]) for item in age_values if item[1])!=int(national[1]):
                            row_failures.append({'page':page_number,'source_y':round(top,3),'status':'age_cells_not_retained_sum_differs','disease':label,'national_total':national[1],'parsed_age_values':age_values})
                            # A source national total stays independent. Unreconciled
                            # source age cells need review; never infer or rescale.
                            values=[national]
                    identity=hashlib.sha256((_fold(label)+'|'+code).encode()).hexdigest()[:20]
                    case_definition='estimated_cases_source_formula' if any(mark in label for mark in ('§',)) else 'source_annual_new_cases'
                    footnote=text[text.find('FUENTE'): ] if 'FUENTE' in text else text[text.find('Fuente'): ]
                    for age,value,missing in values:
                        source_code=f'dge_{identity}__{sex}__{age}__'+('rate' if rate else 'cases')
                        key=(year,source_code)
                        if key in seen:raise MexicoDGEContractError(f'Duplicate source disease cell {key}, label={label}, code={code}, y={top}, page={page_number}')
                        seen[key]=value
                        dimensions={'source_disease_label':label,'source_icd_code':code,'source_icd_revision':('ICD-10' if '10' in code_header['text'] or year>=1998 else 'ICD-9') if code_reported else 'source_not_reported','sex':sex,'age_group':age,'geography_level':'national','table':reference['table'],'source_case_definition':case_definition,'source_footnotes_sha256':hashlib.sha256(footnote.encode()).hexdigest(),'source_footnote_markers':''.join(c for c in label if c in '*#@~={}+&§º¥'),'rate_denominator':'source_footnotes_apply_not_uniform_total_population','source_page':page_number,'edition_path':reference['edition_path']}
                        rows.append({'Date':f'{year}-01-01','PeriodEnd':f'{year}-12-31','Year':str(year),'Month':'1','DiseaseCode':'__source_native__','RawDiseaseLabel':label,'SourceDiseaseCode':source_code,'SourceICDCode':code,'Cases':value,'MissingValueStatus':missing,'PeriodType':'annual','Frequency':'annual','Measure':'incidence_rate' if rate else 'case_notifications','Unit':'per_100000' if rate else 'count','ReportingBasis':'notification','DefinitionVersion':'mx_dge_annual_'+dimensions['source_icd_revision'].lower().replace('-','')+'_v1','Geography':'Mexico','GeographyKey':'country:MX:national','Source':DEFAULT_SOURCE_NAME,'SourceURL':source_url or reference['url'],'LandingURL':LANDING_URL,'RawSHA256':digest,'RetrievedAt':retrieved_at or '', 'Sex':sex,'AgeGroup':age,'RevisionSemantics':'authoritative_revision','AuthoritativeRevision':'true','PublicReleaseEnabled':'false','Dimensions':json.dumps(dimensions,ensure_ascii=False,sort_keys=True)})
                except MexicoDGEContractError as exc:
                    row_failures.append({'page':page_number,'source_y':round(top,3),'error':str(exc),'status':'unsupported_row'})
                    continue
            audits.append({'page':page_number,'source_rows':len(anchors),'age_columns':[x[1] for x in ages],'source_footnotes':text[text.find('FUENTE'):] if 'FUENTE' in text else ''})
    if not rows:raise MexicoDGEContractError('National PDF produced no disease observations')
    return rows,{'source_url':source_url or reference['url'],'year':year,'sex':sex,'table':reference['table'],'pages':audits,'rows':len(rows),'missing':sum(not r['Cases'] for r in rows),'sha256':digest,'failed_rows':row_failures,'status':'partial' if row_failures else 'parsed'}


def parse_national_pdf(payload, *, reference, source_url=None, retrieved_at=None):
    """Keep verified pages/rows while explicitly auditing unsupported source cells.

    A conflicting population/year/geography page contributes no observations.
    Source bytes and SHA always refer to the unmodified original PDF.
    """
    import pypdfium2
    if payload[:4]!=b'%PDF':raise MexicoDGEContractError('National artifact is not PDF')
    digest=hashlib.sha256(payload).hexdigest();document=pypdfium2.PdfDocument(payload)
    rows=[];pages=[];failures=[]
    try:
        for index in range(len(document)):
            single=pypdfium2.PdfDocument.new()
            try:
                single.import_pages(document,[index]);stream=io.BytesIO();single.save(stream)
                try:
                    parsed,audit=_parse_national_pdf(stream.getvalue(),reference=reference,source_url=source_url,retrieved_at=retrieved_at)
                except MexicoDGEContractError as exc:
                    failures.append({'page':index+1,'error':str(exc),'status':'unsupported_page'});continue
                for row in parsed:
                    row['RawSHA256']=digest
                    dims=json.loads(row['Dimensions']);dims['source_page']=index+1;row['Dimensions']=json.dumps(dims,ensure_ascii=False,sort_keys=True)
                rows.extend(parsed)
                for page in audit['pages']:pages.append({**page,'page':index+1})
                failures.extend({**failure,'page':index+1} for failure in audit.get('failed_rows',[]))
            finally:single.close()
    finally:document.close()
    identities={};conflicts=set()
    for row in rows:
        key=(row['Date'],row['SourceDiseaseCode'])
        if key in identities:
            if identities[key]['Cases']!=row['Cases']:
                conflicts.add(key);failures.append({'status':'conflicting_same_edition_cell','source_code':key[1]})
        else:identities[key]=row
    rows=[row for key,row in identities.items() if key not in conflicts]
    if not rows:raise MexicoDGEContractError('No supported national source cells: '+ '; '.join(f.get('error',f.get('status','unsupported')) for f in failures[:3]))
    return rows,{'source_url':source_url or reference['url'],'year':int(reference['year']),'sex':reference['sex'],'table':reference['table'],'pages':pages,'rows':len(rows),'missing':sum(not r['Cases'] for r in rows),'sha256':digest,'failed_count':len(failures),'failures':failures,'status':'partial' if failures else 'parsed'}


class MexicoDGEAnnualCrawler(BaseCrawler):
    def __init__(self, **kwargs):super().__init__(timeout=90,delay=.15,max_retries=3)

    def discover(self, *, start_year=HISTORY_START_YEAR,end_year=None):
        national=self.get(NATIONAL_URL).content;incidence=self.get(INCIDENCE_URL).content
        refs=discover_national_tables(national,incidence_payload=incidence,start_year=start_year,end_year=end_year)
        return refs,[(NATIONAL_URL,national),(INCIDENCE_URL,incidence)]

    def download(self, reference, *, force=False, cache_ttl_days=7):
        """Verified byte cache shared by CLI and staged collect; no stale fallback.

        A reused response must still be passed to ArtifactWriter.write() by the
        caller, so cached bytes never bypass durable task artifact registration.
        """
        from datetime import timedelta
        directory=Path('data/raw/mx/dge_annual_cache');directory.mkdir(parents=True,exist_ok=True)
        receipt=directory/(hashlib.sha256(reference['url'].encode()).hexdigest()+'.json')
        old=None
        if receipt.exists():old=json.loads(receipt.read_text())
        elif Path('data/raw/mx/dge_annual_full_history/artifacts_manifest.json').exists():
            candidates=json.loads(Path('data/raw/mx/dge_annual_full_history/artifacts_manifest.json').read_text())
            item=next((x for x in candidates if x['kind']=='mx_dge_pdf' and x['source_url']==reference['url']),None)
            if item:old={**item['metadata_'],'relative_path':item['relative_path'],'sha256':item['sha256'],'byte_size':item['byte_size']}
        if old and not force:
            path=Path(old['relative_path'])
            if not path.resolve().is_relative_to(Path('data/raw/mx').resolve()):raise MexicoDGEContractError('DGE byte cache path escaped country raw archive')
            body=path.read_bytes()
            if len(body)!=old['byte_size'] or hashlib.sha256(body).hexdigest()!=old['sha256']:raise MexicoDGEContractError('DGE byte cache checksum differs')
            age=datetime.now(timezone.utc)-datetime.fromisoformat(old['retrieved_at'])
            if age<timedelta(days=cache_ttl_days):return body,{**old,'cache_reused':True}
        body=self.get(reference['url']).content
        if body[:4]!=b'%PDF':raise MexicoDGEContractError('DGE returned non-PDF response')
        digest=hashlib.sha256(body).hexdigest();path=directory/(digest+'.pdf')
        if path.exists():
            if path.read_bytes()!=body:raise MexicoDGEContractError('DGE cache content-address collision')
        else:
            temporary=path.with_suffix('.pdf.tmp');temporary.write_bytes(body);os.replace(temporary,path)
        result={**reference,'relative_path':str(path),'sha256':digest,'byte_size':len(body),'retrieved_at':datetime.now(timezone.utc).isoformat(),'cache_reused':False}
        temporary=receipt.with_suffix('.json.tmp');temporary.write_text(json.dumps(result,ensure_ascii=False,indent=2));os.replace(temporary,receipt)
        return body,result

    def fetch_rows(self, **options):
        refs,_=self.discover(**options);rows=[]
        for ref in refs:
            payload,metadata=self.download(ref)
            parsed,_=parse_national_pdf(payload,reference=ref,retrieved_at=metadata['retrieved_at']);rows.extend(parsed)
        return rows

    async def crawl(self, **kwargs):
        rows=await asyncio.to_thread(self.fetch_rows,start_year=kwargs.get('start_year',HISTORY_START_YEAR),end_year=kwargs.get('end_year'))
        return [CrawlerResult(title=DEFAULT_SOURCE_NAME,url=LANDING_URL,raw_data={'rows':rows})]

    def parse(self,response):raise MexicoDGEContractError('Parse requires an explicit frozen edition reference')
