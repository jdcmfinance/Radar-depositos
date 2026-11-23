#!/usr/bin/env python3
"""RadarDepósitos: verificación horaria de fuentes y detección opcional con IA.

- Actualización automática SOLO de TAE verificadas en webs primarias conocidas.
- Descubrimiento de candidatos con OpenAI: cola de revisión, NO publicación directa.
- La ausencia de respuesta, un captcha o una página bloqueada NO implica retirar una oferta.
- Nunca se reciben ni almacenan claves bancarias.
"""
import json, os, re, sys, ssl, smtplib, unicodedata
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parent
DB=ROOT/'data'/'offers.json'
PENDING=ROOT/'data'/'pending.json'
STATE=ROOT/'data'/'monitor_state.json'
NOW=datetime.now(timezone.utc).isoformat(timespec='seconds')
DATE=NOW[:10]
HEADERS={'User-Agent':'RadarDepositosResearch/1.0 (+non-commercial hourly public-offers comparison)','Accept-Language':'es-ES,es;q=0.9','Accept':'text/html'}
SOURCES={
 'renault':{'url':'https://renaultbank.es/','name':'Renault Bank','primary':True},
 'hoist':{'url':'https://www.hoistspar.es/','name':'HoistSpar','primary':True},
 'raisin':{'url':'https://www.raisin.com/es-es/depositos-a-plazo-fijo/','name':'Raisin','primary':False},
 'myinvestor':{'url':'https://myinvestor.es/depositos/','name':'MyInvestor','primary':False},
}

def compact(txt):return ' '.join(txt.split())
def norm(txt):
 txt=unicodedata.normalize('NFKD',txt)
 return compact(''.join(c for c in txt if not unicodedata.combining(c)).lower())
def number(v):return float(str(v).strip().replace('.','').replace(',','.')) if ',' in str(v) else float(v)
def get(url):
 if urlparse(url).scheme!='https':raise ValueError('Solo URLs HTTPS')
 r=requests.get(url,timeout=22,headers=HEADERS,allow_redirects=True)
 r.raise_for_status()
 if 'html' not in r.headers.get('content-type','html'):raise ValueError('Respuesta no HTML')
 if len(r.content)>2_000_000:raise ValueError('Página demasiado grande')
 soup=BeautifulSoup(r.text,'html.parser')
 for node in soup(['script','style','noscript','svg']):node.decompose()
 return compact(soup.get_text(' ',strip=True))

def extract_hoist(text):
 found={}
 for days,rate in re.findall(r'\b(90|180|365|730)\s+d[ií]as\s+(\d{1,2}[\.,]\d{1,2})\s*%\s*TAE',text,re.I):
  months={'90':3,'180':6,'365':12,'730':24}[days]
  pos=text.lower().find(days+' días')
  # No inventar TIN si no está al lado de la oferta.
  tail=text[pos:pos+130] if pos>=0 else ''
  tinm=re.search(r'(?:Dep[oó]sito a \d+ meses)\s+(\d{1,2}[\.,]\d{1,2})\s*%\s*TIN',tail,re.I)
  found[months]={'tae':number(rate),'tin':number(tinm.group(1)) if tinm else None}
 return found

def extract_renault(text):
 found={}
 pattern=r'\b(12|24|36)\s*meses\s+Dep[oó]sito\s*T[uú]\s*\+\s*(\d{1,2}[\.,]\d{1,2})\s*%\s*TAE'
 for match in re.finditer(pattern,text,re.I):
  months,rate=int(match.group(1)),number(match.group(2))
  fragment=text[match.end():match.end()+850]
  tinm=re.search(r'Tipo de inter[eé]s nominal anual\s*:?\s*(\d{1,2}[\.,]\d{1,2})\s*%',fragment,re.I)
  expiry=re.search(r'Condiciones v[aá]lidas hasta el\s+(\d{2})/(\d{2})/(\d{4})',fragment,re.I)
  ends=f'{expiry.group(3)}-{expiry.group(2)}-{expiry.group(1)}' if expiry else None
  found[months]={'tae':rate,'tin':number(tinm.group(1)) if tinm else None,'offer_end':ends}
 return found

def offers_by_source(content,parser):
 if not content:return {}
 return parser(content)

def review_with_ai(label,text,source_url):
 """Extrae *candidatos*; no altera el ranking automáticamente.
      OPENAI_API_KEY opcional. La extracción nunca debe interpretarse como confirmación bancaria."""
 key=os.environ.get('OPENAI_API_KEY','').strip()
 if not key:return []
 prompt=("Extrae ofertas de depósitos a plazo fijo CONTRATABLES por residentes en España, solo si están "
         "literalmente indicadas en el texto siguiente. No incluyas cuentas remuneradas, noticias antiguas, "
         "productos sin plazo explícito ni cifras genéricas de marketing. Devuelve objeto JSON con 'offers' "
         "(lista), cada uno con bank, months (entero), tae (número), evidence (cita corta "
         "literal de la página que contenga el tipo), notes (breve). No inventes requisitos ni mínimos. "
         "Si una fuente solo permite descubrir un posible cambio, marca candidato, no oferta verificada.\n"
         f"FUENTE {label} {source_url}\nTEXTO:\n{text[:35000]}")
 payload={'model':os.environ.get('OPENAI_MODEL','gpt-4.1-mini'),
          'messages':[{'role':'system','content':'Eres extractor cauteloso de anuncios bancarios. Solo observación, no asesoramiento. JSON válido sin markdown.'},
                      {'role':'user','content':prompt}], 'response_format':{'type':'json_object'},'temperature':0}
 try:
  r=requests.post('https://api.openai.com/v1/chat/completions',headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},json=payload,timeout=45)
  r.raise_for_status()
  obj=json.loads(r.json()['choices'][0]['message']['content'])
  results=[]
  for item in obj.get('offers',[])[:30]:
   bank=compact(str(item.get('bank','')))[:100]
   months=int(item.get('months',0));tae=float(item.get('tae',0))
   evidence=compact(str(item.get('evidence','')))[:250]
   if not (bank and 1<=months<=120 and 0.1<=tae<=12 and evidence):continue
   # La evidencia literal debe aparecer en la página recuperada (tolerante al formato de espacios).
   if norm(evidence) not in norm(text):continue
   rate_patterns=[f'{tae:.2f}'.replace('.',','),f'{tae:.2f}',str(tae).replace('.',',')]
   if not any(k in evidence for k in rate_patterns):continue
   results.append({'bank':bank,'months':months,'tae':tae,'evidence':evidence,
                   'notes':str(item.get('notes',''))[:260],'source_url':source_url,
                   'detected_at':NOW,'status':'pendiente_revision'})
  return results
 except Exception as exc:
  print(f'AVISO: IA no disponible para {label}: {exc}',file=sys.stderr)
  return []

