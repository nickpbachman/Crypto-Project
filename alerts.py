import asyncio, json, os, time, hashlib
from pathlib import Path
import httpx
from pywebpush import webpush, WebPushException
from .store import conn
from .engine import analyze

SCAN_INTERVAL = int(os.getenv('ALERT_SCAN_INTERVAL', '300'))
COINGECKO = 'https://api.coingecko.com/api/v3/coins/markets'
PARAMS = {'vs_currency':'usd','order':'market_cap_desc','per_page':100,'page':1,'sparkline':'false','price_change_percentage':'1h,24h,7d'}


def init_alerts():
    with conn() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS push_subscriptions (
            endpoint TEXT PRIMARY KEY, p256dh TEXT NOT NULL, auth TEXT NOT NULL, sensitivity TEXT NOT NULL DEFAULT 'major', created_ts INTEGER NOT NULL, last_seen_ts INTEGER NOT NULL
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS alert_events (
            event_key TEXT PRIMARY KEY, coin_id TEXT NOT NULL, symbol TEXT NOT NULL, name TEXT NOT NULL,
            level TEXT NOT NULL, score INTEGER NOT NULL, summary TEXT NOT NULL, ts INTEGER NOT NULL, payload TEXT NOT NULL
        )''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_alert_events_ts ON alert_events(ts DESC)')


def save_subscription(sub):
    endpoint=sub.get('endpoint'); keys=sub.get('keys') or {}; sensitivity=sub.get('sensitivity','major')
    if sensitivity not in ('major','significant','watch'): sensitivity='major'
    if not endpoint or not keys.get('p256dh') or not keys.get('auth'): raise ValueError('Invalid push subscription')
    now=int(time.time())
    with conn() as c:
        c.execute('INSERT OR REPLACE INTO push_subscriptions(endpoint,p256dh,auth,sensitivity,created_ts,last_seen_ts) VALUES(?,?,?,?,?,?)',
                  (endpoint,keys['p256dh'],keys['auth'],sensitivity,now,now))


def remove_subscription(endpoint):
    with conn() as c: c.execute('DELETE FROM push_subscriptions WHERE endpoint=?',(endpoint,))


def list_subscriptions():
    with conn() as c: return [dict(r) for r in c.execute('SELECT * FROM push_subscriptions').fetchall()]


def recent_events(limit=30):
    with conn() as c:
        rows=c.execute('SELECT payload FROM alert_events ORDER BY ts DESC LIMIT ?',(limit,)).fetchall()
    return [json.loads(r['payload']) for r in rows]


def severity(score, metrics, mode='major'):
    h1=abs(metrics.get('h1',0)); vz=abs(metrics.get('volume_z',0)); tz=abs(metrics.get('turnover_z',0))
    corroborated=h1>=2 and (vz>=2 or tz>=2)
    if score>=85 and corroborated and vz>=3.5: return 'MAJOR'
    if mode in ('significant','watch') and score>=75 and corroborated: return 'SIGNIFICANT'
    if mode=='watch' and score>=30: return 'WATCH'
    return None


def event_key(coin, level, metrics):
    bucket=int(time.time()//3600)
    raw=f"{coin['id']}|{level}|{bucket}|{round(metrics.get('h1',0),1)}|{round(metrics.get('volume_z',0),1)}|{round(metrics.get('turnover_z',0),1)}"
    return hashlib.sha256(raw.encode()).hexdigest()


def make_summary(analysis):
    sigs=analysis.get('signals') or []
    return ' · '.join(sigs[:3]) or 'Multiple unusual market signals detected.'


def record_event(coin, level, analysis):
    key=event_key(coin,level,analysis.get('metrics') or {})
    payload={'id':key,'coin':{'id':coin['id'],'name':coin['name'],'symbol':coin['symbol']},'level':level,
             'score':int(analysis.get('score',0)),'summary':make_summary(analysis),'timestamp':int(time.time()),
             'metrics':analysis.get('metrics') or {},'signals':analysis.get('signals') or []}
    with conn() as c:
        exists=c.execute('SELECT 1 FROM alert_events WHERE event_key=?',(key,)).fetchone()
        if exists: return None
        c.execute('INSERT INTO alert_events(event_key,coin_id,symbol,name,level,score,summary,ts,payload) VALUES(?,?,?,?,?,?,?,?,?)',
                  (key,coin['id'],coin['symbol'],coin['name'],level,payload['score'],payload['summary'],payload['timestamp'],json.dumps(payload)))
    return payload


def push_payload(payload):
    pub=os.getenv('VAPID_PUBLIC_KEY'); priv=os.getenv('VAPID_PRIVATE_KEY'); subject=os.getenv('VAPID_SUBJECT','mailto:alerts@example.com')
    if not pub or not priv: return 0
    body=json.dumps(payload); sent=0
    rank={'WATCH':1,'SIGNIFICANT':2,'MAJOR':3}; level=payload.get('data',{}).get('level','MAJOR')
    for sub in list_subscriptions():
        if rank.get(level,3) < rank.get(sub.get('sensitivity','major').upper(),3): continue
        try:
            webpush(subscription_info={'endpoint':sub['endpoint'],'keys':{'p256dh':sub['p256dh'],'auth':sub['auth']}}, data=body, vapid_private_key=priv, vapid_claims={'sub':subject}, ttl=3600)
            sent+=1
        except WebPushException as e:
            status=getattr(getattr(e,'response',None),'status_code',None)
            if status in (404,410): remove_subscription(sub['endpoint'])
        except Exception:
            pass
    return sent


async def scan_once():
    async with httpx.AsyncClient(timeout=30, headers={'User-Agent':'WhatsHappening/21'}) as client:
        r=await client.get(COINGECKO,params=PARAMS); r.raise_for_status(); coins=r.json()
    ts=int(time.time())
    rows=[]
    for c in coins:
        rows.append((c.get('id'),c.get('symbol',''),c.get('name',''),ts,float(c.get('current_price') or 0),float(c.get('market_cap') or 0),float(c.get('total_volume') or 0),float(c.get('price_change_percentage_1h_in_currency') or 0),float(c.get('price_change_percentage_24h_in_currency') or 0),float(c.get('price_change_percentage_7d_in_currency') or 0)))
    from .store import insert_many, history
    insert_many(rows)
    market=[{'volume':r[6],'market_cap':r[5],'h1':r[7],'h24':r[8],'h7':r[9]} for r in rows]
    results=[]
    for c,r in zip(coins,rows):
        cur={'coin_id':r[0],'symbol':r[1],'name':r[2],'ts':r[3],'price':r[4],'market_cap':r[5],'volume':r[6],'h1':r[7],'h24':r[8],'h7':r[9]}
        a=analyze(cur,history(c['id'],101)[1:],market)
        results.append((c,a))
    results.sort(key=lambda x:x[1].get('score',0),reverse=True)
    generated=[]
    for c,a in results[:15]:
        levels=[]
        for mode in ('major','significant','watch'):
            level=severity(a.get('score',0),a.get('metrics') or {},mode)
            if level and level not in levels: levels.append(level)
        for level in levels:
            p=record_event(c,level,a)
            if p:
                generated.append(p); push_payload({'title':f"{p['level']} ANOMALY · {c['name']} ({c['symbol'].upper()})",'body':p['summary'],'tag':p['id'],'data':p})
    return {'timestamp':ts,'assets':len(coins),'top_score':results[0][1].get('score',0) if results else 0,'new_alerts':generated}


async def worker(stop_event):
    await asyncio.sleep(2)
    while not stop_event.is_set():
        try: await scan_once()
        except Exception as e: print('alert scan error:',repr(e),flush=True)
        try: await asyncio.wait_for(stop_event.wait(),timeout=SCAN_INTERVAL)
        except asyncio.TimeoutError: pass
