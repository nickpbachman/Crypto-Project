import time,httpx,asyncio,os
from fastapi import FastAPI,HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from store import init,insert_many,history
from engine import analyze
from alerts import init_alerts, save_subscription, remove_subscription, recent_events, scan_once, worker
app=FastAPI(title="What's Happening?",version="21.0"); init(); init_alerts()

_stop_event = asyncio.Event()
_worker_task = None

@app.on_event("startup")
async def startup_alert_worker():
    global _worker_task
    _stop_event.clear()
    _worker_task = asyncio.create_task(worker(_stop_event))

@app.on_event("shutdown")
async def shutdown_alert_worker():
    _stop_event.set()
    if _worker_task:
        await _worker_task

URL="https://api.coingecko.com/api/v3/coins/markets"
PARAMS={"vs_currency":"usd","order":"market_cap_desc","per_page":100,"page":1,"sparkline":"false","price_change_percentage":"1h,24h,7d"}

EVM_EXPLORERS={
    "ethereum":"https://eth.blockscout.com", "base":"https://base.blockscout.com",
    "arbitrum-one":"https://arbitrum.blockscout.com", "optimistic-ethereum":"https://optimism.blockscout.com",
    "polygon-pos":"https://polygon.blockscout.com", "avalanche":"https://snowtrace.io",
    "binance-smart-chain":"https://bscscan.com", "gnosis":"https://gnosis.blockscout.com",
    "celo":"https://celo.blockscout.com", "zksync":"https://zksync.blockscout.com",
    "linea":"https://linea.blockscout.com", "scroll":"https://scroll.blockscout.com"
}

