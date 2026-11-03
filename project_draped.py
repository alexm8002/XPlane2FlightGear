#!/usr/bin/env python3
"""v0.3.11: project OBJ8 ATTR_draped triangles onto generated BTG in a fresh build.

Reads the explicit _draped models exported by xp2fg.py (not a heuristic). Maintains
model pivot, STG heading, texture, UVs. Fail closed on missing terrain, not white planes.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from statistics import median
from btg_elevation import TerrainSampler
from drape_surfaces import AC, Face, placements, subdivide, vertex_geodetic


def build(root, max_edge=25., max_depth=9, clearance=.07, max_faces=800000):
    ps=[p for p in placements(root) if p.model.endswith('_draped.ac')]
    report={'version':'0.3.11','draped_placements':len(ps),'projected':0,'disabled':0,'faces_written':0,'failures':[]}
    if not ps:return report
    # Model instances can have distinct terrain heights/headings. Create unique outputs.
    cache={}
    position_samples=[]
    for p in ps:
        ac=cache.setdefault(p.stg.parent/p.model, AC(p.stg.parent/p.model))
        position_samples.append((p.lon,p.lat))
        xs=[v[0] for v in ac.vertices];zs=[v[2] for v in ac.vertices]
        if xs and zs:
            # Bounding box corners ensure the sampler indexes ALL triangle coverage.
            for x in (min(xs),max(xs)):
                for z in (min(zs),max(zs)):
                    position_samples.append(vertex_geodetic(p,(x,0,z)))
    lon0=median(x for x,y in position_samples)
    lat0=median(y for x,y in position_samples)
    sampler=TerrainSampler(root/'Terrain',(lon0,lat0),margin=300)
    sampler.load(position_samples)
    report['btg_triangles']=sampler.triangle_count
    report['btg_errors']=sampler.errors
    if sampler.errors:raise ValueError('Unusable BTG(s): '+str(sampler.errors[:3]))
    for n,p in enumerate(ps):
        ac=cache[p.stg.parent/p.model]
        dest=p.stg.parent/(p.model[:-3]+f'.terrain-{n:04d}.ac')
        try:
            anchor=sampler.query(p.lon,p.lat)
            if anchor is None:raise ValueError('missing BTG at anchor')
            anchor_msl=p.altitude if p.keyword=='OBJECT_STATIC' else anchor.altitude+p.altitude
            vertices=[];faces=[]
            samples={}
            for face in ac.faces:
                tri=[(ac.vertices[j],(u,v)) for j,u,v in face.refs]
                for fragment in subdivide(tri,max_edge,max_depth):
                    if len(faces)>=max_faces:raise ValueError('triangle budget exceeded')
                    refs=[]
                    for (x,y,z),(u,v) in fragment:
                        key=(round(x,6),round(z,6))
                        if key not in samples:
                            lon,lat=vertex_geodetic(p,(x,y,z))
                            hit=sampler.query(lon,lat)
                            samples[key]=hit.altitude if hit else None
                        altitude=samples[key]
                        if altitude is None:raise ValueError(f'BTG gap for vertex {key}')
                        ix=len(vertices);vertices.append((x,altitude-anchor_msl+clearance,z))
                        refs.append((ix,u,v))
                    faces.append(Face(face.flag,face.mat,refs))
            ac.write(dest,vertices,faces)
            lines=p.stg.read_text(encoding='utf8').splitlines()
            fields=lines[p.line_index].split();fields[1]=dest.name
            lines[p.line_index]=' '.join(fields)
            p.stg.write_text('\n'.join(lines)+'\n',encoding='utf8')
            report['projected']+=1;report['faces_written']+=len(faces)
        except (ValueError,OSError,OverflowError) as exc:
            # Do not display a giant undraped plane on failures.
            dest.unlink(missing_ok=True)
            lines=p.stg.read_text(encoding='utf8').splitlines()
            lines[p.line_index]='# XPFG_DRAPE_FAILED '+lines[p.line_index]
            p.stg.write_text('\n'.join(lines)+'\n',encoding='utf8')
            report['disabled']+=1
            report['failures'].append({'model':p.model,'stg':str(p.stg),'reason':str(exc)})
    return report


def main():
    ap=argparse.ArgumentParser(description='Project explicitly marked OBJ8 draped triangles in newly built FlightGear scenery')
    ap.add_argument('--scenery',required=True,type=Path)
    ap.add_argument('--max-edge',type=float,default=25.)
    ap.add_argument('--max-depth',type=int,default=9)
    ap.add_argument('--clearance',type=float,default=.07)
    args=ap.parse_args()
    if args.max_edge<=0 or args.max_depth<0:ap.error('invalid subdivision parameters')
    root=args.scenery.expanduser().resolve()
    report=build(root,args.max_edge,args.max_depth,args.clearance)
    path=root/'draped-build-report.json'
    path.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf8')
    print(json.dumps({k:v if k!='failures' else len(v) for k,v in report.items()},indent=2),flush=True)
    if report['disabled']:raise SystemExit('Draped projection partially failed; see '+str(path))

if __name__=='__main__':main()
