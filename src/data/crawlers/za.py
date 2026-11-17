"""Official South African NICD NMC archive and source-native table contracts."""
from __future__ import annotations
import calendar
import hashlib
import io
import json
import re
from datetime import date
from urllib.parse import urljoin,urlparse
from lxml import html
from .base import BaseCrawler,CrawlerResult

DEFAULT_SCOPE='za_nicd_nmc'
DEFAULT_SOURCE_NAME='South Africa NICD NMC'
ONTOLOGY_SOURCE_ID='SRC_ZA_NICD_NMC'
INDEX_URL='https://www.nicd.ac.za/nmc-overview/nmc-monthly-surveillance-report/'
MEDIA_URLS=['https://www.nicd.ac.za/wp-json/wp/v2/media?search=NMCSS&per_page=100','https://www.nicd.ac.za/wp-json/wp/v2/media?search=Notifiable-Medical-Conditions&per_page=100']
PROVINCES={'EC':'Eastern Cape','FS':'Free State','GP':'Gauteng','KZN':'KwaZulu-Natal','LP':'Limpopo','MP':'Mpumalanga','NC':'Northern Cape','NW':'North West','WC':'Western Cape'}
MONTHS={calendar.month_name[i].casefold():i for i in range(1,13)}
MONTHS.update({calendar.month_abbr[i].casefold():i for i in range(1,13)})
MONTH_PATTERN='|'.join(sorted(MONTHS,key=len,reverse=True))
class SouthAfricaNICDNMCContractError(ValueError):pass

def discover_reports(payload,*,base_url=INDEX_URL):
 if not payload.strip():raise SouthAfricaNICDNMCContractError('Empty NMC archive catalogue')
 page=html.fromstring(payload);reports={}
 for a in page.xpath('//a[@href]'):
  u=urljoin(base_url,a.get('href'));label=' '.join(a.text_content().split())
  if urlparse(u).hostname not in {'www.nicd.ac.za','nicd.ac.za'} or not u.lower().endswith('.pdf'):continue
  if not re.search('NMC.*REPORT',label,re.I):continue
  reports[u]={'url':u,'title':label,'listing_url':base_url,'listed_current':True,'kind':'pdf'}
 if not reports:raise SouthAfricaNICDNMCContractError('No official NMC monthly report links')
 return list(reports.values())

def discover_media(payload,existing):
 reports={r['url']:dict(r) for r in existing}
 for item in json.loads(payload):
  u=item.get('source_url','');title=item.get('title',{}).get('rendered','')
  if urlparse(u).hostname not in {'www.nicd.ac.za','nicd.ac.za'}:continue
  is_monthly=(re.search(r'nmcss.*report|nmcss[-_].*(?:january|feb|march|april|may|june|july|aug|sep|oct|nov|dec)',u,re.I) or re.search(r'Notifiable-Medical-Conditions-Surveillance-System-(?:'+MONTH_PATTERN+')',u,re.I))
  if u not in reports and (not is_monthly or 'annual' in u.casefold() or not u.lower().endswith(('.pdf','.docx'))):continue
  report=reports.setdefault(u,{'url':u,'title':title,'listing_url':INDEX_URL,'listed_current':False,'kind':'docx' if u.lower().endswith('.docx') else 'pdf'})
  report.update(publication_date=item.get('date',''),modified_date=item.get('modified',''),media_id=item['id'])
 return list(reports.values())

def extract_pdf_pages(payload):
 if not payload.startswith(b'%PDF'):raise SouthAfricaNICDNMCContractError('Raw NMC report is not PDF')
 import pypdfium2 as pdfium
 doc=pdfium.PdfDocument(payload);pages=[]
 try:
  for page in doc:
   tp=page.get_textpage()
   try:pages.append(tp.get_text_range().replace('\r\n','\n').replace('\r','\n'))
   finally:tp.close();page.close()
 finally:doc.close()
 if not any(x.strip() for x in pages):raise SouthAfricaNICDNMCContractError('No verifiable PDF text layer')
 return pages

def _clean(value):
 return ' '.join(str(value or '').replace('\xad','').replace('\ufffe','').replace('\ufffd','').replace('\u00ad','').replace('￾','').split())
def _label(value):
 s=_clean(value);s=re.sub(r'^[*¥$†\d]+\s*','',s);s=re.sub(r'[*†¥$§]+$','',s);return s.strip()