async def wallet_activity(client, coin):
    symbol=(coin.get("symbol") or "").lower(); price=float((coin.get("market_data") or {}).get("current_price",{}).get("usd") or 0)
    # Bitcoin has no CoinGecko "platform" address because BTC is a native chain.
    # Use Mempool.space's public REST API to inspect recent confirmed outputs.
    # These are large transfer observations, not proof of whale intent or exchange flow.
    if symbol=="btc" or coin.get("id")=="bitcoin":
        try:
            tip_r=await client.get("https://mempool.space/api/blocks/tip/height"); tip_r.raise_for_status(); tip=int(tip_r.text.strip())
            blocks=await client.get(f"https://mempool.space/api/blocks/{tip}"); blocks.raise_for_status(); block_rows=blocks.json()[:3]
            flows=[]; tx_count=0; large_count=0; total_large=0; largest=0
            threshold=100000
            for b in block_rows:
                bh=b.get("id") or b.get("hash")
                if not bh: continue
                txr=await client.get(f"https://mempool.space/api/block/{bh}/txs/0");
                if txr.status_code!=200: continue
                for tx in txr.json():
                    tx_count+=1
                    for v in tx.get("vout",[]):
                        btc=float(v.get("value") or 0)/100_000_000
                        usd=btc*price
                        if usd>=threshold:
                            large_count+=1; total_large+=usd; largest=max(largest,usd)
                            flows.append({"usd":usd,"btc":btc,"from":"Bitcoin transaction","to":v.get("scriptpubkey_address") or "Unlabeled address","date":b.get("timestamp") and time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime(b["timestamp"])) or ""})
            if tx_count:
                flows.sort(key=lambda x:x["usd"],reverse=True)
                return {"available":True,"chain":"Bitcoin","sample_transfers":tx_count,"large_transfers":large_count,"largest_transfer_usd":largest,"total_large_transfer_usd":total_large,"recent_large_transfers":flows[:5],"message":"Observed large Bitcoin outputs in recent confirmed blocks. These are transfer observations, not proof of whale intent, ownership, or exchange flow."}
        except Exception:
            pass
    if symbol=="xrp" or coin.get("id")=="ripple":
        # Primary: XRPSCAN rich list + account transaction history. The rich list is
        # nightly-updated, while account transactions come from validated ledgers.
        for base, style in [("https://api.xrpscan.com/api/v1","xrpscan"),("https://api.xrpl.to/v1","xrplto")]:
            try:
                if style=="xrpscan":
                    br=await client.get(f"{base}/balances"); br.raise_for_status(); holders=(br.json() or [])[:12]
                else:
                    # xrpl.to has no public rich-list endpoint; use a small set of
                    # well-known exchange/gateway accounts only as a fallback.
                    holders=[]
                cutoff=time.time()-86400; tx_count=large_count=0; largest=total_large=0; flows=[]; named=0
                for h in holders:
                    account=h.get("account") if isinstance(h,dict) else None
                    if not account: continue
                    try:
                        if style=="xrpscan":
                            tr=await client.get(f"{base}/account/{account}/transactions")
                            payload=tr.json() if tr.status_code==200 else {}
                            txs=payload.get("transactions") or []
                        else:
                            tr=await client.get(f"{base}/account/tx/{account}",params={"limit":200})
                            payload=tr.json() if tr.status_code==200 else {}
                            txs=payload.get("transactions") or []
                        for t in txs:
                            dt=t.get("date") or t.get("close_time_iso") or t.get("close_time_human") or ""
                            try: ts=time.mktime(time.strptime(dt[:19].replace('T',' '),"%Y-%m-%d %H:%M:%S"))
                            except Exception: ts=0
                            if ts and ts<cutoff: continue
                            txj=t.get("tx_json") or t
                            amt=(t.get("meta") or {}).get("delivered_amount") or txj.get("Amount") or t.get("Amount")
                            if isinstance(amt,dict):
                                if amt.get("currency") not in (None,"XRP"): continue
                                xrp=float(amt.get("value") or 0)/1e6
                            else:
                                try: xrp=float(amt or 0)/1e6
                                except Exception: xrp=0
                            if xrp<=0: continue
                            tx_count+=1; usd=xrp*price
                            if usd>=100000:
                                large_count+=1; total_large+=usd; largest=max(largest,usd)
                                flows.append({"usd":usd,"xrp":xrp,"from":(t.get("AccountName") or {}).get("name") or txj.get("Account","Wallet"),"to":(t.get("DestinationName") or {}).get("name") or txj.get("Destination","Wallet"),"date":dt})
                            if (t.get("AccountName") or {}).get("verified") or (t.get("DestinationName") or {}).get("verified"): named+=1
                    except Exception: continue
                if holders and (tx_count or large_count):
                    flows.sort(key=lambda x:x["usd"],reverse=True)
                    return {"available":True,"chain":"XRP Ledger","sample_holders":len(holders),"sample_transfers":tx_count,"large_transfers":large_count,"largest_transfer_usd":largest,"total_large_transfer_usd":total_large,"named_exchange_transfers":named,"recent_large_transfers":flows[:5],"message":"Observed validated XRP payments involving a sample of high-balance accounts. Large transfers are not proof of whale intent or buy/sell direction."}
            except Exception: continue
    explorers={
        "ethereum":"https://eth.blockscout.com","base":"https://base.blockscout.com","arbitrum_one":"https://arbitrum.blockscout.com",
        "optimistic_ethereum":"https://optimism.blockscout.com","polygon_pos":"https://polygon.blockscout.com","gnosis":"https://gnosis.blockscout.com",
        "celo":"https://celo.blockscout.com","zksync":"https://zksync.blockscout.com","linea":"https://linea.blockscout.com","scroll":"https://scroll.blockscout.com"
    }
    for chain,address in (coin.get("platforms") or {}).items():
        chain_key=(chain or "").replace("-","_")
        host=explorers.get(chain_key)
        if not host or not address or not address.startswith("0x"): continue
        try:
            r=await client.get(f"{host}/api/v2/tokens/{address}/transfers",params={"type":"ERC-20"})
            if r.status_code!=200: continue
            items=r.json().get("items",[]); large=[]
            for it in items:
                raw=it.get("total",{}).get("value") if isinstance(it.get("total"),dict) else it.get("value"); dec=it.get("total",{}).get("decimals") if isinstance(it.get("total"),dict) else it.get("decimals")
                try: usd=float(raw)/(10**int(dec or 0))*price
                except Exception: continue
                if usd>=100000: large.append({"usd":usd,"from":(it.get("from") or {}).get("hash") or (it.get("from") or {}).get("address_hash") or "Wallet","to":(it.get("to") or {}).get("hash") or (it.get("to") or {}).get("address_hash") or "Wallet","date":it.get("timestamp","")})
            if items:
                large.sort(key=lambda x:x["usd"],reverse=True)
                return {"available":True,"chain":chain,"sample_transfers":len(items),"large_transfers":len(large),"largest_transfer_usd":large[0]["usd"] if large else 0,"total_large_transfer_usd":sum(x["usd"] for x in large),"recent_large_transfers":large[:5],"message":"Observed recent ERC-20 transfer events from a public chain explorer. These are transfer events, not proof of whale intent or exchange flow."}
        except Exception: continue
    return {"available":False,"message":"No compatible public on-chain transfer feed returned data for this asset/network. Whale activity is not inferred."}

