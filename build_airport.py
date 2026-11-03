#!/usr/bin/env python3
"""Generic native TerraGear terrain builder driven by apt.dat geometry.

The v0.3.11 object engine remains unchanged. Works with one airport per build.
"""
import argparse
import gzip
import json
import math
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

from airport import find_apt, parse_airports
from terrain import bounds_from_airport, airport_coordinate_diagnostics
from terrain_assets import find_priorities, download, NE_URL

ROOT = Path(__file__).resolve().parent
SRTM_ROOT = 'https://s3.amazonaws.com/elevation-tiles-prod/skadi'


def execute(cmd):
    print('\n+ ' + ' '.join(map(str,cmd)),flush=True)
    subprocess.run([str(p) for p in cmd],check=True)


def tile_name(lat,lon):
    return f"{'N' if lat>=0 else 'S'}{abs(lat):02d}{'E' if lon>=0 else 'W'}{abs(lon):03d}"


def needed_tiles(bounds):
    west,east,south,north=bounds
    return [(lat,lon) for lat in range(south,north) for lon in range(west,east)]


def selection(source,icao=None,bounds=None,max_tiles=16,margin=0.05):
    apt=find_apt(source)
    if apt is None:
        raise ValueError('apt.dat absent: terrain aéroportuaire impossible. Utiliser une scène avec Earth nav data/apt.dat.')
    _,airports=parse_airports(apt)
    if icao:
        candidates=[a for a in airports if a['icao'].upper()==icao.upper()]
        if not candidates:raise ValueError(f'ICAO {icao} absent de apt.dat')
    else:
        candidates=airports
        if len(candidates)!=1:
            names=', '.join(a['icao'] for a in candidates[:20])
            raise ValueError(f'Plusieurs aéroports ({len(candidates)}): préciser --icao. Disponibles: {names}')
    chosen=candidates[0]
    bb=list(bounds) if bounds is not None else bounds_from_airport(chosen, margin=margin)
    if any(not isinstance(x,int) for x in bb) or len(bb)!=4:
        raise ValueError('Bornes entières requises: WEST EAST SOUTH NORTH')
    west,east,south,north=bb
    if not (-180<=west<east<=180 and -90<=south<north<=90):
        raise ValueError('Bornes invalides')
    tiles=needed_tiles(bb)
    if len(tiles)>max_tiles:
        raise ValueError(f'{len(tiles)} tuiles terrain demandées (maximum {max_tiles}) ; emprise W E S N = {bb}. Choisir des bornes manuelles proches de l aéroport si cette emprise est incorrecte.')
    return chosen,bb,tiles


def ensure_hgt(lat,lon,data,override):
    name=tile_name(lat,lon)
    if override:
        candidates=[p for p in override if p.name.lower().startswith(name.lower()+'.') or p.name.lower()==name.lower()+'.hgt']
        if candidates:
            hgt=candidates[0]
            if hgt.suffix.lower()=='.gz':
                output=data/(name+'.hgt')
                if not output.exists():
                    with gzip.open(hgt,'rb') as src,output.open('wb') as dst:shutil.copyfileobj(src,dst)
                hgt=output
            if hgt.stat().st_size not in (1201*1201*2,3601*3601*2):
                raise ValueError(f'Taille HGT invalide: {hgt}')
            return hgt
        # Single tile explicit HGT accepted even if arbitrary filename
        if len(override)==1 and override[0].suffix.lower()=='.hgt':
            hgt=override[0]
            if hgt.stat().st_size not in (1201*1201*2,3601*3601*2):raise ValueError(f'Taille HGT invalide: {hgt}')
            return hgt
        raise ValueError(f'HGT {name} absent des fichiers fournis')
    hgt=data/(name+'.hgt')
    if not hgt.exists():
        url=f'{SRTM_ROOT}/{name[:3]}/{name}.hgt.gz'
        archive=data/(name+'.hgt.gz')
        download(url,archive)
        with gzip.open(archive,'rb') as src,hgt.open('wb') as dst:shutil.copyfileobj(src,dst)
    if hgt.stat().st_size not in (1201*1201*2,3601*3601*2):
        raise ValueError(f'Taille HGT invalide: {hgt}')
    return hgt