def _verified_label(value):
 label=_label(value)
 key=re.sub(r'[-–\s]+',' ',label.casefold()).strip()
 patterns=[r'acute flaccid paralysis(?:/poliomyelitis)?',r'acute rheumatic fever',r'agricultural or stock remedy poisoning',r'congenital rubella syndrome',r'enteric fever \(typhoid(?: or |/)paratyphoid fever\)',r'food ?borne illness outbreak',r'haemolytic uraemic syndrome \(hus\)',r'haemophilus influenzae type b',r'maternal death \(pregnancy[,.] childbirth and puerperium\)\d*',r'respiratory disease caused by a novel respiratory pathogen(?: hps)?',r'tuberculosis: ?(?:extensively drug resistant \(xdr ?tb\)|multi ?drug resistant \(mdr ?tb\)|extra pulmonary|pulmonary)',r'non typhoidal salmonellosis',r'waterborne illness outbreak undefined',r'crimean congo viral haemorrhagic fever \(human\)',r'endemic arboviral diseases:?(?: chikungunya virus| west nile virus| sindbis)',r'non endemic arbovirus disease: dengue fever virus']
 simple={'anthrax','bilharzia (schistosomiasis)','botulism','brucellosis','congenital syphilis','covid 19','cholera','diphtheria','ebola virus (vhf)','hepatitis a','hepatitis b','hepatitis c','hepatitis e','lead poisoning','legionellosis','leprosy','listeriosis','malaria','marburg virus (vhf)','measles','meningococcal disease','mercury poisoning','monkeypox','mpox','pertussis','plague','poliomyelitis','rabies','rabies (human)','rift valley fever (human)','rubella','shigellosis','smallpox','soil transmitted helminths','tetanus','viral haemorrhagic fever diseases','west nile virus','zika virus','dengue fever virus','chikungunya virus','yellow fever'}
 if key not in simple and not any(re.fullmatch(pattern,key) for pattern in patterns):raise SouthAfricaNICDNMCContractError(f'Incomplete or unverified source condition label: {label}')
 return label

def _slug(value):return re.sub('[^a-z0-9]+','_',_label(value).casefold()).strip('_')
def _count(value):
 s=_clean(value)
 if s.casefold() in {'','-','—','*','n/a','na','unknown'}:return None
 m=re.fullmatch(r'([\d ,]+)(?:\s*\((?:[<>]?\d+(?:[.,]\d+)?%?|n/a)\)?)?[*†]?',s,re.I)
 if not m:raise SouthAfricaNICDNMCContractError(f'Unverified NMC integer cell {s!r}')
 digits=re.sub('[ ,]','',m[1]);return int(digits) if digits else None

def report_period(pages,report):
 cover=_clean(' '.join(pages[:2]))
 q=re.search(r'January\s*[-–]\s*March\s+(20\d{2})|January\s+to\s+March\s+(20\d{2})',cover,re.I)
 if q:
  y=int(q[1] or q[2]);return {'year':y,'month':1,'frequency':'quarterly','period':'reported_quarter','start':f'{y}-01-01','end':f'{y}-03-31','label':f'January-March {y}'}
 matches=list(re.finditer(r'\b('+MONTH_PATTERN+r')\s+(20\d{2})\b',cover,re.I))
 if not matches:raise SouthAfricaNICDNMCContractError('PDF lacks explicit reporting month/year')
 # Report covers/title/Introduction establish the period; URL upload month is
 # deliberately never used as an observation month.
 m=matches[0];year,month=int(m[2]),MONTHS[m[1].casefold()]
 return {'year':year,'month':month,'frequency':'monthly','period':'reported_month','start':f'{year}-{month:02d}-01','end':date(year,month,calendar.monthrange(year,month)[1]).isoformat(),'label':f'{calendar.month_name[month]} {year}'}

def _time_basis(pages):
 first=_clean(' '.join(pages[:4])).casefold()
 if 'date of diagnosis is used for reporting' in first or 'cases diagnosed and notified' in first:return 'diagnosis_with_notification_delays'
 if 'cases notified during' in first or 'case notifications from' in first:return 'notification_window'
 return 'source_reported_window'

