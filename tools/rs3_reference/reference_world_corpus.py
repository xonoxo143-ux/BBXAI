#!/usr/bin/env python3
"""Build the compact non-inventory RS3 skilling reference corpus.

The corpus stores one 512px-or-smaller visual reference per canonical Wiki image
and a separate relationship table. That preserves many-to-one relationships without
duplicating large scenery files.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from reference_image_plan import resolve_imageinfo

API='https://runescape.wiki/api.php'
UA='BBXAI-RS3-World-Reference-Corpus/1.1 (personal research/reference)'
WORKERS=16
THUMB_WIDTH=512
SKILLS=[
    'Agility','Archaeology','Attack','Constitution','Construction','Cooking','Crafting',
    'Defence','Divination','Dungeoneering','Farming','Firemaking','Fishing','Fletching',
    'Herblore','Hunter','Invention','Magic','Mining','Necromancy','Prayer','Ranged',
    'Runecrafting','Slayer','Smithing','Strength','Summoning','Thieving','Woodcutting'
]


def api(**p):
    p.setdefault('format','json'); p.setdefault('formatversion','2')
    req=urllib.request.Request(API+'?'+urllib.parse.urlencode(p),headers={'User-Agent':UA})
    with urllib.request.urlopen(req,timeout=120) as r: return json.load(r)


def category_files(category):
    out=[]; cont={}
    while True:
        d=api(action='query',list='categorymembers',cmtitle=category,cmtype='file',cmnamespace='6',cmlimit='max',**cont)
        out += [x['title'].removeprefix('File:') for x in d.get('query',{}).get('categorymembers',[])]
        if 'continue' not in d: return out
        cont=d['continue']


def resolve_thumb_info(titles):
    """Resolve original metadata plus a <=512px thumbnail URL where available."""
    out={}
    for i in range(0,len(titles),50):
        chunk=titles[i:i+50]
        req_titles=['File:'+x for x in chunk]
        d=api(action='query',prop='imageinfo',redirects='1',titles='|'.join(req_titles),
              iiprop='url|mime|size|sha1',iiurlwidth=THUMB_WIDTH)
        q=d.get('query',{})
        alias={x:x for x in req_titles}
        for n in q.get('normalized',[]) or []: alias[n.get('from')]=n.get('to')
        for r in q.get('redirects',[]) or []:
            src=r.get('from'); dst=r.get('to')
            for k,v in list(alias.items()):
                if v==src: alias[k]=dst
            alias[src]=dst
        pages={p.get('title'):p for p in q.get('pages',[]) if p.get('title')}
        for image,req in zip(chunk,req_titles):
            target=alias.get(alias.get(req,req),alias.get(req,req))
            p=pages.get(target)
            ii=(p.get('imageinfo') or [None])[0] if p else None
            if ii: out[image]=ii
    return out


def skill_icons():
    candidates=[]; used_category=None
    for cat in ['Category:Skill icon images','Category:Skill icons','Category:Skills images']:
        files=category_files(cat)
        if files:
            candidates=files; used_category=cat; break
    chosen={}
    for skill in SKILLS:
        exact=[f for f in candidates if f.casefold() in {(skill+'.png').casefold(),(skill+' icon.png').casefold()}]
        if exact: chosen[skill]=sorted(exact,key=lambda x:(len(x),x.casefold()))[0]
    missing=[s for s in SKILLS if s not in chosen]
    fallback=[f for s in missing for f in (s+'.png',s+' icon.png')]
    fi=resolve_imageinfo(fallback) if fallback else {}
    for s in missing:
        for f in (s+'.png',s+' icon.png'):
            if f in fi: chosen[s]=f; break
    return chosen,used_category


def safe_local(image):
    clean=image.replace('/','／').replace('\\','＼')
    clean=re.sub(r'[<>:"|?*]','_',clean)
    return hashlib.sha1(image.encode()).hexdigest()[:10]+'__'+clean


def download(url,dest):
    dest.parent.mkdir(parents=True,exist_ok=True)
    req=urllib.request.Request(url,headers={'User-Agent':UA})
    with urllib.request.urlopen(req,timeout=120) as r: data=r.read()
    dest.write_bytes(data); return len(data)


def read_csv(path):
    with path.open(newline='',encoding='utf-8') as f: return list(csv.DictReader(f))


def run_planners():
    subprocess.run([sys.executable,'tools/rs3_reference/reference_support_plan.py'],check=True)
    subprocess.run([sys.executable,'tools/rs3_reference/reference_mining_nodes.py'],check=True)
    subprocess.run([sys.executable,'tools/rs3_wiki_scraper/rs3_wiki_pairs_v2.py','--profile','woodcutting','--out','rs3_world_tree_pairs'],check=True)


def main():
    root=Path('rs3_world_reference_corpus'); root.mkdir(exist_ok=True)
    image_dir=root/'images'; image_dir.mkdir(exist_ok=True)
    print('Running narrow world/support planners...',flush=True)
    run_planners()

    relationships=[]
    support=read_csv(Path('rs3_reference_support_plan/manifest.csv'))
    for r in support:
        image=r.get('image','').strip()
        if image:
            relationships.append({'kind':r.get('kind','support'),'name':r.get('name',''),'image':image,'skills':r.get('skills',''),'source':'recipe_support'})

    mining=read_csv(Path('rs3_mining_node_plan/manifest.csv'))
    mining=[r for r in mining if int(r.get('score') or 0)>=300]
    for r in mining:
        image=r.get('image','').strip()
        if image:
            relationships.append({'kind':'mining_node','name':r.get('resource',''),'image':image,'skills':'Mining','source':'resource_locations'})

    pair_manifest=read_csv(Path('rs3_world_tree_pairs/_manifest.csv'))
    tree_seen=set()
    for r in pair_manifest:
        if r.get('side')!='tree': continue
        image=r['file_title'].removeprefix('File:')
        key=(r.get('folder_key',''),image)
        if key in tree_seen: continue
        tree_seen.add(key)
        relationships.append({'kind':'woodcutting_tree','name':r.get('folder_key',''),'image':image,'skills':'Woodcutting','source':'tree_log_pair'})

    icons,icon_category=skill_icons()
    for skill,image in icons.items():
        relationships.append({'kind':'skill_icon','name':skill,'image':image,'skills':skill,'source':icon_category or 'filename_fallback'})

    wanted=sorted({r['image'] for r in relationships},key=str.casefold)
    info=resolve_thumb_info(wanted)
    missing=sorted(set(wanted)-set(info),key=str.casefold)
    print(f'World/support unique images to download: {len(info)}/{len(wanted)}',flush=True)

    image_manifest=[]; failures=[]; total=0; futures={}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for image,meta in info.items():
            url=meta.get('thumburl') or meta.get('url')
            dest=image_dir/safe_local(image)
            futures[ex.submit(download,url,dest)]=(image,meta,url,dest)
        done=0
        for fut in as_completed(futures):
            image,meta,url,dest=futures[fut]
            try:
                size=fut.result(); total+=size
                image_manifest.append({'image':image,'local_path':str(dest.relative_to(root)),'download_url':url,'original_url':meta.get('url',''),'mime':meta.get('mime',''),'original_width':meta.get('width',''),'original_height':meta.get('height',''),'bytes':size,'wiki_sha1':meta.get('sha1','')})
            except Exception as exc:
                failures.append({'image':image,'error':repr(exc)})
            done+=1
            if done%100==0 or done==len(futures): print(f'  downloads {done}/{len(futures)}',flush=True)

    image_manifest.sort(key=lambda r:r['image'].casefold())
    relationships.sort(key=lambda r:(r['kind'],r['name'].casefold(),r['image'].casefold()))
    with (root/'image_manifest.csv').open('w',newline='',encoding='utf-8') as f:
        fields=['image','local_path','download_url','original_url','mime','original_width','original_height','bytes','wiki_sha1']
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(image_manifest)
    with (root/'relationships.csv').open('w',newline='',encoding='utf-8') as f:
        fields=['kind','name','image','skills','source']; w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(relationships)

    for src,dst in [
        ('rs3_reference_support_plan/missing_tools.txt','audit_missing_tools.txt'),
        ('rs3_reference_support_plan/generic_tool_classes.txt','audit_generic_tool_classes.txt'),
        ('rs3_reference_support_plan/missing_facilities.txt','audit_context_not_resolved_as_art.txt'),
        ('rs3_mining_node_plan/missing.txt','audit_missing_mining_nodes.txt')]:
        Path(root/dst).write_text(Path(src).read_text(encoding='utf-8'),encoding='utf-8')
    (root/'missing_image_titles.txt').write_text('\n'.join(missing),encoding='utf-8')
    (root/'download_failures.json').write_text(json.dumps(failures,indent=2),encoding='utf-8')

    counts={}
    for r in relationships: counts[r['kind']]=counts.get(r['kind'],0)+1
    summary={'thumbnail_width':THUMB_WIDTH,'relationship_rows':len(relationships),'unique_images_planned':len(wanted),'images_downloaded':len(image_manifest),'counts_by_kind':counts,'skill_icons':len(icons),'skill_icon_category':icon_category,'unresolved_image_titles':len(missing),'download_failures':len(failures),'downloaded_bytes':total,'downloaded_mb_decimal':round(total/1_000_000,2)}
    (root/'corpus_info.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    (root/'README.md').write_text('# RS3 compact world/support reference corpus\n\n'+f'- unique images: {len(image_manifest)}\n- relationship rows: {len(relationships)}\n- source bytes: {round(total/1_000_000,2)} MB\n- thumbnail cap: {THUMB_WIDTH}px\n'+'\n'.join(f'- {k}: {v} relationships' for k,v in sorted(counts.items()))+f"\n- unresolved image titles: {len(missing)}\n- download failures: {len(failures)}\n",encoding='utf-8')
    print(summary,flush=True)
    if failures: raise SystemExit(f'{len(failures)} downloads failed')

if __name__=='__main__': main()
