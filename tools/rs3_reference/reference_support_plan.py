#!/usr/bin/env python3
"""Resolve non-inventory RS3 skilling references: tools, facilities, mining nodes.

Recipe production_json already supplies canonical material/output inventory sprites.
This pass fills in supporting world/tool art without treating composite Wiki fields
as if they were single entity names.
"""
from __future__ import annotations

import csv
import html
import json
import re
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

API='https://runescape.wiki/api.php'
UA='BBXAI-RS3-Support-Reference-Plan/1.1 (personal research/reference)'
BATCH=750

GENERIC_TOOL_CLASSES={
    'axe','bow','crossbow (weapon type)','hatchet','hatchets','mattock',
    'pickaxe','pickaxes','deadfall','deadfall trapping','net trap','net trapping',
    'pitfall trapping','spike trap (big game hunter)','log trap (big game hunter)',
}


def api(**params):
    params.setdefault('format','json'); params.setdefault('formatversion','2')
    url=API+'?'+urllib.parse.urlencode(params)
    req=urllib.request.Request(url,headers={'User-Agent':UA})
    with urllib.request.urlopen(req,timeout=120) as r: return json.load(r)


def bquery(q):
    d=api(action='bucket',query=q); rows=d.get('bucket',[])
    if not isinstance(rows,list): raise RuntimeError('Bad bucket response')
    return rows


def bucket_all(name,fields):
    out=[]; offset=0; sel=','.join(repr(x) for x in fields)
    while True:
        rows=bquery(f"bucket('{name}').select({sel}).limit({BATCH}).offset({offset}).orderBy('page_name','asc').run()")
        out.extend(rows); print(f'  {name}: {len(out)} rows',flush=True)
        if len(rows)<BATCH: break
        offset+=BATCH; time.sleep(.04)
    return out


def as_list(v): return [] if v is None else (v if isinstance(v,list) else [v])


def lua_string(s): return json.dumps(str(s),ensure_ascii=False)


def split_top_level(text):
    """Split comma-separated Wiki entity refs, preserving commas inside parentheses."""
    text=html.unescape(str(text or '')).strip()
    if not text: return []
    parts=[]; buf=[]; depth=0
    for ch in text:
        if ch=='(':
            depth+=1
        elif ch==')' and depth:
            depth-=1
        if ch==',' and depth==0:
            part=''.join(buf).strip()
            if part: parts.append(part)
            buf=[]
        else:
            buf.append(ch)
    part=''.join(buf).strip()
    if part: parts.append(part)
    return parts


def clean_ref(text):
    text=html.unescape(str(text or '')).strip().replace('_',' ')
    if not text: return ''
    # Strip common Wiki display-text markup while retaining the actual page target.
    if '|' in text: text=text.split('|',1)[0].strip()
    # Query infobox buckets by page_name; anchors only select a variant on-page.
    if '#' in text: text=text.split('#',1)[0].strip()
    return re.sub(r'\s+',' ',text)


def explode_refs(value):
    out=[]
    for raw in as_list(value):
        if isinstance(raw,dict):
            raw=raw.get('name') or raw.get('page') or ''
        for part in split_top_level(raw):
            ref=clean_ref(part)
            if ref: out.append(ref)
    return out


def query_names(bucket,fields,names,field='page_name',chunk_size=35):
    names=sorted({str(x).strip() for x in names if str(x).strip()},key=str.casefold)
    out=[]; sel=','.join(repr(x) for x in fields)
    for i in range(0,len(names),chunk_size):
        chunk=names[i:i+chunk_size]
        conds=','.join("{"+repr(field)+","+lua_string(x)+"}" for x in chunk)
        q=f"bucket('{bucket}').select({sel}).where(bucket.Or({{{conds}}})).limit(5000).run()"
        out.extend(bquery(q))
        print(f'  {bucket}: queried names {min(i+chunk_size,len(names))}/{len(names)}',flush=True)
    return out


def clean_image(s):
    s=html.unescape(str(s or '')).strip()
    if s.startswith('[[') and s.endswith(']]'): s=s[2:-2].strip()
    s=s.split('|',1)[0].strip()
    if s.lower().startswith('file:'): s=s[5:].strip()
    return s


