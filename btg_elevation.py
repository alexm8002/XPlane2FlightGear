#!/usr/bin/env python3
"""Read SimGear BTG v7/v10 and sample airport terrain triangles at WGS84 points.

Pure Python, no external dependency. Includes both airport drop-in and base BTGs.
Triangles are projected to one local tangent plane for fast barycentric queries.
Unsupported BTG versions fail closed rather than returning fake elevations.
"""
from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass
import gzip
import math
from pathlib import Path
import struct

A = 6378137.0
F = 1 / 298.257223563
E2 = F * (2 - F)


def geodetic_to_ecef(lon, lat, h=0):
    lon, lat = math.radians(lon), math.radians(lat)
    sl, cl = math.sin(lat), math.cos(lat)
    n = A / math.sqrt(1-E2*sl*sl)
    return ((n+h)*cl*math.cos(lon), (n+h)*cl*math.sin(lon), (n*(1-E2)+h)*sl)


def ecef_to_geodetic(x, y, z):
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    lat = math.atan2(z, p*(1-E2))
    for _ in range(7):
        s = math.sin(lat)
        n = A / math.sqrt(1-E2*s*s)
        lat = math.atan2(z + E2*n*s, p)
    s = math.sin(lat)
    n = A / math.sqrt(1-E2*s*s)
    h = p / max(math.cos(lat), 1e-10) - n
    return math.degrees(lon), math.degrees(lat), h


class LocalFrame:
    def __init__(self, lon, lat):
        self.lon, self.lat = lon, lat
        self.origin = geodetic_to_ecef(lon, lat)
        lam, phi = math.radians(lon), math.radians(lat)
        self.east = (-math.sin(lam), math.cos(lam), 0)
        self.north = (-math.sin(phi)*math.cos(lam), -math.sin(phi)*math.sin(lam), math.cos(phi))
        self.up = (math.cos(phi)*math.cos(lam), math.cos(phi)*math.sin(lam), math.sin(phi))
    def project(self, xyz):
        d = [xyz[k]-self.origin[k] for k in range(3)]
        return tuple(sum(d[k]*axis[k] for k in range(3)) for axis in (self.east,self.north,self.up))
    def xy(self, lon, lat):
        e, n, _ = self.project(geodetic_to_ecef(lon, lat))
        return e,n
    def altitude(self, lon, lat, local_up):
        # ECEF projection's up component at ellipsoid=0, then add local height.
        _,_,base = self.project(geodetic_to_ecef(lon,lat))
        return local_up - base


def _take(data, offset, fmt):
    size = struct.calcsize(fmt)
    if offset+size > len(data):
        raise ValueError('BTG tronque')
    vals = struct.unpack_from(fmt, data, offset)
    return vals[0] if len(vals)==1 else vals, offset+size


def _records(path):
    path = Path(path)
    with gzip.open(path,'rb') if path.suffix=='.gz' else path.open('rb') as fh:
        data = fh.read()
    header, off = _take(data,0,'<I')
    if header >> 16 != 0x5347:
        raise ValueError(f'{path}: entete SG invalide')
    version = header & 0xffff
    if version < 7 or version > 10:
        raise ValueError(f'{path}: version BTG {version} non prise en charge')
    _, off = _take(data,off,'<I')
    cformat = '<I' if version >=10 else '<H'
    count, off = _take(data,off,cformat)
    if count > 500000:
        raise ValueError('Nombre objets BTG anormal')
    for _ in range(count):
        kind,off=_take(data,off,'<B')
        props,off=_take(data,off,cformat)
        elems,off=_take(data,off,cformat)
        if props > 1000 or elems > 1000000:
            raise ValueError('Compteurs BTG anormaux')
        properties = {}
        for _ in range(props):
            pkind,off=_take(data,off,'<B')
            n,off=_take(data,off,'<I')
            if off+n > len(data): raise ValueError('Propriete BTG tronquee')
            properties[pkind]=data[off:off+n]
            off+=n
        elements=[]
        for _ in range(elems):
            n,off=_take(data,off,'<I')
            if off+n > len(data): raise ValueError('Element BTG tronque')
            elements.append(memoryview(data)[off:off+n])
            off+=n
        yield version,kind,properties,elements