async def social_activity(client, coin):
    """Find verified public developer/community activity for the clicked asset.
    Never invent activity. CoinGecko links are preferred, but GitHub/Reddit search
    is used as a fallback when those links are missing."""
    links=coin.get("links") or {}
    repos=[x for x in ((links.get("repos_url") or {}).get("github") or []) if x]
    name=(coin.get("name") or "").strip()
    symbol=(coin.get("symbol") or "").strip()

    # If CoinGecko does not provide repositories, discover likely public repos.
    if not repos:
        queries=[]
        if name: queries.append(f'"{name}" crypto')
        if symbol and symbol.lower()!=name.lower(): queries.append(f'"{symbol}" crypto blockchain')
        seen=set()
        for q in queries:
            try:
                r=await client.get("https://api.github.com/search/repositories",params={"q":q,"sort":"stars","order":"desc","per_page":8},headers={"Accept":"application/vnd.github+json","User-Agent":"WhatsHappening/12"})
                if r.status_code!=200: continue
                for item in (r.json().get("items") or []):
                    full=item.get("full_name")
                    if full and full not in seen:
                        seen.add(full); repos.append("https://github.com/"+full)
            except Exception: continue
            if len(repos)>=5: break

    stars=forks=commits=repos_checked=0; repo_names=[]
    for repo in repos[:5]:
        try:
            slug=repo.rstrip('/').split('github.com/')[-1].replace('.git','')
            if '/' not in slug: continue
            r=await client.get(f"https://api.github.com/repos/{slug}",headers={"Accept":"application/vnd.github+json","User-Agent":"WhatsHappening/12"})
            if r.status_code!=200: continue
            d=r.json(); repos_checked+=1; repo_names.append(d.get("full_name") or slug)
            stars+=int(d.get("stargazers_count") or 0); forks+=int(d.get("forks_count") or 0)
            since=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(time.time()-28*86400))
            cr=await client.get(f"https://api.github.com/repos/{slug}/commits",params={"since":since,"per_page":100},headers={"Accept":"application/vnd.github+json","User-Agent":"WhatsHappening/12"})
            if cr.status_code==200:
                payload=cr.json(); commits+=len(payload) if isinstance(payload,list) else 0
        except Exception: continue

    reddit_posts=None; reddit_sub=None; reddit_comments=None
    sub=((links.get("subreddit_url") or "").rstrip('/').split('/r/')[-1] if links.get("subreddit_url") else "")
    try:
        if sub:
            rss=f"https://www.reddit.com/r/{sub}/new.rss"
        else:
            term=(name or symbol).strip()
            rss=f"https://www.reddit.com/search.rss?q={term}&sort=new&t=day"
        rr=await client.get("https://api.rss2json.com/v1/api.json",params={"rss_url":rss},headers={"User-Agent":"WhatsHappening/12"})
        if rr.status_code==200:
            d=rr.json()
            if d.get("status")=="ok":
                cutoff=time.time()-86400
                posts=[]
                for x in d.get("items",[]):
                    try:
                        ts=time.mktime(time.strptime((x.get("pubDate") or "")[:19],"%Y-%m-%d %H:%M:%S"))
                    except Exception: ts=0
                    if ts>=cutoff: posts.append(x)
                reddit_posts=len(posts); reddit_sub=sub or None
    except Exception: pass

    available=bool(repos_checked or reddit_posts is not None)
    return {
        "available":available,
        "github_stars":stars if repos_checked else None,
        "github_forks":forks if repos_checked else None,
        "github_repos":repos_checked or None,
        "github_commits_4_weeks":commits if repos_checked else None,
        "github_repo_names":repo_names,
        "reddit_posts_24h":reddit_posts,
        "reddit_comments_24h":reddit_comments,
        "reddit_subreddit":reddit_sub or None,
        "telegram_link":links.get("telegram_channel_identifier") or None,
        "message":"Live public GitHub/Reddit activity. Repository discovery uses project-name/symbol search when no official repository link is available." if available else "No verified public developer/community activity source returned data for this project."
    }