def canonical_score(row,wanted):
    image=clean_image((as_list(row.get('image')) or [''])[0])
    sub=str(row.get('page_name_sub') or '')
    page=str(row.get('page_name') or '')
    score=0
    if sub.casefold()==page.casefold(): score+=100
    if sub.casefold().endswith('#common'): score+=180
    if image.casefold()==(wanted+'.png').casefold(): score+=200
    if image and '(' not in image: score+=30
    bad=['historical','old','detail','equipped','stage ','empty','broken']
    if any(x in image.casefold() for x in bad): score-=500
    return score


def choose_rows(rows,wanted_names):
    grouped=defaultdict(list)
    for r in rows: grouped[str(r.get('page_name') or '').casefold()].append(r)
    resolved={}; missing=[]
    for wanted in wanted_names:
        candidates=grouped.get(str(wanted).casefold(),[])
        candidates=[r for r in candidates if any(clean_image(x) for x in as_list(r.get('image')))]
        if not candidates: missing.append(wanted); continue
        best=max(candidates,key=lambda r:canonical_score(r,str(wanted)))
        imgs=[clean_image(x) for x in as_list(best.get('image')) if clean_image(x)]
        if imgs: resolved[wanted]=(best,imgs[0])
        else: missing.append(wanted)
    return resolved,missing


def norm_resource(s):
    s=html.unescape(str(s or '')).casefold().replace('_',' ')
    s=re.sub(r'\([^)]*\)',' ',s)
    s=re.sub(r'\b(?:ore|rocks?|veins?|deposit|mine|mineral)\b',' ',s)
    s=re.sub(r'[^a-z0-9]+',' ',s)
    return ' '.join(s.split())


def skill_values(v):
    return {str(x).strip().casefold() for x in as_list(v) if str(x).strip()}


