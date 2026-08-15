#!/usr/bin/env python3
"""Download the canonical inventory-art corpus referenced by active skill recipes.

This is a reference archive, not a game asset pack. It keeps exact current RS3 Wiki
images plus a normalized recipe graph so later pixel-art work can be selected by
skill/entity without scraping the Wiki again.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from reference_image_plan import bucket_all, as_list, clean_image, resolve_imageinfo

UA='BBXAI-RS3-Recipe-Reference-Corpus/1.0 (personal research/reference)'
WORKERS=24


def safe_local_name(image: str) -> str:
    cleaned=image.replace('/','／').replace('\\','＼').replace('\x00','')
    cleaned=re.sub(r'[<>:"|?*]','_',cleaned)
    prefix=hashlib.sha1(image.encode('utf-8')).hexdigest()[:10]
    return f'{prefix}__{cleaned}'


def download_one(url: str, dest: Path):
    dest.parent.mkdir(parents=True,exist_ok=True)
    req=urllib.request.Request(url,headers={'User-Agent':UA})
    with urllib.request.urlopen(req,timeout=120) as r:
        data=r.read()
    dest.write_bytes(data)
    return len(data)


def skill_names(prod,row):
    skills=[]
    for s in as_list(prod.get('skills')):
        if isinstance(s,dict) and s.get('name'): skills.append(str(s['name']).strip())
    if not skills: skills=[str(x).strip() for x in as_list(row.get('uses_skill')) if x]
    return sorted({s[:1].upper()+s[1:] for s in skills if s})


def normal_entity(ent):
    if not isinstance(ent,dict): return None
    out={k:v for k,v in ent.items() if k in {'name','page','quantity','amount','id','note'} and v is not None}
    if ent.get('image'):
        out['image']=clean_image(ent['image'])
    return out


def main():
    root=Path('rs3_reference_corpus')
    item_dir=root/'inventory'
    root.mkdir(exist_ok=True); item_dir.mkdir(exist_ok=True)

    print('Loading structured recipe graph...',flush=True)
    rows=bucket_all('recipe',['page_name','page_name_sub','uses_skill','source_template','production_json'])
    images={}
    graph=[]
    parse_failures=[]

    for row in rows:
        raw=row.get('production_json') or '{}'
        try: prod=json.loads(raw)
        except Exception as exc:
            parse_failures.append({'page':row.get('page_name'),'error':repr(exc)}); continue
        skills=skill_names(prod,row)
        if not skills: continue

        materials=[x for x in (normal_entity(e) for e in as_list(prod.get('materials'))) if x]
        outputs=[x for x in (normal_entity(e) for e in as_list(prod.get('outputs'))) if x]
        for role,ents in [('material',materials),('output',outputs)]:
            for ent in ents:
                im=ent.get('image')
                if not im or '(historical)' in im.casefold(): continue
                rec=images.setdefault(im,{'skills':set(),'roles':set(),'names':set(),'pages':set()})
                rec['skills'].update(skills); rec['roles'].add(role)
                if ent.get('name'): rec['names'].add(str(ent['name']))
                if row.get('page_name'): rec['pages'].add(str(row['page_name']))

        graph.append({
            'page_name':row.get('page_name'),
            'page_name_sub':row.get('page_name_sub'),
            'skills':skills,
            'materials':materials,
            'outputs':outputs,
            'tool':prod.get('tool',prod.get('tools')),
            'facility':prod.get('facility',prod.get('facilities')),
            'process':prod.get('process'),
            'method':prod.get('method'),
            'ticks':prod.get('ticks'),
            'source_template':row.get('source_template'),
        })

    titles=sorted(images,key=str.casefold)
    print(f'Resolving {len(titles)} unique canonical image names...',flush=True)
    info=resolve_imageinfo(titles)
    resolved=[x for x in titles if x in info]
    missing=[x for x in titles if x not in info]
    print(f'Resolved {len(resolved)}/{len(titles)}; downloading...',flush=True)

    manifest=[]; failures=[]; total=0
    futures={}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for image in resolved:
            meta=info[image]
            local='inventory/'+safe_local_name(image)
            fut=ex.submit(download_one,meta['url'],root/local)
            futures[fut]=(image,local,meta)
        done=0
        for fut in as_completed(futures):
            image,local,meta=futures[fut]
            try:
                size=fut.result(); total+=size
                rec=images[image]
                manifest.append({
                    'image':image,'local_path':local,'source_url':meta.get('url',''),
                    'mime':meta.get('mime',''),'width':meta.get('width',''),'height':meta.get('height',''),
                    'bytes':size,'wiki_sha1':meta.get('sha1',''),
                    'roles':' | '.join(sorted(rec['roles'])),'skills':' | '.join(sorted(rec['skills'])),
                    'names':' | '.join(sorted(rec['names'])),'source_page_count':len(rec['pages']),
                })
            except Exception as exc:
                failures.append({'image':image,'error':repr(exc)})
            done+=1
            if done%500==0 or done==len(futures): print(f'  downloads {done}/{len(futures)}',flush=True)

    manifest.sort(key=lambda r:r['image'].casefold())
    with (root/'inventory_manifest.csv').open('w',newline='',encoding='utf-8') as f:
        fields=['image','local_path','source_url','mime','width','height','bytes','wiki_sha1','roles','skills','names','source_page_count']
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(manifest)
    with (root/'recipes.jsonl').open('w',encoding='utf-8') as f:
        for recipe in graph: f.write(json.dumps(recipe,ensure_ascii=False,separators=(',',':'))+'\n')
    (root/'missing_images.txt').write_text('\n'.join(missing),encoding='utf-8')
    (root/'download_failures.json').write_text(json.dumps(failures,indent=2),encoding='utf-8')
    summary={
        'recipe_rows_source':len(rows),'skill_recipe_records':len(graph),
        'planned_unique_images':len(titles),'resolved_images':len(resolved),'downloaded_images':len(manifest),
        'missing_image_titles':len(missing),'download_failures':len(failures),
        'downloaded_bytes':total,'downloaded_mb_decimal':round(total/1_000_000,2),
        'parse_failures':len(parse_failures),
    }
    (root/'corpus_info.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    (root/'README.md').write_text(
        '# RS3 canonical skilling reference corpus\n\n'
        'Generated from the RuneScape Wiki structured recipe database. Inventory PNGs are kept as canon visual references only.\n\n'
        f"- Skill-bearing recipe records: {len(graph)}\n- Unique inventory references downloaded: {len(manifest)}\n"
        f"- Missing Wiki image titles: {len(missing)}\n- Download failures: {len(failures)}\n- Source bytes: {round(total/1_000_000,2)} MB\n",
        encoding='utf-8')
    print(summary,flush=True)
    if failures: raise SystemExit(f'{len(failures)} image downloads failed')

if __name__=='__main__': main()