async def fetch():
    async with httpx.AsyncClient(timeout=20) as c:
        r=await c.get(URL,params=PARAMS); r.raise_for_status(); return r.json()
def row(c,ts):
    return (c["id"],c["symbol"],c["name"],ts,float(c.get("current_price") or 0),float(c.get("market_cap") or 0),float(c.get("total_volume") or 0),float(c.get("price_change_percentage_1h_in_currency") or 0),float(c.get("price_change_percentage_24h_in_currency") or 0),float(c.get("price_change_percentage_7d_in_currency") or 0))
def current(r): return {"volume":r[6],"market_cap":r[5],"h1":r[7],"h24":r[8],"h7":r[9]}
@app.get("/api/news")
async def news(q: str):
    # Server-side companion for deployments. The browser build uses the
    # CORS-enabled RSS2JSON route directly, so this is optional.
    feeds=[
        ("CoinDesk","https://www.coindesk.com/arc/outboundfeeds/rss/"),
        ("Cointelegraph","https://cointelegraph.com/rss"),
        ("Decrypt","https://decrypt.co/feed"),
        ("CryptoSlate","https://cryptoslate.com/feed/"),
        ("NewsBTC","https://www.newsbtc.com/feed/")
    ]
    terms=[x.strip().lower() for x in q.replace('"','').split() if x.strip()]
    items=[]
    async with httpx.AsyncClient(timeout=12,follow_redirects=True) as c:
        for source,rss in feeds:
            try:
                r=await c.get("https://api.rss2json.com/v1/api.json",
                              params={"rss_url":rss})
                if r.status_code!=200: continue
                d=r.json()
                for a in d.get("items",[]):
                    hay=(a.get("title","")+" "+a.get("description","")+" "+a.get("content","")).lower()
                    if any(t in hay for t in terms):
                        items.append({
                            "title":a.get("title","Untitled"),
                            "link":a.get("link","#"),
                            "pub":a.get("pubDate",""),
                            "source":source
                        })
            except Exception:
                continue
    # Deduplicate and sort newest first.
    seen=set(); out=[]
    for a in sorted(items,key=lambda x:x.get("pub",""),reverse=True):
        k=a["title"].strip().lower()
        if k and k not in seen:
            seen.add(k); out.append(a)
    return {"items":out[:8]}


@app.get("/api/checks/{coin_id}")
async def checks(coin_id: str):
    """Fetch supporting evidence for the clicked asset instead of leaving
    the user with a generic checklist. Data is observational, not a causal claim."""
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
        coin_r = await c.get(f"https://api.coingecko.com/api/v3/coins/{coin_id}", params={
            "localization":"false","tickers":"false","market_data":"true",
            "community_data":"true","developer_data":"true","sparkline":"false"})
        coin_r.raise_for_status(); coin = coin_r.json()
        tick_r = await c.get(f"https://api.coingecko.com/api/v3/coins/{coin_id}/tickers", params={
            "include_exchange_logo":"false","depth":"true","page":1})
        tickers = tick_r.json().get("tickers",[]) if tick_r.status_code == 200 else []

    md=coin.get("market_data") or {}
    total_volume=float(md.get("total_volume",{}).get("usd") or 0)
    venues=[]; spreads=[]; prices=[]
    for t in tickers:
        market=t.get("market") or {}
        name=market.get("name") or "Unknown venue"
        vol=float(t.get("converted_volume",{}).get("usd") or t.get("volume") or 0)
        last=float(t.get("converted_last",{}).get("usd") or t.get("last") or 0)
        if vol>0: venues.append({"name":name,"volume":vol,"type":market.get("identifier","")})
        if t.get("bid_ask_spread_percentage") is not None:
            try: spreads.append(float(t["bid_ask_spread_percentage"]))
            except: pass
        if last>0: prices.append(last)
    venues.sort(key=lambda x:x["volume"],reverse=True)
    pspread=((max(prices)-min(prices))/((max(prices)+min(prices))/2)*100) if len(prices)>=2 else None
    wallet=await wallet_activity(c,coin)
    social=await social_activity(c,coin)
    return {
        "volume_liquidity": {
            "market_volume_24h": total_volume,
            "top_venues": venues[:5],
            "top_venue_share": (venues[0]["volume"]/total_volume*100) if venues and total_volume else None
        },
        "venue_spreads": {
            "reported_bid_ask_avg": (sum(spreads)/len(spreads)) if spreads else None,
            "reported_bid_ask_max": max(spreads) if spreads else None,
            "observed_price_dispersion": pspread,
            "ticker_count": len(tickers)
        },
        "wallet_activity": wallet,
        "social": social
    }

