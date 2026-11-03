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
import math
from xml.etree import ElementTree as ET
from drape_surfaces import vertex_geodetic
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




def real_ac_model(folder: Path, name: str):
    """Follow only the generated FlightGear wrapper path; never guess external assets."""
    model=folder/name
    if model.suffix.lower()=='.ac':
        return model
    if model.suffix.lower()!='.xml':
        return None
    try:
        inner=ET.parse(model).getroot().findtext('path')
        if not inner or Path(inner).name!=inner or not inner.lower().endswith('.ac'):
            return None
        return folder/inner
    except (OSError,ET.ParseError):
        return None


def contact_points(path: Path, max_points=32, band_m=0.20):
    """Ground-contact candidates in local X/Y/Z coordinates (AC3D, metres).

    Sample representative vertices near the lowest point, spread over the model
    footprint. Avoid loading triangle connectivity and preserve every AC file.
    """
    if not path or not path.is_file():
        raise ValueError(f'AC3D model missing: {path}')
    vertices=[]
    with path.open(encoding='utf8',errors='replace') as fh:
        lines=iter(fh)
        for line in lines:
            if line.startswith('numvert '):
                count=int(line.split()[1])
                if count>3_000_000:raise ValueError(f'AC3D vertex limit exceeded: {path}')
                for _ in range(count):
                    x,y,z=map(float,next(lines).split()[:3])
                    if not all(math.isfinite(v) for v in (x,y,z)):
                        raise ValueError(f'Invalid AC3D vertex: {path}')
                    vertices.append((x,y,z))
                break
    if not vertices:
        raise ValueError(f'AC3D vertices missing: {path}')
    ymin=min(v[1] for v in vertices)
    candidates=[v for v in vertices if v[1]<=ymin+band_m]
    # Deterministic spatial coverage, not first-N vertices only.
    if len(candidates)>max_points:
        xs=[p[0] for p in candidates];zs=[p[2] for p in candidates]
        xrange=max(xs)-min(xs);zrange=max(zs)-min(zs)
        resolution=max(1,int(math.sqrt(max_points)))
        bins={}
        for v in candidates:
            ix=min(resolution-1,int((v[0]-min(xs))/max(xrange,1e-10)*resolution))
            iz=min(resolution-1,int((v[2]-min(zs))/max(zrange,1e-10)*resolution))
            key=(ix,iz)
            if key not in bins or v[1]<bins[key][1]:bins[key]=v
        candidates=list(bins.values())
        if len(candidates)>max_points:
            stride=len(candidates)/max_points
            candidates=[candidates[int(i*stride)] for i in range(max_points)]
    return ymin,candidates


def is_draped_model(model):
    return '_draped.ac' in model


def is_lights_model(model):
    return model.endswith('_lights.xml') or model.endswith('_lights.ac')