def _row(label,value,*,category,classification,measure,geography,period,report,sha,page,cohort='all_notifications',time_basis='source_reported_window'):
 code=':'.join([f'nmc{category}',_slug(label),classification,period['period'],measure,time_basis,cohort])
 key='country:ZA:national' if geography=='national' else f'country:ZA:province:{geography}'
 dimensions={'source_disease_code':_slug(label),'nmc_category':str(category),'case_classification':classification,'count_period':period['period'],'time_basis':time_basis,'cohort':cohort,'source_window_label':period['label'],'source_window_detail':period.get('window_detail',''),'date_role':'representative_period_start'}
 return {'Date':period['start'],'Year':str(period['year']),'Month':str(period['month']),'DiseaseCode':'__source_native__','RawDiseaseLabel':_label(label),'SourceDiseaseCode':code,'Cases':'' if value is None else str(value),'MissingValueStatus':'source_missing' if value is None else '', 'Measure':measure,'Unit':'count','Frequency':period['frequency'],'PeriodType':period['period'],'ReportingBasis':'notification','DefinitionVersion':f'za_nicd_nmc_{classification}_{period["period"]}_{measure}_{time_basis}_{cohort}_v1','Geography':'South Africa' if geography=='national' else PROVINCES[geography],'GeographyKey':key,'Source':DEFAULT_SOURCE_NAME,'SourceURL':report['url'],'SourceListingURL':report.get('listing_url',INDEX_URL),'RawSHA256':sha,'SourcePage':str(page),'SourceReportYear':str(report['report_year']),'SourceReportMonth':str(report['report_month']),'SourceReportPublicationDate':report.get('publication_date',''),'SourceReportCurrentLink':str(report.get('listed_current',False)).lower(),'SourcePeriodStart':period['start'] if period['frequency']=='quarterly' else '','SourcePeriodEnd':period['end'] if period['frequency']=='quarterly' else '','SourceWindowDetail':period.get('window_detail',''),'SourcePeriodLabel':period['label'],'SourceCalendar':'source_named_reporting_window','DatasetStatus':'provisional','IsProvisional':'true','RevisionSemantics':'authoritative_revision','AuthoritativeRevision':'true','PublicReleaseEnabled':'false','License':'reuse_terms_unverified','Dimensions':json.dumps(dimensions,sort_keys=True)}

def _category(text):
 # Captions, not the disease's intuitive classification, determine the source
 # NMC category. A condition can move category across report editions.
 prefix=re.split(r'\bEC\s*1?\s+FS',text,maxsplit=1)[0]
 matches=re.findall(r'Table\s*\d+[.:]?\s*[^\n]{0,100}?(?:Category|category)\s*([123])\b',prefix)
 if matches:return matches[0]
 heading=re.search(r'(?:^|\b)(?:Distribution of\s+)?Category\s*([123])\s+(?:notifications|NMCs)',text,re.I|re.M)
 if heading:return heading[1]
 prose=re.findall(r'category\s*([123])\s+notification',prefix,re.I)
 return prose[0] if len(set(prose))==1 else None

def _province_header(table):
 for i,row in enumerate(table[:5]):
  cols={j:re.sub(r'\d+$','',_clean(c).upper()).strip() for j,c in enumerate(row) if re.sub(r'\d+$','',_clean(c).upper()).strip() in PROVINCES}
  if len(cols)>=8 and len(set(cols.values()))==len(cols):return i,cols
 return None

def _coordinate_table(page,*,kind='province'):
 """Read explicit printed columns when PDF line segmentation omits cells."""
 words=page.extract_words(x_tolerance=2,y_tolerance=3)
 candidates=[]
 for w in words:
  if w['text'] in PROVINCES:candidates.append(w)
 groups=[]
 for w in candidates:
  group=next((g for g in groups if abs(g[0]['top']-w['top'])<5),None)
  if group is None:groups.append([w])
  else:group.append(w)
 groups=[g for g in groups if len({w['text'] for w in g})>=8]
 if not groups:return None
 header=sorted(groups[0],key=lambda w:w['x0']);top=max(w['bottom'] for w in header)
 totals=[w for w in words if w['text'].casefold().startswith('total') and abs(w['top']-header[0]['top'])<8 and w['x0']>header[-1]['x0']]
 if len(totals)!=1:return None
 heads=header+[totals[0]];centres=[(w['x0']+w['x1'])/2 for w in heads];bounds=[centres[0]-(centres[1]-centres[0])/2]+[(a+b)/2 for a,b in zip(centres,centres[1:])]+[page.width]
 anchors=[]
 for w in words:
  centre=(w['x0']+w['x1'])/2
  if w['top']>top and bounds[0]<=centre<bounds[1] and re.fullmatch(r'[\d,]+|[-—]|n/a',w['text'],re.I):
   y=(w['top']+w['bottom'])/2
   if not anchors or all(abs(y-a)>4 for a in anchors):anchors.append(y)
 anchors.sort();rows=[['Condition']+[w['text'] for w in heads]]
 for i,y in enumerate(anchors):
  lo=y-5 if i==0 else (anchors[i-1]+y)/2;hi=(y+anchors[i+1])/2 if i+1<len(anchors) else y+10
  line=[w for w in words if lo<=(w['top']+w['bottom'])/2<hi]
  cells=[' '.join(w['text'] for w in sorted(line,key=lambda w:(round(w['top']/4),w['x0'])) if w['x1']<=bounds[0]+2)]
  for a,b in zip(bounds,bounds[1:]):cells.append(' '.join(w['text'] for w in sorted(line,key=lambda w:w['x0']) if a<=(w['x0']+w['x1'])/2<b))
  if cells[0].casefold().startswith('total'):break
  rows.append(cells)
 return rows

