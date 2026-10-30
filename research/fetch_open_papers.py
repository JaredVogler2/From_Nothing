#!/usr/bin/env python3
"""Download only explicitly public PDF URLs from papers.json; never bypass paywalls."""
import json, pathlib, urllib.request, hashlib, time
root=pathlib.Path(__file__).resolve().parent
papers=json.loads((root/"papers.json").read_text())
for p in papers:
    url=p.get("pdf","")
    if not url or p["access"]!="open":
        print(f"SKIP {p['id']} {p['title']}: no confirmed open PDF URL")
        continue
    target=root/f"{p['id']}_{''.join(c if c.isalnum() else '_' for c in p['title'])[:75]}.pdf"
    if target.exists() and target.read_bytes()[:5]==b"%PDF-":
        print("EXISTS",target.name);continue
    try:
        req=urllib.request.Request(url,headers={"User-Agent":"FOCUS-Research-Library/1.0"})
        with urllib.request.urlopen(req,timeout=45) as response:
            data=response.read(25_000_001)
        if len(data)>25_000_000 or not data.startswith(b"%PDF-"):
            raise ValueError("not a PDF or >25 MB")
        target.write_bytes(data)
        print("OK",target.name,len(data),hashlib.sha256(data).hexdigest())
    except Exception as e:
        print("FAILED",p["id"],url,str(e))
    time.sleep(0.5)