def notify(changes,errors):
 host=os.getenv('SMTP_HOST');user=os.getenv('SMTP_USER');pwd=os.getenv('SMTP_PASSWORD');target=os.getenv('ALERT_EMAIL')
 if not all([host,user,pwd,target]) or not changes:return
 msg=EmailMessage();msg['Subject']='RadarDepósitos: cambios de TAE detectados';msg['From']=user;msg['To']=target
 msg.set_content('Cambios detectados en las páginas oficiales:\n\n'+'\n'.join(changes)+'\n\nRevisa la web de la entidad antes de contratar.')
 try:
  with smtplib.SMTP_SSL(host,int(os.getenv('SMTP_PORT') or '465'),context=ssl.create_default_context(),timeout=20) as smtp:
   smtp.login(user,pwd);smtp.send_message(msg)
 except Exception as exc:print(f'AVISO: no se pudo enviar aviso: {exc}',file=sys.stderr)

def main():
 db=json.loads(DB.read_text(encoding='utf-8'))
 state=json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {'sources':{}}
 pending=json.loads(PENDING.read_text(encoding='utf-8')) if PENDING.exists() else {'candidates':[]}
 errors=[];changes=[];success=0
 for name,source in SOURCES.items():
  try:
   raw=get(source['url']);success+=1
   state['sources'][name]={'checked_at':NOW,'result':'ok','url':source['url']}
   if source['primary']:
    values=offers_by_source(raw,extract_renault if name=='renault' else extract_hoist)
    if not values:raise ValueError('No se encontró ninguna TAE verificable en el formato esperado')
    for o in db['offers']:
     if o['bank']!=source['name'] or o['months'] not in values:continue
     candidate=values[o['months']]
     old=float(o['tae']);new=candidate['tae']
     # Bloqueo de anomalías: grandes oscilaciones necesitan revisión manual.
     if abs(new-old)>1:
      pending['candidates'].append({'bank':o['bank'],'months':o['months'],'tae':new,'old_tae':old,
         'source_url':source['url'],'status':'pendiente_revision','detected_at':NOW,
         'notes':'Cambio > 1 punto porcentual; no se ha autopublicado.'})
      continue
     if abs(new-old)>=0.005:
      o['tae']=new;o['tin']=candidate['tin']
      change=f"{o['bank']} · {o['months']} meses: {old:.2f}% → {new:.2f}% TAE"
      changes.append(change)
      db['events'].append({'bank':o['bank'],'months':o['months'],'message':f"{old:.2f}% → {new:.2f}% TAE",'at':NOW})
     elif candidate['tin'] is not None:
      o['tin']=candidate['tin']
     if candidate.get('offer_end'):o['offer_end']=candidate['offer_end']
     o['observed_at']=DATE
   else:
    if name=='raisin':
     for item in review_with_ai(source['name'],raw,source['url']):
      matched=next((o for o in db['offers'] if norm(o['bank'])==norm(item['bank']) and o['months']==item['months']),None)
      if matched and abs(matched['tae']-item['tae'])<0.005:continue
      # Evitar repeticiones idénticas en el histórico de pendientes.
      if not any(norm(q.get('bank',''))==norm(item['bank']) and q.get('months')==item['months'] and q.get('tae')==item['tae'] for q in pending['candidates']):pending['candidates'].append(item)
  except Exception as exc:
   msg=f'{source["name"]}: {exc}';errors.append(msg)
   state['sources'][name]={'checked_at':NOW,'result':'error','detail':str(exc)[:300],'url':source['url']}
 db['meta']['last_checked']=NOW
 db['meta']['monitor_enabled']=True
 db['meta']['monitor_details']=f'{success} fuentes accesibles, {len(errors)} fallos, {len(changes)} cambios primarios publicados'
 if success:db['meta']['last_success']=NOW
 db['events']=db['events'][-80:]
 pending['candidates']=pending['candidates'][-150:]
 DB.write_text(json.dumps(db,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 PENDING.write_text(json.dumps(pending,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 STATE.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 notify(changes,errors)
 print(f'{NOW}: {success} fuentes, {len(changes)} cambios, {len(errors)} errores; {len(pending["candidates"])} candidatos pendientes.')
 for error in errors:print('AVISO: '+error,file=sys.stderr)
 # No abortar por fallos aislados; el estado de cada fuente se conserva en JSON.
 return 0 if success else 1
if __name__=='__main__':sys.exit(main())