def _parse_province_table(table,*,category,period,report,sha,page,time_basis,context_text=''):
 found=_province_header(table)
 if not found:raise SouthAfricaNICDNMCContractError('Province column headers not verified')
 # Some Word-generated PDFs have phantom line intersections that produce
 # dozens of empty columns. The printed nonempty cells still form exactly
 # one label, nine provinces and national total. Validate that whole schema.
 original_offset,original_cols=found
 compacted_layout=len(table[original_offset])>20
 if compacted_layout:
  compacted=[[c for c in raw if _clean(c)] for raw in table]
  if _province_header(compacted):table=compacted;found=_province_header(table)
 original_offset,original_cols=found
 merged=table[:original_offset+1]
 for raw in table[original_offset+1:]:
  first=min(original_cols)
  prefix=_clean(' '.join(_clean(c) for c in raw[:first]));rest=[_clean(c) for c in raw[first:] if _clean(c)]
  previous_label=_clean(' '.join(_clean(c) for c in merged[-1][:first])) if merged else ''
  continuation=(prefix.startswith(('(', 'drug-', 'resistant', 'childbirth', 'stock ', 'remedy ', 'syndrome', 'type B', 'caused ', 'respiratory ', 'pathogen', 'paratyphoid', 'Salmonellosis')) or prefix in {'poisoning','fever)','B','outbreak'} or previous_label.endswith('-') or previous_label.count('(')>previous_label.count(')'))
  previous_rest=[_clean(c) for c in merged[-1][first:] if _clean(c)] if merged else []
  duplicate_cells=bool(rest) and rest==previous_rest
  if prefix and continuation and (not rest or duplicate_cells) and len(merged)>original_offset+1:
   index=next((i for i in range(first) if _clean(merged[-1][i])),0);merged[-1][index]=_clean(merged[-1][index])+' '+prefix
  else:merged.append(raw)
 table=merged
 offset,cols=found;last=max(cols);header=table[offset]
 totals=[i for i in range(last+1,len(header)) if re.search(r'\bTotal\b',_clean(header[i]),re.I) and not re.search('deaths',_clean(header[i]),re.I)]
 if not totals:return _parse_no_total_table(table,offset,cols,category=category,period=period,report=report,sha=sha,page=page,time_basis=time_basis,context_text=context_text)
 national=totals[0];death=next((i for i in range(national+1,min(len(header),national+3)) if re.search('death',_clean(header[i]),re.I)),None)
 previous=totals[1] if len(totals)>1 else None
 compare_period=None
 if previous is not None and period['frequency']=='monthly':
  heading=' '.join(_clean(c) for row in table[:offset] for c in row)
  months=[(MONTHS[m[1].casefold()],int(m[2])) for m in re.finditer(r'('+MONTH_PATTERN+r')\s+(20\d{2})',heading,re.I)]
  unique=list(dict.fromkeys(months))
  for month,year in unique:
   if (year,month)!=(period['year'],period['month']):compare_period={'year':year,'month':month,'frequency':'monthly','period':'comparison_notification_month','start':f'{year}-{month:02d}-01','end':date(year,month,calendar.monthrange(year,month)[1]).isoformat(),'label':f'{calendar.month_name[month]} {year}'}
 rows=[];issues=[];labels=[]
 for raw in table[offset+1:]:
  if compacted_layout and len(raw)!=len(header):
   issues.append({'page':page,'category':category,'error':'Compacted source row has missing cells; positional mapping withheld','cells':raw});continue
  if len(raw)<=national:continue
  label=_label(' '.join(_clean(c) for c in raw[:min(cols)]))
  if label.casefold().startswith(('total','grand total')):break
  if not label or label.casefold().startswith(('ec (','ec(','province')):continue
  if not any(_clean(c) for c in raw[min(cols):]):continue
  try:
   label=_verified_label(label)
   values={code:_count(raw[index]) for index,code in cols.items()};total=_count(raw[national])
   if total is None:raise SouthAfricaNICDNMCContractError('National total is missing')
   rows.append(_row(label,total,category=category,classification='all_notified',measure='cases',geography='national',period=period,report=report,sha=sha,page=page,time_basis=time_basis));labels.append(label)
   if all(v is not None for v in values.values()) and sum(values.values())!=total:raise SouthAfricaNICDNMCContractError(f'Province sum {sum(values.values())} differs from national {total}')
   for geography,value in values.items():rows.append(_row(label,value,category=category,classification='all_notified',measure='cases',geography=geography,period=period,report=report,sha=sha,page=page,time_basis=time_basis))
   if death is not None:rows.append(_row(label,_count(raw[death]),category=category,classification='patient_status_deceased',measure='deaths',geography='national',period=period,report=report,sha=sha,page=page,time_basis=time_basis))
   if previous is not None and compare_period:
    rows.append(_row(label,_count(raw[previous]),category=category,classification='all_notified',measure='cases',geography='national',period=compare_period,report=report,sha=sha,page=page,time_basis='notification_comparison'))
    if previous+1<len(raw) and re.search('death',_clean(header[previous+1]),re.I):rows.append(_row(label,_count(raw[previous+1]),category=category,classification='patient_status_deceased',measure='deaths',geography='national',period=compare_period,report=report,sha=sha,page=page,time_basis='notification_comparison'))
  except (ValueError,IndexError) as exc:issues.append({'page':page,'category':category,'label':label,'error':str(exc),'cells':raw})
 return rows,issues,labels

