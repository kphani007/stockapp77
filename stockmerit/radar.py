"""StockMerit Master Radar: repeatable broad-universe technical/fundamental scan."""
from __future__ import annotations
import numpy as np
import pandas as pd
import yfinance as yf
from stockmerit.features.momentum import rolling_rsi
from stockmerit.features.relative_strength import relative_strength_score

WEIGHTS={"trend":.20,"momentum":.12,"rs":.15,"volume":.15,"breakout":.13,"risk":.05,"fund":.20}

def clip(x,lo=0,hi=100): return float(max(lo,min(hi,x)))

def fund_score(info):
    vals=[]
    for k,m,b in [("revenueGrowth",200,50),("earningsGrowth",180,50),("returnOnEquity",200,0)]:
        v=info.get(k)
        if v is not None: vals.append(clip(b+float(v)*m if k!="returnOnEquity" else float(v)*m))
    de=info.get("debtToEquity")
    if de is not None: vals.append(clip(85-float(de)*.35))
    for k,v in [("freeCashflow",info.get("freeCashflow")),("operatingCashflow",info.get("operatingCashflow"))]:
        if v is not None: vals.append(70 if float(v)>0 else 30)
    peg=info.get("pegRatio") or info.get("trailingPegRatio")
    pe=info.get("trailingPE")
    if peg and float(peg)>0: vals.append(clip(100-(float(peg)-.8)*30))
    elif pe and float(pe)>0: vals.append(clip(75-max(float(pe)-25,0)*1.2))
    return (round(float(np.mean(vals)),1) if vals else None)

def tech_row(sym,df,benchmark):
    if df is None or df.empty or "Close" not in df: return None
    c=pd.to_numeric(df["Close"],errors="coerce").dropna()
    if len(c)<210: return None
    v=pd.to_numeric(df.get("Volume",pd.Series(index=df.index)),errors="coerce").fillna(0)
    h=pd.to_numeric(df.get("High",c),errors="coerce"); l=pd.to_numeric(df.get("Low",c),errors="coerce")
    p=float(c.iloc[-1]); s20=float(c.rolling(20).mean().iloc[-1]); s50=float(c.rolling(50).mean().iloc[-1]); s200=float(c.rolling(200).mean().iloc[-1])
    rsi=float(rolling_rsi(c).iloc[-1]); v20=float(v.tail(20).mean()) or 0; rv=float(v.iloc[-1]/v20) if v20 else 0
    h20=float(c.tail(20).max()); h52=float(c.tail(252).max())
    trend=(30 if p>s50 else 0)+(30 if p>s200 else 0)+(20 if s50>s200 else 0)+(20 if s20>s50 else 0)
    momentum=clip(50+(rsi-50)*1.6-(min(15,(rsi-75)*1.5) if rsi>75 else 0))
    rs=relative_strength_score(c,benchmark,min(63,len(c)-1)) or 50
    volume=clip(50+(rv-1)*35)
    d20=(h20/p-1)*100 if p else 99; d52=(h52/p-1)*100 if p else 99
    breakout=clip(100-d20*8)+(12 if rv>=1.5 and d20<=3 else 0); breakout=clip(breakout)
    tr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    atr=float(tr.rolling(14).mean().iloc[-1]/p*100) if p else 99
    risk=clip(90-max(0,atr-2)*12-max(0,(p/s200-1)*100-20)*1.5)
    ret63=float(c.iloc[-1]/c.iloc[-64]-1)*100 if len(c)>64 else None
    return {"Symbol":sym.replace(".NS",""),"Price":round(p,2),"RSI":round(rsi,1),"SMA20":round(s20,2),"SMA50":round(s50,2),"SMA200":round(s200,2),"RelVol":round(rv,2),"From20DHigh%":round(d20,2),"From52WHigh%":round(d52,2),"Return63D%":round(ret63,2) if ret63 is not None else None,"ATR14%":round(atr,2),"_trend":trend,"_momentum":momentum,"_rs":rs,"_volume":volume,"_breakout":breakout,"_risk":risk}

def state(score):
    return "Core Candidate" if score>=75 else "Emerging" if score>=65 else "Watch" if score>=55 else "Weak"

def scan_master_radar(tickers,fund_limit=120,batch=200):
    if not tickers: return pd.DataFrame()
    b=yf.download("^NSEI",period="2y",interval="1d",auto_adjust=False,progress=False)
    benchmark=(b["Close"].squeeze() if isinstance(b.columns,pd.MultiIndex) else b["Close"]).dropna()
    rows=[]
    for i in range(0,len(tickers),batch):
        chunk=tickers[i:i+batch]
        data=yf.download(chunk,period="2y",interval="1d",group_by="ticker",auto_adjust=False,threads=True,progress=False)
        for sym in chunk:
            try:
                df=data[sym] if isinstance(data.columns,pd.MultiIndex) else data
                x=tech_row(sym,df,benchmark)
                if x: rows.append(x)
            except Exception: pass
    if not rows: return pd.DataFrame()
    d=pd.DataFrame(rows)
    d["_pre"]=d._trend*.24+d._momentum*.12+d._rs*.22+d._volume*.18+d._breakout*.18+d._risk*.06
    top=d.sort_values("_pre",ascending=False)["Symbol"].head(fund_limit).tolist()
    fmap={}
    for s in top:
        try:
            info=yf.Ticker(s+".NS").info or {}
            fmap[s]=(fund_score(info),info)
        except Exception: fmap[s]=(None,{})
    out=[]
    for _,r in d.iterrows():
        fs,info=fmap.get(r.Symbol,(None,{})); f=50 if fs is None else fs
        score=r._trend*WEIGHTS["trend"]+r._momentum*WEIGHTS["momentum"]+r._rs*WEIGHTS["rs"]+r._volume*WEIGHTS["volume"]+r._breakout*WEIGHTS["breakout"]+r._risk*WEIGHTS["risk"]+f*WEIGHTS["fund"]
        q=r.to_dict(); q["Radar Score"]=round(score,1); q["Status"]=state(score); q["Fundamental Score"]=round(f,1); q["Sector"]=info.get("sector","n/a"); q["Market Cap Cr"]=round(float(info["marketCap"])/1e7,0) if info.get("marketCap") else None; q["PE"]=round(float(info["trailingPE"]),1) if info.get("trailingPE") else None; q["PEG"]=round(float(info.get("pegRatio") or info.get("trailingPegRatio")),2) if (info.get("pegRatio") or info.get("trailingPegRatio")) else None
        out.append(q)
    return pd.DataFrame(out).sort_values(["Radar Score","Return63D%"],ascending=False).reset_index(drop=True)
