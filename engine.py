from statistics import mean,pstdev
def z(value,values):
    values=[x for x in values if x is not None]
    if len(values)<3:return 0.0
    s=pstdev(values);return (value-mean(values))/s if s else 0.0
def analyze(cur,hist,market=None):
    score=0; reasons=[]
    market=market or []
    if market:
        turns=[x["volume"]/x["market_cap"] for x in market if x.get("market_cap")]
        h1=[x["h1"] for x in market];h24=[x["h24"] for x in market];h7=[x["h7"] for x in market]
        for label,value,arr,weight in [("turnover",cur["volume"]/cur["market_cap"] if cur["market_cap"] else 0,turns,8),("1h move",cur["h1"],h1,6),("24h move",cur["h24"],h24,6),("7d move",cur["h7"],h7,4)]:
            zz=z(value,arr)
            if abs(zz)>=2:
                score+=min(30 if label=="turnover" else 18,round(abs(zz)*weight));reasons.append(f"{label} is {zz:+.1f}σ across the market")
    vols=[x["volume"] for x in hist if x.get("volume") is not None];h24=[x["h24"] for x in hist if x.get("h24") is not None]
    turns=[x["volume"]/x["market_cap"] for x in hist if x.get("volume") and x.get("market_cap")]
    zv=z(cur["volume"],vols);zp=z(cur["h24"],h24);zt=z(cur["volume"]/cur["market_cap"] if cur["market_cap"] else 0,turns)
    if len(hist)>=3 and abs(zv)>=2:score+=min(25,round(abs(zv)*7));reasons.append(f"volume is {zv:+.1f}σ vs its own baseline")
    if len(hist)>=3 and abs(zp)>=2:score+=min(15,round(abs(zp)*5));reasons.append(f"24h move is {zp:+.1f}σ vs its own baseline")
    if len(hist)>=3 and abs(zv)>=2 and abs(zp)<1:score+=12;reasons.append("price/volume divergence detected")
    score=min(100,score)
    cls="HIGH-CONFIDENCE ANOMALY" if score>=75 else "WORTH INVESTIGATING" if score>=50 else "EARLY SIGNAL" if score>=30 else "NORMAL / BASELINE"
    explanation=("Multiple independent signals are unusually strong; this deserves deeper investigation of venue, liquidity, wallet and catalyst data."
        if score>=75 else
        "Several signals are unusual; the next step is checking venue, liquidity, wallet and catalyst data."
        if score>=50 else
        "The asset is behaving differently from the broader market, but evidence is not yet strong enough for a major anomaly."
        if score>=30 else
        "The current data does not show a strong anomaly.")
    return {"score":score,"classification":cls,"confidence":min(99,max(15,round(30+score*.65))),"explanation":explanation,"signals":reasons or ["No strong anomaly signal yet."],"baseline_observations":len(hist),"metrics":{"volume_z":round(zv,2),"price_z":round(zp,2),"turnover_z":round(zt,2)},"news_status":"PENDING","news_correlation":None}