def root_signature(model):
    return model.split('_',1)[0].split('.',1)[0]


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
    ap=argparse.ArgumentParser(description='BTG altitude and AC3D footprint alignment for XPlane2FlightGear v0.3.15.0')
    ap.add_argument('--scenery',type=Path,default=Path.home()/'Desktop/TestAirport')
    ap.add_argument('--apply',action='store_true',help='Rewrite STG after preview, keeping .pre-elevation-v037 copies')
    ap.add_argument('--restore',action='store_true',help='Restore previous STGs from backups')
    ap.add_argument('--clearance',type=float,default=0,help='Uniform vertical offset above sampled terrain, metres')
    ap.add_argument('--margin',type=float,default=1200,help='BTG region expanded around anchors, metres')
    ap.add_argument('--model-base',choices=('keep','min','footprint'),default='footprint',help='footprint: raise ground-contact geometry to sampled BTG footprint; min: place lowest vertex at BTG anchor; keep: historical behavior')
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
    # Prepare ground-contact candidates BEFORE loading the sampler, so its index
    # includes all footprint locations (not just DSF anchor coordinates).
    geometry_cache={}
    candidates_by_entry={}
    sample_locations=list(anchors)
    geometry_errors=[]
    for entry in entries:
        model=entry['model']
        if is_draped_model(model) or is_lights_model(model):continue
        model_path=real_ac_model(entry['stg'].parent,model)
        if model_path is None:
            geometry_errors.append({'model':model,'reason':'unsupported or missing model wrapper'})
            continue
        if model_path not in geometry_cache:
            try: geometry_cache[model_path]=contact_points(model_path)
            except (ValueError,OSError,StopIteration) as exc:
                geometry_errors.append({'model':model,'reason':str(exc)})
                continue
        ymin,vertices=geometry_cache[model_path]
        if args.model_base=='footprint':
            coords=[(*vertex_geodetic(type('P',(),{'lon':entry['lon'],'lat':entry['lat'],'heading':entry['heading']})(),v),v[1]) for v in vertices]
            candidates_by_entry[(entry['stg'],entry['line'])]=(ymin,coords)
            sample_locations.extend((lon,lat) for lon,lat,y in coords)
        else:
            candidates_by_entry[(entry['stg'],entry['line'])]=(ymin,[])
    sampler=TerrainSampler(root/'Terrain',origin,margin=args.margin)
    sampler.load(sample_locations)
    actions=[];unresolved=[];groups=[];footprint_stats=[]
    # Share the geometry lift for associated night-light wrappers at the same
    # DSF position; draped AC3D stays at exact BTG anchor for later projection.
    lifts={}
    pending_lights=[]
    for (lon,lat),members in sorted(anchors.items()):
        hit=sampler.query(lon,lat)
        if hit is None:
            unresolved.append({'lon':lon,'lat':lat,'models':[m['model'] for m in members], 'reason':'No BTG triangle covers anchor'})
            continue
        group={'lon':lon,'lat':lat,'terrain_msl':round(hit.altitude,3),'material':hit.material,
               'btg':hit.path,'objects':len(members)}
        groups.append(group)
        for entry in members:
            model=entry['model']
            action=dict(entry)
            action['terrain_msl']=hit.altitude
            lift=0.0
            if is_lights_model(model):
                pending_lights.append((action,lon,lat))
                continue
            if not is_draped_model(model) and args.model_base!='keep':
                data=candidates_by_entry.get((entry['stg'],entry['line']))
                if data is None:
                    unresolved.append({'lon':lon,'lat':lat,'models':[model], 'reason':'Missing/unsupported AC3D geometry'})
                    continue
                ymin, coords=data
                if args.model_base=='min':
                    lift=max(0.,-ymin)
                else:
                    deltas=[]
                    for vlon,vlat,y in coords:
                        local_hit=sampler.query(vlon,vlat)
                        if local_hit is not None:
                            deltas.append(local_hit.altitude-hit.altitude-y)
                    if not deltas:
                        unresolved.append({'lon':lon,'lat':lat,'models':[model], 'reason':'No BTG contact samples on object footprint'})
                        continue
                    # Ground points shouldn't be underground. Preserve intentional
                    # positive elevation offsets; never move objects downward.
                    lift=max(0.,max(deltas))
                    footprint_stats.append({'model':model,'lon':lon,'lat':lat,'lift_m':round(lift,3),
                                            'model_min_y_m':round(ymin,3),'contact_hits':len(deltas),'contact_candidates':len(coords)})
                lifts[(entry['stg'],lon,lat,entry['heading'],root_signature(model))]=lift
            action['msl']=hit.altitude+entry['agl']+args.clearance+lift
            action['model_base_offset']=(-lift if not is_draped_model(model) else 0.0)
            action['footprint_lift_m']=lift
            actions.append(action)
    for action,lon,lat in pending_lights:
        hit=sampler.query(lon,lat)
        lift=lifts.get((action['stg'],lon,lat,action['heading'],root_signature(action['model'])),0.0)
        action['msl']=hit.altitude+action['agl']+args.clearance+lift
        action['model_base_offset']=-lift
        action['footprint_lift_m']=lift
        actions.append(action)
    out=args.report or root/'elevation-report.json'
    report={'version':'0.3.15.0','mode':'BTG anchor and AC3D ground-contact footprint alignment',
            'status':'partial' if unresolved else 'ready', 'applied':False,
            'btg_files_used':sampler.files,'btg_files_errors':sampler.errors,
            'triangles_indexed':sampler.triangle_count,'placement_total':len(entries),
            'placement_resolved':len(actions),'placement_unresolved':sum(len(i['models']) for i in unresolved),
            'unique_anchor_total':len(anchors),'unique_anchor_resolved':len(groups),
            'groups':groups,'unresolved':unresolved,'geometry_errors':geometry_errors,'footprint_stats':footprint_stats,'clearance_m':args.clearance,'model_base':args.model_base,
            'preview':[{k:v for k,v in a.items() if k not in ('stg',)}|{'stg':str(a['stg'])} for a in actions[:30]]}
    reason=None
    if args.apply:
        if unresolved or sampler.errors or geometry_errors:
            reason=f'Refusing partial apply: {len(unresolved)} unresolved placements, {len(sampler.errors)} unreadable BTGs, {len(geometry_errors)} geometry errors'
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
