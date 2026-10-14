"""HYROX France watcher: official France race directory -> data.json.
Never guesses ticket-opening times. Notifies via ntfy on substantive changes.
"""
import json, os, re, sys, urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup

DIRECTORY = 'https://hyroxfrance.com/fr/trouve-ta-course/?filter_region=france'
BASE = 'https://hyroxfrance.com'
DATA = Path('data.json')
FR_TZ = ZoneInfo('Europe/Paris')
MONTHS = {'janvier':1,'février':2,'fevrier':2,'mars':3,'avril':4,'mai':5,'juin':6,'juillet':7,'août':8,'aout':8,'septembre':9,'octobre':10,'novembre':11,'décembre':12,'decembre':12}
HEADERS = {'User-Agent':'Mozilla/5.0 (compatible; HYROX-FR-Registration-Watcher/1.0)'}

def get(url):
    response = requests.get(url,timeout=25,headers=HEADERS)
    response.raise_for_status()
    return BeautifulSoup(response.text,'html.parser')

def cleaned(s):
    return ' '.join(s.split()).strip()

def parse_opening(text):
    # ONLY sentences tied to ticket sales; never race-day dates.
    sentences = re.split(r'(?<=[.!?])\s+|\n+',text)
    candidates=[]
    for s in sentences:
        lower=s.lower()
        sale_word = re.search(r'billetterie|vente des billets|ventes des billets|inscriptions?\s+(?:ouvrent|ouvriront|débutent)|tickets?\s+(?:en vente|disponibles)',lower)
        opening_word = re.search(r'ouvre|ouvrira|ouverture|début|débutera|commence|commencera|à partir|dès le|seront disponibles|en vente le', lower)
        if sale_word and opening_word:
            candidates.append(s)
    # Accept date and time only together. Date alone is displayed without false precise countdown.
    for sentence in candidates:
        m = re.search(r'\b([0-3]?\d)\s+(janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre)\s+(20\d{2})\b',sentence,re.I)
        if not m: continue
        day,month,year=int(m[1]),MONTHS[m[2].lower()],int(m[3])
        tm = re.search(r'\b(?:à|dès)\s+(\d{1,2})\s*(?:h|:)\s*(\d{2})?\b',sentence,re.I)
        try:
            if tm:
                hour=int(tm[1]);minute=int(tm[2] or '0')
                iso=datetime(year,month,day,hour,minute,tzinfo=FR_TZ).isoformat()
                return iso, None
            return None, f'{day:02d}/{month:02d}/{year}'
        except ValueError: continue
    return None,None

def event_info(url):
    soup=get(url)
    title=soup.find('h1')
    name=cleaned(title.get_text(' ',strip=True)) if title else url.rstrip('/').split('/')[-1].replace('-',' ').upper()
    # Text from body, not hidden scripts. Strip irrelevant ticket terms in long FAQ if possible.
    for tag in soup(['script','style','noscript','footer','nav']): tag.decompose()
    paragraphs=[cleaned(p.get_text(' ',strip=True)) for p in soup.select('p,h1,h2,h3')]
    lines='\n'.join(x for x in paragraphs if x)
    leading=' '.join(paragraphs[:28]).lower()
    opening_at,opening_day=parse_opening(lines)
    # Keywords checked on introductory text only; don't confuse race check-in with ticket sales.
    if opening_at or opening_day: status='announced'
    elif re.search(r'(?:billetterie\s+ouvre\s+bientôt|ouverture\s+de\s+la\s+billetterie\s+approche|ticket sales (?:open|start) soon|inscri\w*[- ]toi\s+sur\s+la\s+liste\s+d.attente)',leading): status='soon'
    elif re.search(r'(?:billetterie\s+est\s+ouverte|billets\s+(?:sont\s+)?en\s+vente|tickets?\s+on\s+sale\s+now)',leading): status='open'
    else: status='unknown'
    # Keep date label if one is available; don't turn race date into ticket date.
    return {'name':name,'url':url,'status':status,'opening_at':opening_at,'opening_day':opening_day}

def discover():
    soup=get(DIRECTORY)
    urls=[]
    for a in soup.find_all('a',href=True):
        raw=urllib.parse.urljoin(BASE,a['href'])
        u=urllib.parse.urlsplit(raw)
        if u.netloc not in ('hyroxfrance.com','www.hyroxfrance.com'): continue
        if not re.fullmatch(r'/fr/event/[^/]+/?',u.path): continue
        if 'youngstars' in u.path.lower():continue
        clean=urllib.parse.urlunsplit((u.scheme,u.netloc,u.path,'',''))
        if clean not in urls:urls.append(clean)
    if not urls: raise RuntimeError('No event URLs found: site markup may have changed; refusing to erase data')
    return urls

def notify(title,message,url=''):
    topic=os.environ.get('NTFY_TOPIC','').strip()
    if not topic:return
    if not re.fullmatch(r'[A-Za-z0-9_-]{12,}',topic):
        print('NTFY_TOPIC invalid: must be 12+ letters/digits/_/-',file=sys.stderr);return
    try:
        hdr={'Title':title.encode('ascii','ignore').decode('ascii') or 'HYROX France','Priority':'high','Tags':'trophy'}
        if url: hdr['Click']=url
        r=requests.post('https://ntfy.sh/'+topic,data=message.encode('utf-8'),headers=hdr,timeout=15)
        r.raise_for_status()
    except Exception as e: print('Notification failed:',e,file=sys.stderr)

def main():
    previous=json.loads(DATA.read_text()) if DATA.exists() else {'events':[]}
    old={e['url']:e for e in previous.get('events',[])}
    urls=discover();events=[];errors=[]
    for url in urls:
        try:events.append(event_info(url))
        except Exception as e:
            print('WARN',url,repr(e),file=sys.stderr);errors.append(url)
            if url in old: events.append(old[url])
    if not events: raise RuntimeError('No event page parsed; preserving previous data')
    # New event announcements and status changes only; on initial run no mass notifications.
    if old:
        for e in events:
            before=old.get(e['url'])
            if not before:
                notify('Nouvel HYROX France',e['name']+' ajouté au calendrier',e['url'])
            elif any(before.get(k)!=e.get(k) for k in ('status','opening_at','opening_day')):
                value=e['opening_at'] or e['opening_day'] or e['status']
                notify('Billetterie HYROX',e['name']+' : '+str(value),e['url'])
    output={'updated_at':datetime.now(timezone.utc).isoformat(),'source':DIRECTORY,'events':events,'failed_pages':errors}
    DATA.write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
    print('Updated',len(events),'events;',len(errors),'errors')

if __name__=='__main__':main()