def _compact(text):return re.sub(r'\s+','',text.replace('￾','').replace('\xad','').replace('–','-').replace('−','-')).casefold()

def _parse_no_total_table(table,offset,cols,*,category,period,report,sha,page,time_basis,context_text=''):
 header=list(table[offset]);last=max(cols)
 # Older PDFs split column names above and below the province header.
 for index in range(last+1,len(header)):
  if not _clean(header[index]):
   header[index]=next((row[index] for row in reversed(table[:offset]) if len(row)>index and _clean(row[index]) and _clean(row[index]) not in {'Vital Status','Case Definition, n(%)','Case Type, n(%)'}),'')
 def name(value):
  value=re.sub('[^a-z]','',_clean(value).casefold())
  if value.startswith('notdeceased'):return 'not_deceased'
  if value.startswith('deceased'):return 'deceased'
  if value.startswith('alive'):return 'alive'
  if value.startswith('suspectedprobable'):return 'suspected_or_probable'
  if value.startswith('confirmed'):return 'confirmed'
  if value.startswith('clinical'):return 'clinical_notification'
  if value.startswith('laboratory'):return 'laboratory_notification'
  if value.startswith('merged'):return 'merged_notification'
  return value
 extra=[(i,name(header[i])) for i in range(last+1,len(header)) if _clean(header[i])]
 names=[v for _,v in extra]
 if len(names)==4 and set(names[:2])=={'confirmed','suspected'} and names[:2]==names[2:]:
  combined='suspectedcasesarebothsuspectedandprobable' in _compact(context_text)
  fields=[('suspected_or_probable' if n=='suspected' and combined else n,'cases' if i<2 else 'deaths') for i,n in enumerate(names)]
 elif names in [['suspected_or_probable','confirmed'],['clinical_notification','laboratory_notification','merged_notification']]:
  fields=[(n,'cases') for n in names]
 elif names in [['alive','deceased'],['not_deceased','deceased']]:
  fields=[('patient_status_'+names[0],'cases'),('patient_status_deceased','deaths')]
 else:raise SouthAfricaNICDNMCContractError(f'No-total national column schema unverified: {names}')
 rows=[];issues=[];labels=[]
 for raw in table[offset+1:]:
  label=_label(' '.join(_clean(c) for c in raw[:min(cols)]))
  if label.casefold().startswith(('total','grand')):break
  if not label or label.casefold().startswith(('1n','*','province')):continue
  if not any(_clean(c) for c in raw[min(cols):]):continue
  if not any(_clean(raw[index]) for index in cols if index<len(raw)):continue
  try:
   label=_verified_label(label)
   values={code:_count(raw[index]) for index,code in cols.items()}
   for geography,value in values.items():rows.append(_row(label,value,category=category,classification='all_notified',measure='cases',geography=geography,period=period,report=report,sha=sha,page=page,time_basis=time_basis))
   for (index,_),(classification,measure) in zip(extra,fields):
    rows.append(_row(label,_count(raw[index]),category=category,classification=classification,measure=measure,geography='national',period=period,report=report,sha=sha,page=page,time_basis=time_basis))
   labels.append(label)
  except (ValueError,IndexError) as exc:issues.append({'page':page,'category':category,'label':label,'error':str(exc),'cells':raw})
 return rows,issues,labels