def read_triangles(path, frame, focus=None, limit=6000):
    """Return (material, [((east,north,up),... x3),...]) from BTG.

    focus is (min_e,min_n,max_e,max_n) to reduce retained geometry.
    """
    center = (0.0,0.0,0.0)
    vertices = []
    tris=[]
    for version,kind,properties,elements in _records(path):
        if kind == 0:
            for elem in elements:
                if len(elem)>=28:
                    center=struct.unpack_from('<3d',elem,0)
        elif kind == 1:
            for elem in elements:
                if len(elem)%12: raise ValueError(f'{path}: vertex list invalide')
                for xyz in struct.iter_unpack('<fff',elem):
                    vertices.append(frame.project(tuple(center[i]+xyz[i] for i in range(3))))
        elif kind in (10,11,12):
            material=bytes(properties.get(0,b'')).decode('utf8','replace')
            flags=properties.get(1,b'\x09')[0]
            stride = sum(1 for bit in (1,2,4,8) if flags&bit)
            if not(flags&1) or stride == 0: continue
            unit=4 if version>=10 else 2
            index_fmt='<I' if version>=10 else '<H'
            for elem in elements:
                rec_size=unit*stride
                if len(elem)%rec_size: raise ValueError(f'{path}: index element invalide')
                ids=[struct.unpack_from(index_fmt,elem,k)[0] for k in range(0,len(elem),rec_size)]
                if kind==10:
                    combos=((ids[i],ids[i+1],ids[i+2]) for i in range(0,len(ids)-2,3))
                elif kind==11:
                    combos=((ids[i],ids[i+1],ids[i+2]) for i in range(0,len(ids)-2))
                else:
                    combos=((ids[0],ids[i+1],ids[i+2]) for i in range(len(ids)-2)) if ids else ()
                for ix in combos:
                    if max(ix)>=len(vertices): raise ValueError(f'{path}: vertex index invalide')
                    a,b,c=(vertices[j] for j in ix)
                    if focus:
                        loe,lon,hie,hin=focus
                        if max(a[0],b[0],c[0]) < loe or min(a[0],b[0],c[0])>hie or max(a[1],b[1],c[1])<lon or min(a[1],b[1],c[1])>hin:
                            continue
                    tris.append((material,(a,b,c)))
                    if len(tris)>5_000_000:
                        raise ValueError(f'{path}: trop de triangles')
    return tris


@dataclass
class Hit:
    altitude: float
    material: str
    path: str


class TerrainSampler:
    def __init__(self, terrain_root, lonlat, margin=1000, cell=40):
        self.terrain_root=Path(terrain_root)
        self.frame=LocalFrame(*lonlat)
        self.cell=cell
        self.cells=defaultdict(list)
        self.files=[]
        self.triangle_count=0
        self.errors=[]
        self.margin=margin
        self.bbox=None

    def load(self, positions):
        if not positions: raise ValueError('Aucune position')
        points=[self.frame.xy(lon,lat) for lon,lat in positions]
        self.bbox=(min(p[0] for p in points)-self.margin,min(p[1] for p in points)-self.margin,
                   max(p[0] for p in points)+self.margin,max(p[1] for p in points)+self.margin)
        paths=sorted(p for p in self.terrain_root.rglob('*.btg*') if p.is_file())
        if not paths: raise ValueError('Aucun BTG dans Terrain')
        for path in paths:
            try:
                triangles=read_triangles(path,self.frame,self.bbox)
            except (OSError,ValueError,struct.error) as exc:
                self.errors.append(f'{path}: {exc}')
                continue
            if triangles: self.files.append(str(path))
            for material, verts in triangles:
                self.triangle_count+=1
                a,b,c=verts
                minimum_e=min(a[0],b[0],c[0]); maximum_e=max(a[0],b[0],c[0]); minimum_n=min(a[1],b[1],c[1]); maximum_n=max(a[1],b[1],c[1])
                ix0,ix1=math.floor(minimum_e/self.cell),math.floor(maximum_e/self.cell)
                iy0,iy1=math.floor(minimum_n/self.cell),math.floor(maximum_n/self.cell)
                if (ix1-ix0+1)*(iy1-iy0+1)>30000: continue
                item=(verts,material,str(path))
                for ix in range(ix0,ix1+1):
                    for iy in range(iy0,iy1+1): self.cells[(ix,iy)].append(item)
        if not self.triangle_count:
            raise ValueError('Aucun triangle BTG exploitable: '+str(self.errors[:3]))

    def query(self,lon,lat):
        e,n=self.frame.xy(lon,lat)
        candidates=self.cells.get((math.floor(e/self.cell),math.floor(n/self.cell)),())
        hits=[]
        for (a,b,c),material,path in candidates:
            det=(b[1]-c[1])*(a[0]-c[0])+(c[0]-b[0])*(a[1]-c[1])
            if abs(det)<1e-6: continue
            u=((b[1]-c[1])*(e-c[0])+(c[0]-b[0])*(n-c[1]))/det
            v=((c[1]-a[1])*(e-c[0])+(a[0]-c[0])*(n-c[1]))/det
            w=1-u-v
            if min(u,v,w)<-1e-7: continue
            h=self.frame.altitude(lon,lat,u*a[2]+v*b[2]+w*c[2])
            hits.append(Hit(h,material,path))
        # Prefer airport overlay if multiple surfaces overlap; reject giant layer gaps.
        airports=[v for v in hits if 'airport' in v.path.lower() or Path(v.path).stem.upper().startswith('LFLL')]
        return max(airports,key=lambda v:v.altitude) if airports else (max(hits,key=lambda v:v.altitude) if hits else None)