def main():
    ap=argparse.ArgumentParser(description='TerraGear: rebuild one airport anywhere in the world from X-Plane apt.dat')
    ap.add_argument('--source',required=True,type=Path)
    ap.add_argument('--output',required=True,type=Path)
    ap.add_argument('--icao',help='Required if apt.dat describes multiple airports')
    ap.add_argument('--bounds',nargs=4,type=int,metavar=('W','E','S','N'),help='Bornes manuelles entières. Prioritaires sur la détection automatique.')
    ap.add_argument('--margin',type=float,default=0.05,help='Marge en degrés autour des coordonnées apt.dat (0 à 1, défaut 0.05).')
    ap.add_argument('--terragear-env',required=True,type=Path)
    ap.add_argument('--priorities',type=Path)
    ap.add_argument('--hgt',action='append',type=Path,default=[])
    ap.add_argument('--max-tiles',type=int,default=16)
    ap.add_argument('--threads',type=int,default=2)
    a=ap.parse_args()
    source=a.source.expanduser().resolve();output=a.output.expanduser().resolve();wrapper=a.terragear_env.expanduser().resolve()
    if not wrapper.is_file():ap.error('terragear-env introuvable')
    ogr=shutil.which('ogr2ogr')
    if not ogr:ap.error('ogr2ogr absent (installer GDAL)')
    try:airport,bb,tiles=selection(source,a.icao,a.bounds,a.max_tiles,a.margin)
    except ValueError as exc:ap.error(str(exc))
    print(f"Airport: {airport['icao']} {airport['name']} | Bounds {bb} | SRTM tiles {len(tiles)}",flush=True)
    if not a.bounds:
        print('Auto bounds diagnostic: '+json.dumps(airport_coordinate_diagnostics(airport, a.margin), ensure_ascii=False),flush=True)
    priorities=a.priorities.expanduser().resolve() if a.priorities else find_priorities(wrapper,ROOT)
    if not priorities.is_file():ap.error('default_priorities.txt absent')
    build=output/'AirportBuild'; data=build/'downloads';work=build/'work'
    data.mkdir(parents=True,exist_ok=True);work.mkdir(parents=True,exist_ok=True)
    if len(tiles)>1 and len(a.hgt)==1 and not a.hgt[0].name[:1].upper() in ('N','S'):
        ap.error('Pour plusieurs tuiles, nommer chaque HGT NxxEyyy.hgt ou laisser le téléchargement automatique')
    hgt_paths=[]
    for lat,lon in tiles:hgt_paths.append(ensure_hgt(lat,lon,data,[p.expanduser().resolve() for p in a.hgt]))
    resolutions={1 if p.stat().st_size==3601*3601*2 else 3 for p in hgt_paths}
    if len(resolutions)!=1:ap.error('Mélange de résolutions HGT 1/3 arcsecond non pris en charge')
    ne_zip=data/'ne_10m_land.zip';download(NE_URL,ne_zip)
    ne_dir=data/'ne_10m_land';ne_dir.mkdir(exist_ok=True)
    with zipfile.ZipFile(ne_zip) as z:
        if not (ne_dir/'ne_10m_land.shp').is_file():z.extractall(ne_dir)
    geojson=data/'landmass.geojson'
    geojson.unlink(missing_ok=True)
    execute([ogr,'-f','GeoJSON','-clipsrc',str(bb[0]),str(bb[2]),str(bb[1]),str(bb[3]),geojson,ne_dir/'ne_10m_land.shp'])
    landmass=work/'Landmass'
    if landmass.exists():shutil.rmtree(landmass)
    landmass.mkdir(parents=True)
    execute([wrapper,'ogr-decode','--area-type','Default','--max-segment','500',landmass,geojson])
    if not any(landmass.rglob('*')):
        raise RuntimeError('Aucune terre dans Natural Earth pour cette emprise; génération interrompue (zones océaniques non encore prises en charge)')
    terrain=output/'Terrain'
    if terrain.exists():shutil.rmtree(terrain)
    terrain.mkdir(parents=True)
    cmd=[sys.executable, ROOT/'xp2fg.py',source,output,'--airport-mode','only','--build-terrain',
         '--icao',airport['icao'],'--terragear-env',wrapper,'--bounds',*map(str,bb),
         '--hgt-resolution',str(next(iter(resolutions))),'--elevation-layer','SRTMGL1',
         '--landcover-layer','Landmass','--priorities',priorities,'--threads',str(a.threads)]
    for hgt in hgt_paths:cmd+=['--hgt',hgt]
    execute(cmd)
    found=list(terrain.rglob('*.btg'))+list(terrain.rglob('*.btg.gz'))
    if not found:raise RuntimeError('Aucun BTG produit')
    report={'version':'0.3.13.3','icao':airport['icao'],'airport':airport['name'],'bounds':bb,'bounds_mode':'manual' if a.bounds else 'automatic','margin_degrees':a.margin,
            'srtm_tiles':[p.name for p in hgt_paths],'terrain_btg':len(found)}
    (build/'generic-build-report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf8')
    print('Terrain generated:',json.dumps(report,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
