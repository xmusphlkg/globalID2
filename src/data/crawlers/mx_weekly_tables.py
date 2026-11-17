"""DGE national TOTAL cells for mixed-sex, inline-ICD and clinical-stage tables.

Only printed national totals are read. Column headers establish the reporting
year, incident/cumulative measure and sex; no state or stage values are summed.
"""
from __future__ import annotations

import json
import re

from .mx import MexicoDGEContractError, _fold, _groups, _lines


def _center(word):
    return (word['x0'] + word['x1']) / 2


def parse_national_total(words, *, caption_evidence, issue_year, issue_week,
                         issue_start, issue_end, page_number, source_url,
                         digest, retrieved_at, legend_pages):
    from .mx_high_frequency import WEEKLY_SCOPE, _row, _table_period, _weekly_number, _words_text

    total = [w for w in words if w['text'] == 'TOTAL']
    if len(total) != 1:
        raise MexicoDGEContractError('Exactly one explicit national TOTAL row required')
    totals = _groups([w for w in words if abs(w['top']-total[0]['top']) < 1.5
                      and w['x0'] > total[0]['x1']+3], gap=5)
    current = [w for w in words if w['text'] == str(issue_year) and 70 < w['top'] < 270]
    if not current:
        raise MexicoDGEContractError('Source current-year column headers missing')
    head_y = max(w['top'] for w in current)
    year_heads = sorted([w for w in words if abs(w['top']-head_y) < 2
                         and re.fullmatch(r'20\d{2}', w['text'])], key=_center)
    cie = sorted([w for w in words if w['text'].startswith('CIE-') and 70 < w['top'] < head_y], key=_center)
    if not cie or len(cie) > 3:
        raise MexicoDGEContractError('One to three explicit ICD disease headings required')

    if caption_evidence:
        caption = caption_evidence['caption']
        caption_bottom = caption_evidence['crop_points'][3]
    else:
        lines = _lines([w for w in words if 70 < w['top'] < min(x['top'] for x in cie)])
        start = next((i for i, line in enumerate(lines) if any(w['text'] == 'CUADRO' for w in line)), None)
        if start is None:
            raise MexicoDGEContractError('Explicit national table caption missing')
        title = [lines[start]]
        for line in lines[start+1:]:
            if line[0]['top']-title[-1][0]['top'] > 18:
                break
            title.append(line)
        caption = _words_text([w for line in title for w in line])
        caption_bottom = max(w['bottom'] for line in title for w in line)
    number = re.search(r'CUADRO\s+(\d+(?:\.\d+)?)', caption, re.I)
    if not number:
        raise MexicoDGEContractError('Explicit national table number missing')
    foot = min((w['top'] for w in words if w['text'].startswith('FUENTE:')), default=total[0]['bottom'])
    common = {'source_page':page_number, 'source_table':number[1],
              'bulletin_issue_year':issue_year, 'bulletin_issue_week':issue_week,
              'source_footnotes':_words_text([w for w in words if w['top'] >= foot]),
              'dash_semantics':'reported_zero' if legend_pages else 'source_missing',
              'dash_legend_pages':legend_pages}
    if caption_evidence:
        common['source_caption_ocr'] = caption_evidence

    clinical = 'estadios clinicos' in _fold(_words_text([w for w in words if head_y < w['top'] < head_y+45]))
    if clinical:
        if len(cie) != 1 or len(totals) not in (9,10,11):
            raise MexicoDGEContractError('Clinical-stage TOTAL requires one disease and nine current-year columns')
        sexes = sorted([w for w in words if head_y < w['top'] < head_y+70
                         and w['text'] in {'H','M'}], key=_center)
        sex_top = min((w['top'] for w in sexes), default=head_y)
        stage_words = sorted([w for w in words if head_y < w['top'] < sex_top
                              and w['text'] in {'1','2','3','4'}], key=_center)
        weekly_heads = [w for w in words if head_y < w['top'] < sex_top
                        and _fold(w['text']).rstrip('.')=='sem' and _center(w)<_center(stage_words[0])] if stage_words else []
        if ([w['text'] for w in stage_words] != ['1','2','3','4']
                or [w['text'] for w in sexes] != ['H','M']*4 or len(weekly_heads) != 1
                or not _center(weekly_heads[0]) < _center(totals[0]) < _center(sexes[0])):
            raise MexicoDGEContractError('Clinical-stage years/stages/H-M headers are not verified')
        # The current-year clinical-stage block is not explicitly labelled
        # cumulative. Preserve those eight cells in the original; only Sem.
        # has an independently established temporal measure on this table.
        groups = [(0, float('inf'), cie[0], [('case_notifications','all',0,None)])]
    else:
        sems = sorted([w for w in words if head_y < w['top'] < head_y+50
                       and _fold(w['text']).rstrip('.') == 'sem'], key=_center)
        if len(sems) != len(cie):
            raise MexicoDGEContractError('Each disease requires an explicit weekly column')
        layouts = []
        for i, marker in enumerate(cie):
            next_sem = _center(sems[i+1]) if i+1<len(sems) else float('inf')
            sex_words = sorted([w for w in words if head_y < w['top'] < head_y+50
                                and w['text'] in {'H','M'} and _center(sems[i]) < _center(w) < next_sem], key=_center)
            if [w['text'] for w in sex_words] not in ([],['H','M']):
                raise MexicoDGEContractError('Unsupported or incomplete cumulative sex columns')
            acums = sorted([w for w in words if head_y < w['top'] < head_y+50
                            and w['text'].startswith('Acum') and _center(sems[i]) < _center(w) < next_sem],key=_center)
            if not acums:
                raise MexicoDGEContractError('Current cumulative heading required')
            headings=[sems[i],*sex_words] if sex_words else [sems[i],acums[0]]
            centers=[_center(w) for w in headings]
            bounds=[centers[0]-(centers[1]-centers[0])/2]
            bounds += [(a+b)/2 for a,b in zip(centers,centers[1:])]
            bounds += [centers[-1]+(centers[-1]-centers[-2])/2]
            current_heads=[w for w in year_heads if w['text']==str(issue_year) and bounds[0]<_center(w)<bounds[-1]]
            if len(current_heads)!=1 or not bounds[1]<_center(acums[0])<bounds[-1]:
                raise MexicoDGEContractError('Current year/cumulative header alignment not verified')
            cells=[]
            for low,high in zip(bounds,bounds[1:]):
                found=[j for j,w in enumerate(totals) if low<=_center(w)<high]
                if len(found)!=1:
                    raise MexicoDGEContractError('TOTAL cells do not align with explicit current-year measure columns')
                cells.append(found[0])
            emitted=[('case_notifications','all',cells[0],None)]
            emitted += ([('cumulative_case_notifications','male',cells[1],None),
                         ('cumulative_case_notifications','female',cells[2],None)] if sex_words else
                        [('cumulative_case_notifications','all',cells[1],None)])
            layouts.append((bounds[0],marker,emitted))
        # The original may omit a previous-year column entirely. It cannot
        # invalidate independently labelled current-year cells or fill them.
        groups=[(left,layouts[i+1][0] if i+1<len(layouts) else float('inf'),marker,emitted)
                for i,(left,marker,emitted) in enumerate(layouts)]

    rows=[]
    for left,right,marker,emitted in groups:
        prefix = [w for w in words if left <= _center(w) < right
                  and caption_bottom < w['top'] < head_y-3
                  and w['text'] not in {'ENTIDAD','FEDERATIVA'}]
        label = _words_text([w for w in prefix if w['top'] < marker['top']-2])
        code = _words_text([w for w in prefix if w['top'] >= marker['top']-2
                            and not w['text'].startswith(('CIE-','REV.'))])
        if not label or not re.match(r'^(?:[A-Z]\d|S/C)',code):
            raise MexicoDGEContractError('National TOTAL disease heading/code missing')
        sy,week,start,end = _table_period(caption,label,issue_year=issue_year,issue_week=issue_week,
                                         issue_start=issue_start,issue_end=issue_end)
        definition = 'special_confirmed_surveillance' if '§' in label else 'conventional_including_probable'
        for measure,sex,j,stage in emitted:
            value,missing = _weekly_number(totals[j]['text'],zero_dash=bool(legend_pages))
            dimensions = {**common, 'source_epi_year':sy, 'source_epi_week':week,
                          'table_family':'national_total_from_state_table',
                          'calendar':'source_verified_sunday_saturday'}
            if stage is not None:
                dimensions['clinical_stage'] = str(stage)
            row = _row(scope=WEEKLY_SCOPE,label=label,icd=code,value=value,missing=missing,
                       period_start=start,period_end=end,frequency='weekly',measure=measure,
                       sex=sex,definition=definition,dimensions=dimensions,source_url=source_url,
                       digest=digest,retrieved_at=retrieved_at,year=sy)
            if clinical:
                provenance=json.loads(row['SourceProvenance'])
                provenance['excluded_source_cells']=[{
                    'page':page_number,'table':number[1],'clinical_stage':str(stage+1),
                    'sex':sex_label,'source_text':totals[1+2*stage+offset]['text'],
                    'reason':'clinical_stage_temporal_measure_not_explicit',
                } for stage in range(4) for offset,sex_label in enumerate(('male','female'))]
                row['SourceProvenance']=json.dumps(provenance,ensure_ascii=False,sort_keys=True)
            if stage is not None:
                # A stage is a source stratum, not a separate patient total.
                row['SourceDiseaseCode'] += f'__clinical_stage_{stage}'
            rows.append(row)
    return rows