def _parse_classification_percent(text,labels,*,category,period,report,sha,page,time_basis):
 compact=_compact(text);matches=[];candidates=[]
 for label in labels:
  needle=_compact(_label(label));start=0
  while (pos:=compact.find(needle,start))>=0:
   candidates.append((pos,label,len(needle)));start=pos+len(needle)
 for label in labels:
  eligible=[item for item in candidates if item[1]==label and not any(other[0]<=item[0] and item[0]+item[2]<=other[0]+other[2] and other[2]>item[2] for other in candidates)]
  if eligible:matches.append(min(eligible))
 matches.sort();rows=[];issues=[]
 token=r'(\d[\d,]*)\((?:[<>]?\d+(?:[.,]\d+)?%?|n/a)\)'
 for i,(start,label,length) in enumerate(matches):
  chunk=compact[start+length:matches[i+1][0] if i+1<len(matches) else len(compact)]
  values=re.findall(token,chunk)
  expected=10 if re.search(r'Asymptomatic\s+carrier',text,re.I) else 9
  if len(values)<expected:
   issues.append({'page':page,'category':category,'label':label,'error':'Classification/status percentage row has unverified column width','width':len(values)});continue
  counts=[int(v.replace(',','')) for v in values[:expected]];total=counts[-1]
  fields=[('clinical_unclassified','cases'),('laboratory_unclassified','cases'),('confirmed','cases'),('probable','cases'),('suspected','cases'),('patient_status_alive','cases'),('patient_status_deceased','deaths'),('patient_status_unknown','cases')]
  if expected==10:fields.insert(3,('asymptomatic_carrier','cases'))
  split=expected-4
  for indices,name in [(range(split),'epidemiological classification'),(range(split,split+3),'patient status')]:
   if sum(counts[j] for j in indices)!=total:
    issues.append({'page':page,'category':category,'label':label,'error':f'{name} counts do not reconcile with table total','values':counts});continue
   for j in indices:
    c,m=fields[j];rows.append(_row(label,counts[j],category=category,classification=c,measure=m,geography='national',period=period,report=report,sha=sha,page=page,time_basis=time_basis,cohort='classification_table'))
 return rows,issues

