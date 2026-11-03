#!/usr/bin/env python3
"""Recompute FlightGear static model MSL altitude from generated BTG triangles.

No OBJ/AC3D changes. Same exact DSF geographic anchor/heading. Groups sharing
an anchor receive identical MSL reference, preserving multi-part airport models.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import shutil
import statistics
from btg_elevation import TerrainSampler


def parse_stgs(folder):
    entries=[]
    for stg in sorted((folder/'Objects').rglob('*.stg')):
        for idx,line in enumerate(stg.read_text(encoding='utf8').splitlines()):
            p=line.split()
            if len(p)>=6 and p[0]=='OBJECT_STATIC_AGL':
                try:
                    lon,lat,agl,heading=map(float,p[2:6])
                except ValueError:
                    continue
                entries.append(dict(stg=stg,line=idx,model=p[1],lon=lon,lat=lat,agl=agl,heading=heading))
    return entries


def apply_changes(root, actions, restore=False):
    paths=sorted({a['stg'] for a in actions})
    changed=0
    if not restore:
        for path in paths:
            backup=path.with_suffix('.stg.pre-elevation-v037')
            if backup.exists(): raise RuntimeError(f'Backup deja present: {backup}. Restaurer avant de recalculer.')
    for path in paths:
        backup=path.with_suffix('.stg.pre-elevation-v037')
        if restore:
            if backup.exists():
                shutil.copy2(backup,path)
                changed+=1
            continue
        if backup.exists():
            raise RuntimeError(f'Backup deja present: {backup}. Restaurer avant de recalculer.')
        original=path.read_text(encoding='utf8')
        lines=original.splitlines()
        for a in actions:
            if a['stg']!=path: continue
            words=lines[a['line']].split()
            assert words[0]=='OBJECT_STATIC_AGL'
            words[0]='OBJECT_STATIC'
            words[4]=f"{a['msl']:.3f}"
            lines[a['line']]=' '.join(words)
        backup.write_text(original,encoding='utf8')
        path.write_text('\n'.join(lines)+'\n',encoding='utf8')
        changed+=1
    return changed


def main():
    ap=argparse.ArgumentParser(description='BTG altitude sampling for XPlane2FlightGear v0.3.7')
    ap.add_argument('--scenery',type=Path,default=Path.home()/'Desktop/TestAirport')
    ap.add_argument('--apply',action='store_true',help='Rewrite STG after preview, keeping .pre-elevation-v037 copies')
    ap.add_argument('--restore',action='store_true',help='Restore previous STGs from backups')
    ap.add_argument('--clearance',type=float,default=0,help='Uniform vertical offset above sampled terrain, metres')
    ap.add_argument('--margin',type=float,default=1200,help='BTG region expanded around anchors, metres')
    ap.add_argument('--model-base',choices=('keep','min'),default='keep',help='keep: preserve OBJ8 vertical origin; min: place lowest vertex at terrain (use only if needed)')
    ap.add_argument('--report',type=Path,help='JSON report path')
    args=ap.parse_args()
    root=args.scenery.expanduser().resolve()
    if not (root/'Objects').is_dir(): ap.error(f'Objects folder missing: {root}')
    if args.apply and args.restore: ap.error('--apply and --restore cannot be combined')
    if args.restore:
        paths=list((root/'Objects').rglob('*.stg.pre-elevation-v037'))
        if not paths: ap.error('No .pre-elevation-v037 backups found')
        for backup in paths:
            # .with_suffix() is wrong for a multi-part backup name; remove exact suffix.
            original=Path(str(backup).removesuffix('.pre-elevation-v037'))
            shutil.copy2(backup,original)
            backup.unlink()
        print(f'Restored {len(paths)} STG files')
        return
    if not (root/'Terrain').is_dir(): ap.error(f'Terrain folder missing: {root}')
    entries=parse_stgs(root)
    if not entries: ap.error('No OBJECT_STATIC_AGL placements found; already converted?')
    anchors=defaultdict(list)
    for entry in entries:
        anchors[(entry['lon'],entry['lat'])].append(entry)
    origin=(statistics.median(k[0] for k in anchors),statistics.median(k[1] for k in anchors))
    sampler=TerrainSampler(root/'Terrain',origin,margin=args.margin)
    sampler.load(list(anchors))
    actions=[]; unresolved=[]; groups=[]
    bbox_cache={}
    for (lon,lat),members in sorted(anchors.items()):
        hit=sampler.query(lon,lat)
        if hit is None:
            unresolved.append({'lon':lon,'lat':lat,'models':[m['model'] for m in members], 'reason':'No BTG triangle covers anchor'})
            continue
        group={'lon':lon,'lat':lat,'terrain_msl':round(hit.altitude,3),'material':hit.material,
               'btg':hit.path,'objects':len(members)}
        groups.append(group)
        for entry in members:
            base=0.0
            if args.model_base=='min':
                path=entry['stg'].parent/entry['model']
                if path not in bbox_cache:
                    try:
                        lines=path.read_text(errors='replace').splitlines()
                        ys=[]
                        for i,line in enumerate(lines):
                            if line.startswith('numvert '):
                                n=int(line.split()[1])
                                ys.extend(float(row.split()[1]) for row in lines[i+1:i+1+n])
                        bbox_cache[path]=min(ys) if ys else 0.0
                    except (OSError,ValueError,IndexError): bbox_cache[path]=0.0
                base=bbox_cache[path]
            action=dict(entry)
            action['msl']=hit.altitude+entry['agl']+args.clearance-base
            action['terrain_msl']=hit.altitude
            action['model_base_offset']=base
            actions.append(action)
    out=args.report or root/'elevation-report.json'
    report={'version':'0.3.7','mode':'BTG triangle interpolation (ECEF to ENU)',
            'status':'partial' if unresolved else 'ready', 'applied':False,
            'btg_files_used':sampler.files,'btg_files_errors':sampler.errors,
            'triangles_indexed':sampler.triangle_count,'placement_total':len(entries),
            'placement_resolved':len(actions),'placement_unresolved':sum(len(i['models']) for i in unresolved),
            'unique_anchor_total':len(anchors),'unique_anchor_resolved':len(groups),
            'groups':groups,'unresolved':unresolved,'clearance_m':args.clearance,'model_base':args.model_base,
            'preview':[{k:v for k,v in a.items() if k not in ('stg',)}|{'stg':str(a['stg'])} for a in actions[:30]]}
    reason=None
    if args.apply:
        if unresolved or sampler.errors:
            reason=f'Refusing partial apply: {len(unresolved)} unresolved anchors and {len(sampler.errors)} unreadable BTGs'
        elif not actions:
            reason='Nothing to apply'
        else:
            changed=apply_changes(root,actions)
            report['applied']=True
            report['stg_changed']=changed
    if reason: report['apply_blocked']=reason
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf8')
    print(f"BTG: {len(sampler.files)} files, {sampler.triangle_count} triangles; resolved {len(actions)}/{len(entries)} objects; unresolved {len(unresolved)} anchors")
    print(f"Report: {out}")
    print('STGs modified with backups' if report['applied'] else 'PREVIEW ONLY: add --apply to commit changes')
    if reason: raise SystemExit(reason)

if __name__=='__main__':
    main()