def main():
    out=Path('rs3_reference_support_plan'); out.mkdir(exist_ok=True)
    print('Reading recipe support entities...',flush=True)
    recipes=bucket_all('recipe',['page_name','uses_skill','uses_tool','uses_facility','production_json'])
    tools=defaultdict(set); facilities=defaultdict(set)
    for row in recipes:
        try: prod=json.loads(row.get('production_json') or '{}')
        except Exception: continue
        skills=[]
        for s in as_list(prod.get('skills')):
            if isinstance(s,dict) and s.get('name'): skills.append(str(s['name']).strip().capitalize())
        if not skills: skills=[str(x).strip().capitalize() for x in as_list(row.get('uses_skill')) if x]
        skills={x for x in skills if x}
        if not skills: continue

        tool_values=[]
        tool_values += explode_refs(prod.get('tool') if 'tool' in prod else prod.get('tools'))
        tool_values += explode_refs(row.get('uses_tool'))
        for name in tool_values:
            for s in skills: tools[name].add(s)

        facility_values=[]
        facility_values += explode_refs(prod.get('facility') if 'facility' in prod else prod.get('facilities'))
        facility_values += explode_refs(row.get('uses_facility'))
        for name in facility_values:
            for s in skills: facilities[name].add(s)

    # Generic classes are relationships, not distinct art assets; their concrete
    # pickaxes/hatchets/bows already enter via canonical recipe item sprites.
    generic_tools={name for name in tools if name.casefold() in GENERIC_TOOL_CLASSES}
    tool_targets={name for name in tools if name not in generic_tools}

    print(f'Tools={len(tools)} ({len(generic_tools)} generic classes) facilities={len(facilities)}',flush=True)
    tool_item_rows=query_names('infobox_item',['page_name','page_name_sub','item_name','image','item_id'],tool_targets)
    tool_resolved,tool_missing=choose_rows(tool_item_rows,tool_targets)
    tool_scene_rows=query_names('infobox_scenery',['page_name','page_name_sub','object_name','image','object_id','options'],tool_missing)
    tool_scene_resolved,tool_missing2=choose_rows(tool_scene_rows,tool_missing)
    tool_resolved.update(tool_scene_resolved)

    facility_scene_rows=query_names('infobox_scenery',['page_name','page_name_sub','object_name','image','object_id','options'],facilities)
    facility_resolved,facility_missing=choose_rows(facility_scene_rows,facilities)
    facility_item_rows=query_names('infobox_item',['page_name','page_name_sub','item_name','image','item_id'],facility_missing)
    facility_item_resolved,facility_missing2=choose_rows(facility_item_rows,facility_missing)
    facility_resolved.update(facility_item_resolved)

    print('Reading mining resources...',flush=True)
    resource_rows=bucket_all('resource_locations',['resource','skill','resource_location_type'])
    mining_resources=set()
    for r in resource_rows:
        if 'mining' not in skill_values(r.get('skill')): continue
        for resource in as_list(r.get('resource')):
            resource=clean_ref(resource)
            if resource: mining_resources.add(resource)
    mining_resources=sorted(mining_resources,key=str.casefold)

    mine_rows=bquery("bucket('infobox_scenery').select('page_name','page_name_sub','object_name','image','object_id','options').where('options','Mine').limit(5000).run()")
    by_norm=defaultdict(list)
    for r in mine_rows:
        for val in as_list(r.get('page_name'))+as_list(r.get('object_name')):
            n=norm_resource(val)
            if n: by_norm[n].append(r)
    mining_resolved={}; mining_missing=[]
    for resource in mining_resources:
        n=norm_resource(resource); candidates=by_norm.get(n,[])
        if not candidates:
            candidates=[r for key,rs in by_norm.items() if n and (n in key or key in n) for r in rs]
        candidates=[r for r in candidates if any(clean_image(x) for x in as_list(r.get('image')))]
        if not candidates: mining_missing.append(resource); continue
        best=max(candidates,key=lambda r:canonical_score(r,resource))
        image=clean_image((as_list(best.get('image')) or [''])[0])
        mining_resolved[resource]=(best,image)

    all_images=set(); rows_out=[]
    for kind,source,resolved in [('tool',tools,tool_resolved),('facility',facilities,facility_resolved)]:
        for name,(row,image) in resolved.items():
            all_images.add(image)
            rows_out.append({'kind':kind,'name':name,'image':image,'skills':' | '.join(sorted(source[name])),'page_name':row.get('page_name',''),'page_name_sub':row.get('page_name_sub','')})
    for name,(row,image) in mining_resolved.items():
        all_images.add(image)
        rows_out.append({'kind':'mining_node','name':name,'image':image,'skills':'Mining','page_name':row.get('page_name',''),'page_name_sub':row.get('page_name_sub','')})

    with (out/'manifest.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=['kind','name','image','skills','page_name','page_name_sub']); w.writeheader(); w.writerows(sorted(rows_out,key=lambda r:(r['kind'],r['name'].casefold())))
    for fn,vals in [('missing_tools.txt',tool_missing2),('generic_tool_classes.txt',generic_tools),('missing_facilities.txt',facility_missing2),('missing_mining_nodes.txt',mining_missing)]:
        (out/fn).write_text('\n'.join(sorted(vals,key=str.casefold)),encoding='utf-8')
    summary={
        'tools_total_refs':len(tools),'generic_tool_classes':len(generic_tools),'concrete_tool_targets':len(tool_targets),
        'tools_resolved':len(tool_resolved),'tools_missing':len(tool_missing2),
        'facilities':len(facilities),'facilities_resolved':len(facility_resolved),'facilities_missing':len(facility_missing2),
        'mining_resources':len(mining_resources),'mineable_scenery_rows':len(mine_rows),'mining_nodes_resolved':len(mining_resolved),'mining_nodes_missing':len(mining_missing),
        'unique_support_images':len(all_images),
    }
    (out/'plan.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    lines=['# RS3 support-reference plan','',f"- Concrete tools: {len(tool_resolved)}/{len(tool_targets)} resolved ({len(generic_tools)} generic classes intentionally skipped)",f"- Facilities: {len(facility_resolved)}/{len(facilities)} resolved",f"- Mining nodes: {len(mining_resolved)}/{len(mining_resources)} resolved",f"- Unique support images: {len(all_images)}"]
    (out/'SUMMARY.md').write_text('\n'.join(lines),encoding='utf-8')
    print('Support plan complete',flush=True)

if __name__=='__main__': main()