def _parse_classification_plain(pdf_page,labels,*,category,period,report,sha,page,time_basis):
 words=pdf_page.extract_words(x_tolerance=2,y_tolerance=3)
 tokens=['Clinical','Laboratory','Confirmed','Probable','Suspected','Total','Alive','Deceased','Unknown']
 clinical=next((w for w in words if w['text']=='Clinical'),None)
 if clinical is None:return [],[{'page':page,'error':'Plain classification header missing Clinical'}]
 heads=[]
 for token in tokens:
  found=[w for w in words if w['text']==token and abs(w['top']-clinical['top'])<20]
  if len(found)!=1:return [],[{'page':page,'error':'Plain classification header is not unique','column':token}]
  heads.append(found[0])
 centres=[(w['x0']+w['x1'])/2 for w in heads]
 if centres!=sorted(centres):return [],[{'page':page,'error':'Plain classification column order changed'}]
 bounds=[centres[0]-(centres[1]-centres[0])/2]+[(a+b)/2 for a,b in zip(centres,centres[1:])]+[pdf_page.width]
 top=max(w['bottom'] for w in heads);anchors=[]
 for w in words:
  x=(w['x0']+w['x1'])/2;y=(w['top']+w['bottom'])/2
  if w['top']>top and bounds[0]<=x<bounds[1] and re.fullmatch(r'[\d,]+|[-—]|n/a',w['text'],re.I) and all(abs(y-a)>4 for a in anchors):anchors.append(y)
 anchors.sort();rows=[];issues=[];known={_compact(_label(label)):_label(label) for label in labels}
 for i,y in enumerate(anchors):
  lo=y-5 if i==0 else (anchors[i-1]+y)/2;hi=(y+anchors[i+1])/2 if i+1<len(anchors) else y+10
  line=[w for w in words if lo<=(w['top']+w['bottom'])/2<hi]
  label=_label(' '.join(w['text'] for w in sorted(line,key=lambda w:(round(w['top']/4),w['x0'])) if w['x1']<=bounds[0]+2))
  if label.casefold().startswith('total'):break
  if _compact(label) not in known:
   issues.append({'page':page,'label':label,'error':'Plain classification label absent from verified province roster'});continue
  label=known[_compact(label)];cells=[]
  for a,b in zip(bounds,bounds[1:]):cells.append(' '.join(w['text'] for w in sorted(line,key=lambda w:w['x0']) if a<=(w['x0']+w['x1'])/2<b))
  try:values=[_count(c) for c in cells]
  except ValueError as exc:issues.append({'page':page,'label':label,'error':str(exc),'cells':cells});continue
  fields=[('clinical_unclassified','cases'),('laboratory_unclassified','cases'),('confirmed','cases'),('probable','cases'),('suspected','cases'),('patient_status_alive','cases'),('patient_status_deceased','deaths'),('patient_status_unknown','cases')]
  counts=values[:5]+values[6:]+[values[5]];total=counts[8]
  for indices,name in [(range(5),'classification'),(range(5,8),'patient status')]:
   if any(counts[j] is None for j in indices):issues.append({'page':page,'label':label,'error':f'{name} contains explicit blank cells; retained as unknown','cells':cells})
   elif sum(counts[j] for j in indices)!=total:issues.append({'page':page,'label':label,'error':f'{name} counts do not reconcile with table total','values':counts});continue
   for j in indices:
    c,m=fields[j];rows.append(_row(label,counts[j],category=category,classification=c,measure=m,geography='national',period=period,report=report,sha=sha,page=page,time_basis=time_basis,cohort='classification_table'))
 return rows,issues

def parse_report(payload,report):
 if payload.startswith(b'PK'):return _parse_docx_report(payload,report)
 pages=extract_pdf_pages(payload);period=report_period(pages,report)
 window_text=_clean(' '.join(pages[:3]));window=re.search(r'[^.]{0,180}\bISO\s*weeks?[^.]{0,100}',window_text,re.I)
 if window:period['window_detail']=window[0].strip()
 report=dict(report,report_year=period['year'],report_month=period['month']);sha=hashlib.sha256(payload).hexdigest();time_basis=_time_basis(pages)
 import pdfplumber
 rows=[];issues=[];labels_by_category={};classification=[];tables_seen=0;section_category=None
 with pdfplumber.open(io.BytesIO(payload)) as doc:
  for i,text in enumerate(pages):
   section=re.findall(r'^(?:NMC\s+)?Category\s*([123])\s+(?:notifications|conditions)',text,re.I|re.M)
   if section:section_category=section[-1]
   category=_category(text) or section_category
   prefix=re.split(r'\bEC\s*1?\s+FS',text,maxsplit=1)[0]
   declared=set(re.findall(r'Table\s*\d+[.:]?[^\n]{0,100}?(?:Category|category)\s*([123])\b',prefix))
   declared.update(re.findall(r'^(?:NMC\s+)?(?:Distribution of\s+)?Category\s*([123])\s+(?:NMC|notifications|conditions)',prefix,re.I|re.M))
   if len(declared)>1:
    issues.append({'page':i+1,'error':'Source category heading and table caption disagree; table withheld','categories':sorted(declared)});continue
   if not category:continue
   if _category(text) and re.search('province',text,re.I):section_category=category
   if re.search(r'epidemiological\s+classification.*?(?:vital|patient)\s+(?:vital\s+)?status',text,re.I|re.S):classification.append((i,text,category));continue
   continuation=re.search(r'Condition\s+EC\s*1?\s+FS',text,re.I) and re.search('Deaths',text,re.I)
   if not (re.search(r'Category\s*'+category+r'.*(?:province|NMC)',text,re.I) or re.search(r'Table\s*\d+[.:]\s*The number of notifications by province',text,re.I) or continuation):continue
   if re.search('Back.capture|Appendix',text,re.I):
    issues.append({'page':i+1,'error':'Back-captured or appendix table retained separately in raw; historical diagnosis window not verified'});continue
   if not all(code in text for code in ['EC','FS','GP','WC']):continue
   tables_seen+=1;tables=doc.pages[i].extract_tables();valid=[t for t in tables if _province_header(t)]
   if not valid:
    fallback=_coordinate_table(doc.pages[i]);valid=[fallback] if fallback else []
   if not valid:issues.append({'page':i+1,'category':category,'error':'No complete verified province table layout'});continue
   for table in valid:
    try:
     parsed,errors,labels=_parse_province_table(table,category=category,period=period,report=report,sha=sha,page=i+1,time_basis=time_basis,context_text=text);rows.extend(parsed);issues.extend(errors);labels_by_category.setdefault(category,[]).extend(labels)
    except ValueError as exc:issues.append({'page':i+1,'category':category,'error':str(exc)})
 with pdfplumber.open(io.BytesIO(payload)) as doc:
  for i,text,category in classification:
   labels=labels_by_category.get(category,[])
   if not labels:issues.append({'page':i+1,'category':category,'error':'Classification table has no verified source disease roster'});continue
   if re.search(r'\(\d+(?:[.,]\d+)?%\)',text):parsed,errors=_parse_classification_percent(text,labels,category=category,period=period,report=report,sha=sha,page=i+1,time_basis=time_basis)
   else:parsed,errors=_parse_classification_plain(doc.pages[i],labels,category=category,period=period,report=report,sha=sha,page=i+1,time_basis=time_basis)
   rows.extend(parsed);issues.extend(errors)
 if not rows:raise SouthAfricaNICDNMCContractError('No independently verified NMC disease table; raw report retained')
 return rows,{'year':period['year'],'month':period['month'],'frequency':period['frequency'],'period_label':period['label'],'time_basis':time_basis,'province_tables_seen':tables_seen,'parsed_categories':sorted(labels_by_category),'issues':issues,'coverage':'Verified national/province notification tables and reconciling national classification/vital-status rows; percentages, demographics, timeliness, back-capture and charts are distinct raw evidence.'}

