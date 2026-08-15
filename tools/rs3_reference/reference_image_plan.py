#!/usr/bin/env python3
"""Resolve canonical image metadata for all skill-bearing RS3 recipe entities.

This does not download image bytes. It builds the exact inventory-sprite manifest
and measures the corpus before a bulk reference download is allowed.
"""
from __future__ import annotations

import csv
import json
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

API = "https://runescape.wiki/api.php"
UA = "BBXAI-RS3-Reference-Image-Plan/1.0 (personal research/reference)"
BATCH = 750


def api(**params):
    params.setdefault("format", "json")
    params.setdefault("formatversion", "2")
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def bucket_all(name, fields):
    out=[]; offset=0
    select=",".join(repr(x) for x in fields)
    while True:
        q=f"bucket('{name}').select({select}).limit({BATCH}).offset({offset}).orderBy('page_name','asc').run()"
        d=api(action="bucket",query=q)
        rows=d.get("bucket",[])
        out.extend(rows)
        print(f"  {name}: {len(out)} rows",flush=True)
        if len(rows)<BATCH: break
        offset += BATCH
        time.sleep(.05)
    return out


def as_list(v):
    if v is None: return []
    return v if isinstance(v,list) else [v]


def clean_image(s):
    s=str(s or '').strip()
    return s[5:] if s.lower().startswith('file:') else s


def main():
    out=Path('rs3_reference_image_plan'); out.mkdir(exist_ok=True)
    print('Loading recipe graph...',flush=True)
    rows=bucket_all('recipe',['page_name','uses_skill','production_json'])
    images={}
    failures=[]
    for row in rows:
        try: prod=json.loads(row.get('production_json') or '{}')
        except Exception as exc:
            failures.append({'page':row.get('page_name'),'error':repr(exc)}); continue
        skills=[]
        for s in as_list(prod.get('skills')):
            if isinstance(s,dict) and s.get('name'): skills.append(str(s['name']).strip())
        if not skills: skills=[str(x).strip() for x in as_list(row.get('uses_skill')) if x]
        skills={s for s in skills if s}
        if not skills: continue
        for role,key in [('material','materials'),('output','outputs')]:
            for ent in as_list(prod.get(key)):
                if not isinstance(ent,dict) or not ent.get('image'): continue
                image=clean_image(ent['image'])
                # Explicit historical-state art is not a current canonical target.
                if '(historical)' in image.casefold(): continue
                rec=images.setdefault(image,{'skills':set(),'roles':set(),'names':set(),'pages':set()})
                rec['skills'].update(skills); rec['roles'].add(role)
                if ent.get('name'): rec['names'].add(str(ent['name']))
                if row.get('page_name'): rec['pages'].add(str(row['page_name']))

    titles=sorted(images, key=str.casefold)
    print(f'Resolving imageinfo for {len(titles)} canonical recipe sprites...',flush=True)
    info={}
    for i in range(0,len(titles),50):
        chunk=titles[i:i+50]
        d=api(action='query',prop='imageinfo',titles='|'.join('File:'+x for x in chunk),iiprop='url|mime|size|sha1')
        for p in d.get('query',{}).get('pages',[]):
            title=p.get('title','')
            key=clean_image(title)
            ii=(p.get('imageinfo') or [None])[0]
            if ii: info[key]=ii
        if (i//50+1)%25==0 or i+50>=len(titles):
            print(f'  resolved {min(i+50,len(titles))}/{len(titles)}',flush=True)

    total_bytes=0; resolved=0; missing=[]; mime_counts=defaultdict(int); skill_bytes=defaultdict(int); skill_counts=defaultdict(int)
    manifest=[]
    for image in titles:
        meta=info.get(image)
        rec=images[image]
        if not meta:
            missing.append(image); continue
        resolved += 1
        size=int(meta.get('size') or 0); total_bytes += size
        mime_counts[str(meta.get('mime') or '')]+=1
        for s in rec['skills']:
            skill_bytes[s]+=size; skill_counts[s]+=1
        manifest.append({
            'image':image,'url':meta.get('url',''),'mime':meta.get('mime',''),
            'width':meta.get('width',''),'height':meta.get('height',''),'bytes':size,
            'sha1':meta.get('sha1',''),'roles':' | '.join(sorted(rec['roles'])),
            'skills':' | '.join(sorted(rec['skills'])),'names':' | '.join(sorted(rec['names'])),
            'source_page_count':len(rec['pages']),
        })

    with (out/'manifest.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=manifest[0].keys() if manifest else ['image'])
        w.writeheader(); w.writerows(manifest)
    (out/'missing.txt').write_text('\n'.join(missing),encoding='utf-8')
    summary={
        'recipe_rows':len(rows),'planned_images':len(titles),'resolved_images':resolved,
        'missing_images':len(missing),'total_bytes':total_bytes,
        'total_megabytes_decimal':round(total_bytes/1_000_000,2),
        'mime_counts':dict(mime_counts),
        'per_skill':{s:{'images':skill_counts[s],'bytes':skill_bytes[s],'mb':round(skill_bytes[s]/1_000_000,2)} for s in sorted(skill_counts)},
        'parse_failures':len(failures),
    }
    (out/'plan.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    lines=['# RS3 recipe-image download plan','',f"- Canonical skill-recipe sprites: **{len(titles)}**",f"- Resolved: **{resolved}**",f"- Missing: **{len(missing)}**",f"- Raw source bytes: **{summary['total_megabytes_decimal']} MB**",'', '## By skill']
    for s,v in summary['per_skill'].items(): lines.append(f"- {s}: {v['images']} images / {v['mb']} MB")
    (out/'SUMMARY.md').write_text('\n'.join(lines),encoding='utf-8')
    print('Plan complete',flush=True)

if __name__=='__main__': main()
