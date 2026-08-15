#!/usr/bin/env python3
"""Build the non-inventory RS3 skilling reference corpus.

Combines narrowly-derived world/support art:
- Woodcutting canonical tree renders selected by tree<->log pairing
- Mining canonical mineable node renders selected from resource_locations
- concrete recipe tools and resolvable facilities
- canonical skill icons when exposed by the Wiki image taxonomy
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from reference_image_plan import resolve_imageinfo

API='https://runescape.wiki/api.php'
UA='BBXAI-RS3-World-Reference-Corpus/1.0 (personal research/reference)'
WORKERS=16
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


def skill_icons():
    candidates=[]
    used_category=None
    for cat in ['Category:Skill icon images','Category:Skill icons','Category:Skills images']:
        files=category_files(cat)
        if files:
            candidates=files; used_category=cat; break
    chosen={}
    for skill in SKILLS:
        exact=[f for f in candidates if f.casefold() in {(skill+'.png').casefold(),(skill+' icon.png').casefold()}]
        if exact: chosen[skill]=sorted(exact,key=lambda x:(len(x),x.casefold()))[0]
    # Filename fallback is safe to try; only resolved existing files are kept.
    missing=[s for s in SKILLS if s not in chosen]
    fallback=[]
    for s in missing:
        fallback += [s+'.png',s+' icon.png']
    fi=resolve_imageinfo(fallback) if fallback else {}
    for s in missing:
        for f in [s+'.png',s+' icon.png']:
            if f in fi:
                chosen[s]=f; break
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
    print('Running narrow world/support planners...',flush=True)
    run_planners()

    support=read_csv(Path('rs3_reference_support_plan/manifest.csv'))
    mining=read_csv(Path('rs3_mining_node_plan/manifest.csv'))
    mining=[r for r in mining if int(r.get('score') or 0)>=300]  # reject accidental zero-score fuzzy matches

    # Copy only the tree side of pair output; logs are already in inventory corpus.
    pair_manifest=read_csv(Path('rs3_world_tree_pairs/_manifest.csv'))
    tree_rows=[r for r in pair_manifest if r.get('side')=='tree']
    manifest=[]
    for r in tree_rows:
        src=Path('rs3_world_tree_pairs')/r['path']
        image=r['file_title'].removeprefix('File:')
        dest=root/'world'/'woodcutting'/'trees'/safe_local(image)
        dest.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(src,dest)
        manifest.append({'kind':'woodcutting_tree','name':r.get('folder_key',''),'image':image,'local_path':str(dest.relative_to(root)),'skills':'Woodcutting','source_url':r.get('source_url',''),'bytes':dest.stat().st_size})

    wanted={}
    for r in support:
        image=r.get('image','').strip()
        if not image: continue
        kind=r.get('kind','support')
        local_kind='tools' if kind=='tool' else 'facilities'
        wanted.setdefault(image,{'kind':kind,'name':r.get('name',''),'skills':r.get('skills',''),'dest':root/'support'/local_kind/safe_local(image)})
    for r in mining:
        image=r.get('image','').strip()
        if not image: continue
        wanted.setdefault(image,{'kind':'mining_node','name':r.get('resource',''),'skills':'Mining','dest':root/'world'/'mining'/'nodes'/safe_local(image)})

    icons,icon_category=skill_icons()
    for skill,image in icons.items():
        wanted.setdefault(image,{'kind':'skill_icon','name':skill,'skills':skill,'dest':root/'ui'/'skill_icons'/safe_local(image)})

    info=resolve_imageinfo(sorted(wanted,key=str.casefold))
    missing=[x for x in wanted if x not in info]
    print(f'World/support unique images to download: {len(info)}/{len(wanted)}',flush=True)
    failures=[]; total=0
    futures={}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for image,meta in info.items():
            rec=wanted[image]
            futures[ex.submit(download,meta['url'],rec['dest'])]=(image,meta,rec)
        done=0
        for fut in as_completed(futures):
            image,meta,rec=futures[fut]
            try:
                size=fut.result(); total+=size
                manifest.append({'kind':rec['kind'],'name':rec['name'],'image':image,'local_path':str(rec['dest'].relative_to(root)),'skills':rec['skills'],'source_url':meta.get('url',''),'bytes':size})
            except Exception as exc:
                failures.append({'image':image,'error':repr(exc)})
            done+=1
            if done%100==0 or done==len(futures): print(f'  downloads {done}/{len(futures)}',flush=True)

    manifest.sort(key=lambda r:(r['kind'],r['name'].casefold(),r['image'].casefold()))
    with (root/'manifest.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=['kind','name','image','local_path','skills','source_url','bytes']); w.writeheader(); w.writerows(manifest)
    shutil.copy2('rs3_reference_support_plan/missing_tools.txt',root/'audit_missing_tools.txt')
    shutil.copy2('rs3_reference_support_plan/generic_tool_classes.txt',root/'audit_generic_tool_classes.txt')
    shutil.copy2('rs3_reference_support_plan/missing_facilities.txt',root/'audit_context_not_resolved_as_art.txt')
    shutil.copy2('rs3_mining_node_plan/missing.txt',root/'audit_missing_mining_nodes.txt')
    (root/'missing_image_titles.txt').write_text('\n'.join(missing),encoding='utf-8')
    (root/'download_failures.json').write_text(json.dumps(failures,indent=2),encoding='utf-8')
    counts={}
    for r in manifest: counts[r['kind']]=counts.get(r['kind'],0)+1
    summary={'tree_references':len(tree_rows),'mining_node_references':len(mining),'skill_icons':len(icons),'skill_icon_category':icon_category,'support_source_rows':len(support),'manifest_rows':len(manifest),'counts_by_kind':counts,'unique_download_targets':len(wanted),'unresolved_image_titles':len(missing),'download_failures':len(failures),'downloaded_bytes_excluding_pre_downloaded_trees':total}
    (root/'corpus_info.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    (root/'README.md').write_text('# RS3 world/support reference corpus\n\n'+ '\n'.join(f'- {k}: {v}' for k,v in counts.items()) + f"\n- unresolved image titles: {len(missing)}\n- download failures: {len(failures)}\n",encoding='utf-8')
    print(summary,flush=True)
    if failures: raise SystemExit(f'{len(failures)} downloads failed')

if __name__=='__main__': main()