@app.get("/api/push/public-key")
async def push_public_key():
    key=os.getenv("VAPID_PUBLIC_KEY")
    return {"enabled":bool(key),"publicKey":key}

@app.post("/api/push/subscribe")
async def push_subscribe(payload: dict):
    try: save_subscription(payload)
    except ValueError as e: raise HTTPException(400,str(e))
    return {"ok":True}

@app.post("/api/push/unsubscribe")
async def push_unsubscribe(payload: dict):
    endpoint=payload.get("endpoint")
    if endpoint: remove_subscription(endpoint)
    return {"ok":True}

@app.get("/api/alerts/recent")
async def alerts_recent(limit:int=30):
    return {"alerts":recent_events(max(1,min(limit,100)))}

@app.post("/api/alerts/scan-now")
async def alerts_scan_now():
    return await scan_once()

@app.get("/api/health")
def health(): return {"ok":True,"version":"7.0"}
@app.post("/api/ingest")
async def ingest():
    cs=await fetch();ts=int(time.time());insert_many([row(c,ts) for c in cs]);return {"inserted":len(cs),"timestamp":ts}
@app.get("/api/search")
async def search(q: str):
    q=q.strip()
    if not q: return {"results":[]}
    async with httpx.AsyncClient(timeout=20) as c:
        r=await c.get("https://api.coingecko.com/api/v3/search",params={"query":q})
        r.raise_for_status()
        data=r.json()
    results=[]
    for x in data.get("coins",[])[:12]:
        results.append({"id":x.get("id"),"name":x.get("name"),"symbol":x.get("symbol"),
                        "thumb":x.get("thumb"),"market_cap_rank":x.get("market_cap_rank")})
    return {"results":results}

@app.get("/api/asset/{coin_id}")
async def asset(coin_id:str):
    async with httpx.AsyncClient(timeout=20) as c:
        r=await c.get("https://api.coingecko.com/api/v3/coins/markets",
                      params={"vs_currency":"usd","ids":coin_id,"price_change_percentage":"1h,24h,7d"})
        r.raise_for_status(); data=r.json()
        d=await c.get(f"https://api.coingecko.com/api/v3/coins/{coin_id}",
                      params={"localization":"false","tickers":"false","market_data":"true",
                              "community_data":"true","developer_data":"true","sparkline":"false"})
        d.raise_for_status(); details=d.json()
    if not data: raise HTTPException(404,"Asset not found")
    c=data[0]; ts=int(time.time()); rr=row(c,ts); insert_many([rr])
    market=[{"volume":float(x.get("total_volume") or 0),"market_cap":float(x.get("market_cap") or 0),
             "h1":float(x.get("price_change_percentage_1h_in_currency") or 0),
             "h24":float(x.get("price_change_percentage_24h_in_currency") or 0),
             "h7":float(x.get("price_change_percentage_7d_in_currency") or 0)} for x in data]
    a=analyze(current(rr),history(coin_id,101)[1:],market)
    return {"coin":c,"project":details,"analysis":a}

@app.get("/api/scan")
async def scan():
    cs=await fetch();ts=int(time.time());rows=[row(c,ts) for c in cs];insert_many(rows);out=[]
    for c,r in zip(cs,rows):
        market=[{"volume":float(x.get("total_volume") or 0),"market_cap":float(x.get("market_cap") or 0),
" h1":float(x.get("price_change_percentage_1h_in_currency") or 0),
"h24":float(x.get("price_change_percentage_24h_in_currency") or 0),
"h7":float(x.get("price_change_percentage_7d_in_currency") or 0)} for x in cs]
        market=[{k.strip():v for k,v in x.items()} for x in market]
        a=analyze(current(r),history(c["id"],101)[1:],market);out.append({"coin":c,"analysis":a})
    out.sort(key=lambda x:x["analysis"]["score"],reverse=True)
    return {"timestamp":ts,"count":len(out),"results":out}
@app.get("/",response_class=HTMLResponse)
def home(): return open("web/index.html",encoding="utf-8").read()