def _parse_docx_report(payload,report):
 import zipfile
 from lxml import etree
 ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
 root=etree.fromstring(zipfile.ZipFile(io.BytesIO(payload)).read('word/document.xml'))
 body=root.find('w:body',ns)
 paragraphs=[' '.join(el.xpath('.//w:t/text()',namespaces=ns)) for el in body if el.tag.endswith('}p')]
 period=report_period([' '.join(paragraphs)[:9000]],report)
 report=dict(report,report_year=period['year'],report_month=period['month'])
 sha=hashlib.sha256(payload).hexdigest();time_basis=_time_basis(paragraphs[:30]);rows=[];issues=[];categories=set();context=[];table_number=0
 for element in body:
  if element.tag.endswith('}p'):
   context.append(' '.join(element.xpath('.//w:t/text()',namespaces=ns)));continue
  if not element.tag.endswith('}tbl'):continue
  table_number+=1
  table=[[' '.join(c.xpath('.//w:t/text()',namespaces=ns)) for c in tr.findall('w:tc',ns)] for tr in element.findall('w:tr',ns)]
  if not _province_header(table):continue
  caption=' '.join(context[-3:]);category=_category(caption)
  if not category or 'Back captured' in caption or 'Appendix' in caption:continue
  try:
   parsed,errors,_=_parse_province_table(table,category=category,period=period,report=report,sha=sha,page=f'docx_table_{table_number}',time_basis=time_basis,context_text=caption)
   rows.extend(parsed);issues.extend(errors);categories.add(category)
  except ValueError as exc:issues.append({'table':table_number,'error':str(exc)})
 if not rows:raise SouthAfricaNICDNMCContractError('No verified DOCX NMC tables')
 return rows,{'year':period['year'],'month':period['month'],'frequency':period['frequency'],'period_label':period['label'],'time_basis':time_basis,'parsed_categories':sorted(categories),'issues':issues,'coverage':'Source DOCX province and case-definition tables; alternate original of October 2023 publication.'}

class SouthAfricaNICDNMCCrawler(BaseCrawler):
 def __init__(self,**kwargs):super().__init__(timeout=60,delay=0.15,**kwargs)
 async def crawl(self,**kwargs):raise NotImplementedError('Use durable ZA staged collection')
 def parse(self,response):return [CrawlerResult(title=DEFAULT_SOURCE_NAME,url=response.url,raw_data={'raw_bytes':response.content})]
