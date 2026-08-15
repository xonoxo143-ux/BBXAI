#!/usr/bin/env python3
"""Resolve canonical Mining world-node images from structured RS3 resource data."""
from __future__ import annotations
import csv, html, json, re, time, urllib.parse, urllib.request
from collections import defaultdict
from pathlib import Path

API='https://runescape.wiki/api.php'
UA='BBXAI-RS3-Mining-Node-Resolver/1.0 (personal research/reference)'
BATCH=750

def api(**p):
    p.setdefault('format','json'); p.setdefault('formatversion','2')
    req=urllib.request.Request(API+'?'+urllib.parse.urlencode(p),headers={'User-Agent':UA})
    with urllib.request.urlopen(req,timeout=120) as r: return json.load(r)

def bq(q): return api(action='bucket',query=q).get('bucket',[])

def all_rows(bucket,fields):
    out=[]; off=0; sel=','.join(repr(x) for x in fields)
    while True:
        rows=bq(f"bucket('{bucket}').select({sel}).limit({BATCH}).offset({off}).orderBy('page_name','asc').run()")
        out += rows
        if len(rows)<BATCH: return out
        off += BATCH; time.sleep(.03)

def vals(v): return v if isinstance(v,list) else ([] if v is None else [v])
def clean(s):
    s=html.unescape(str(s or '')).strip().replace('_',' ')
    if '|' in s: s=s.split('|',1)[0]
    if '#' in s: s=s.split('#',1)[0]
    return re.sub(r'\s+',' ',s).strip()
def image(s):
    s=html.unescape(str(s or '')).strip()
    if '|' in s: s=s.split('|',1)[0]
    if s.lower().startswith('file:'): s=s[5:]
    return s.strip()
def norm(s):
    s=clean(s).casefold()
    s=re.sub(r'\([^)]*\)',' ',s)
    s=re.sub(r'\b(?:ores?|rocks?|veins?|deposit|mine|mineral)\b',' ',s)
    return ' '.join(re.sub(r'[^a-z0-9]+',' ',s).split())
def score(r,wanted):
    p=clean(r.get('page_name')); o=' '.join(clean(x) for x in vals(r.get('object_name'))); im=image((vals(r.get('image')) or [''])[0]); n=norm(wanted)
    sc=0
    if norm(p)==n: sc+=500
    if norm(o)==n: sc+=450
    if p.casefold()==(wanted+' rock').casefold(): sc+=250
    if im.casefold()==(wanted+' rock.png').casefold(): sc+=300
    if '(historical)' in im.casefold(): sc-=1000
    if any(x in im.casefold() for x in ['old','detail','depleted','empty']): sc-=400
    if '#common' in str(r.get('page_name_sub') or '').casefold(): sc+=100
    return sc

def main():
    out=Path('rs3_mining_node_plan'); out.mkdir(exist_ok=True)
    locs=all_rows('resource_locations',['page_name','resource','skill','resource_location_type'])
    resources=set()
    for r in locs:
        # Bucket may serialize repeated fields as either scalar or list; string fallback is deliberate.
        if 'mining' not in str(r.get('skill') or '').casefold(): continue
        for x in vals(r.get('resource')):
            x=clean(x)
            if x: resources.add(x)
    resources=sorted(resources,key=str.casefold)
    print(f'Mining resources: {len(resources)}',flush=True)

    mine=bq("bucket('infobox_scenery').select('page_name','page_name_sub','object_name','image','object_id','options').where('options','Mine').limit(5000).run()")
    print(f'Mineable scenery rows: {len(mine)}',flush=True)
    index=defaultdict(list)
    for r in mine:
        for x in vals(r.get('page_name'))+vals(r.get('object_name')):
            n=norm(x)
            if n: index[n].append(r)

    resolved=[]; missing=[]
    for res in resources:
        n=norm(res); cand=list(index.get(n,[]))
        if not cand:
            cand=[r for key,rs in index.items() if n and (n in key or key in n) for r in rs]
        cand=[r for r in cand if any(image(x) for x in vals(r.get('image')))]
        if not cand:
            missing.append(res); continue
        best=max(cand,key=lambda r:score(r,res))
        im=image((vals(best.get('image')) or [''])[0])
        resolved.append({'resource':res,'image':im,'page_name':best.get('page_name',''),'page_name_sub':best.get('page_name_sub',''),'score':score(best,res)})

    with (out/'manifest.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=['resource','image','page_name','page_name_sub','score']); w.writeheader(); w.writerows(resolved)
    (out/'missing.txt').write_text('\n'.join(missing),encoding='utf-8')
    summary={'resources':len(resources),'resolved':len(resolved),'missing':len(missing),'mineable_scenery_rows':len(mine),'unique_images':len({x['image'] for x in resolved})}
    (out/'plan.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    (out/'SUMMARY.md').write_text(f"# RS3 Mining node plan\n\n- Resource types: **{len(resources)}**\n- Resolved node images: **{len(resolved)}**\n- Missing: **{len(missing)}**\n- Unique node images: **{summary['unique_images']}**\n",encoding='utf-8')
    print(summary,flush=True)

if __name__=='__main__': main()
