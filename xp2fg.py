#!/usr/bin/env python3
"""X-Plane DSFTool text + static OBJ8 -> FlightGear AC3D/STG (early MVP)."""
import argparse
import hashlib
import json
import math
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from airport import prepare_airport, find_apt, parse_airports
from terrain import build_terrain
from axis_transform import xp_to_fg
from night_lights import parse_obj_lights, write_lights_ac, write_lights_xml, write_lit_xml


def bucket(lon, lat):
    ay = abs(lat)
    span = next((s for cutoff, s in [(89, 12), (86, 4), (83, 2), (76, 1), (62, .5), (22, .25), (0, .125)] if ay >= cutoff), .125)
    by = math.floor(lat)
    lon_bucket = math.floor(lon / span) * span
    bx = math.floor(lon_bucket)
    y = max(0, min(7, int((lat - by) * 8)))
    x = int(round((lon_bucket - bx) * 8))
    idx = ((bx + 180) << 14) + ((by + 90) << 6) + (y << 3) + x
    def folder(lon_int, lat_int):
        return ('e' if lon_int >= 0 else 'w') + f'{abs(lon_int):03d}' + ('n' if lat_int >= 0 else 's') + f'{abs(lat_int):02d}'
    return folder(math.floor(bx / 10) * 10, math.floor(by / 10) * 10), folder(bx, by), idx


def parse_obj_layers(path):
    """Respect OBJ8 TRIS render state: separate rigid and ATTR_draped faces."""
    vt, indices = [], []
    faces = {'rigid': [], 'draped': []}
    textures = {'rigid': None, 'draped': None}
    draped = False
    for raw in path.read_text(encoding='utf-8-sig', errors='replace').splitlines():
        p = raw.strip().split()
        if not p: continue
        key = p[0]
        if key == 'TEXTURE' and len(p)>1: textures['rigid'] = p[1]
        elif key == 'TEXTURE_DRAPED' and len(p)>1: textures['draped'] = p[1]
        elif key == 'ATTR_draped': draped = True
        elif key == 'ATTR_no_draped': draped = False
        elif key == 'VT' and len(p)>=9: vt.append(tuple(map(float, p[1:9])))
        elif key == 'IDX' and len(p)>=2: indices.append(int(p[1]))
        elif key == 'IDX10' and len(p)>=11: indices.extend(map(int, p[1:11]))
        elif key == 'TRIS' and len(p)>=3:
            start,count=map(int,p[1:3])
            selected=indices[start:start+count]
            if len(selected)!=count or count%3: raise ValueError('TRIS/IDX invalides')
            for j in range(0,count,3):
                tri=selected[j:j+3]
                if min(tri)<0 or max(tri)>=len(vt): raise ValueError('Index de sommet hors limites')
                faces['draped' if draped else 'rigid'].append(tri)
    if not vt or not any(faces.values()): raise ValueError('OBJ8 sans triangles statiques (VT/TRIS)')
    return vt,faces,textures


def convert_obj(src, out, texture_out, layer='rigid', parsed=None):
    vt, layers, textures = parsed if parsed is not None else parse_obj_layers(src)
    faces=layers[layer]
    if not faces: raise ValueError('Pas de triangles dans la couche '+layer)
    texture=textures[layer]
    if layer=='draped' and not texture:
        raise ValueError('ATTR_draped sans TEXTURE_DRAPED: refuse de creer une dalle blanche')
    texname = ''
    if texture:
        candidate = (src.parent / texture).resolve()
        if not candidate.is_file():
            raise FileNotFoundError(f'Texture introuvable: {candidate}')
        texname = hashlib.sha1(str(candidate).encode()).hexdigest()[:10] + '_' + candidate.name
        if candidate.suffix.lower() == '.dds':
            try:
                from PIL import Image
                pngname = Path(texname).with_suffix('.png').name
                with Image.open(candidate) as img: img.save(texture_out / pngname, 'PNG')
                texname = pngname
            except ImportError as exc:
                raise ValueError('DDS texture requires Pillow: python3 -m pip install Pillow') from exc
        else:
            shutil.copy2(candidate, texture_out / texname)
    lines = ['AC3Db', 'MATERIAL "default" rgb 1 1 1 amb 0.2 0.2 0.2 emis 0 0 0 spec 0 0 0 shi 0 trans 0', 'OBJECT world', 'kids 1', 'OBJECT poly', 'name "converted_'+layer+'"']
    if texname: lines.append(f'texture "{texname}"')
    lines.extend([f'numvert {len(vt)}'])
    # v0.3.6 axes retained: +X South, +Z West
    for v in vt:
        x, y, z = xp_to_fg(v[0], v[1], v[2])
        lines.append(f'{x:.7f} {y:.7f} {z:.7f}')
    lines.append(f'numsurf {len(faces)}')
    for tri in faces:
        lines.extend(['SURF 0x30', 'mat 0', 'refs 3'])
        for ix in tri:
            v=vt[ix];lines.append(f'{ix} {v[6]:.7f} {v[7]:.7f}')
    lines.append('kids 0')
    out.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return len(faces)


def dsf_to_text(dsf, tool):
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / 'scene.txt'
        subprocess.run([tool, '--dsf2text', str(dsf), str(out)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return out.read_text(encoding='utf-8-sig', errors='replace')


def resolve_asset(base, name, libraries):
    """DSF names are virtual names mapped using library.txt EXPORT rules.

    Fallback to a package-local object only when it really exists.  Never map
    lib/... to a guessed physical path: the virtual and physical names differ.
    """
    rel = name.replace('\\', '/').lstrip('/')
    candidates = libraries.get(rel, ())
    for path in candidates:
        if path.is_file() and path.suffix.lower() == '.obj':
            return path.resolve()
    # Package-local DSF references are allowed, even without library.txt.
    local = base / rel
    if local.is_file() and local.suffix.lower() == '.obj':
        return local.resolve()
    return None


def library_index(packages):
    """Index virtual keys from library.txt, never use the key as disk path.

    Supports EXPORT, EXPORT_EXTEND, EXPORT_BACKUP, EXPORT_RATIO and
    EXPORT_EXCLUDE (the latter blocks earlier definitions for this key).
    Returns ordered candidate physical paths.  The first physically present
    path is selected; alternate paths remain available as fallbacks.
    """
    ret = defaultdict(list)
    package_stats = []
    for base in packages:
        file = base / 'library.txt'
        if not file.is_file(): continue
        count = 0
        for line in file.read_text(encoding='utf-8-sig', errors='replace').splitlines():
            p = line.split('#', 1)[0].split()
            if not p: continue
            cmd = p[0].upper()
            if cmd == 'EXPORT_EXCLUDE' and len(p) >= 2:
                # Keep later packages from accidentally reintroducing excluded
                # alternatives? Explicit exclusion clears earlier candidates.
                ret.pop(p[1], None)
                continue
            if cmd == 'EXPORT_RATIO':
                if len(p) < 4: continue
                virtual, physical = p[2], p[3]
            elif cmd in ('EXPORT', 'EXPORT_EXTEND', 'EXPORT_BACKUP'):
                if len(p) < 3: continue
                virtual, physical = p[1], p[2]
            else:
                continue
            if not physical.lower().endswith('.obj'):
                continue
            # The physical target must be inside the library package.
            candidate = (base / physical).resolve()
            if not candidate.is_relative_to(base.resolve()):
                continue
            if candidate not in ret[virtual]:
                ret[virtual].append(candidate)
            count += 1
        package_stats.append({'package':str(base),'obj_exports':count})
    return ret, package_stats


def library_resolution_report(unresolved, libraries, package_stats):
    result = {}
    for name, n in sorted(unresolved.items(), key=lambda kv: -kv[1]):
        candidates = libraries.get(name, ())
        if not candidates:
            reason = 'no_export_in_loaded_libraries'
        elif not any(x.is_file() for x in candidates):
            reason = 'export_found_but_target_file_missing'
        else:
            reason = 'export_exists_but_asset_not_supported_or_inaccessible'
        result[name] = {
            'placements': n, 'reason':reason,
            'physical_candidates': [str(x) for x in candidates[:6]],
        }
    return {'packages_indexed':package_stats, 'virtual_obj_keys':len(libraries),
            'unresolved':result}

def run(args):
    source, target = args.source.resolve(), args.output.resolve()
    if not source.is_dir(): raise ValueError('Dossier de scenery source introuvable')
    dsfs = sorted((source / 'Earth nav data').rglob('*.dsf')) if (source / 'Earth nav data').exists() else []
    if not dsfs and args.airport_mode == 'off': raise ValueError('Aucun .dsf dans Earth nav data')
    libs, library_stats = library_index([source] + [p.resolve() for p in args.library])
    target.mkdir(parents=True, exist_ok=True)
    entries = defaultdict(list)
    airport = prepare_airport(source, target, args.apt_dat, args.icao, None) if args.airport_mode != 'off' else {'status': 'disabled'}
    if args.build_terrain:
        aptpath = find_apt(source, args.apt_dat)
        if aptpath is None: raise ValueError('apt.dat missing')
        _, blocks = parse_airports(aptpath)
        if args.icao: blocks = [b for b in blocks if b['icao'].upper() == args.icao.upper()]
        terrain_info = build_terrain(args, target, airport, blocks)
        airport['terrain'] = {k: v for k, v in terrain_info.items() if k != 'commands'}
    if args.airport_mode == 'only' or (not dsfs and args.airport_mode != 'off'):
        (target / 'report.json').write_text(json.dumps({'airport': airport, 'dsf_count': 0}, indent=2, ensure_ascii=False))
        print(json.dumps(airport, indent=2, ensure_ascii=False))
        return
    log = {'airport': airport, 'dsf_count': len(dsfs), 'placed': 0, 'skipped': [], 'models': 0, 'triangles': 0, 'elevation_mode': args.object_placement, 'axis_transform': 'xp_eus_to_fg_suw_v036', 'version': '0.3.15.0', 'night_lights': {'lights_emitted': 0, 'assets_with_lights': 0, 'texture_lit_models': 0, 'unknown_names': {}, 'source_commands': {}}}
    model_cache = {}
    unresolved_refs = defaultdict(int)
    for dsf in dsfs:
        content = dsf_to_text(dsf, args.dsftool)
        defs = []
        for line in content.splitlines():
            p = line.strip().split()
            if not p: continue
            if p[0] == 'OBJECT_DEF' and len(p) >= 2:
                defs.append(p[1])
            elif p[0] in ('OBJECT', 'OBJECT_AGL', 'OBJECT_MSL') and len(p) >= 5:
                try:
                    index = int(p[1]); lon = float(p[2]); lat = float(p[3]); heading = float(p[4])
                    if not -180 <= lon <= 180 or not -90 <= lat <= 90: raise ValueError('coords')
                    name = defs[index]
                    asset = resolve_asset(source, name, libs)
                    if asset is None:
                        unresolved_refs[name] += 1
                        raise FileNotFoundError(f'OBJ non resolu: {name}')
                    grand, small, idx = bucket(lon, lat)
                    tile_dir = target / 'Objects' / grand / small
                    tile_dir.mkdir(parents=True, exist_ok=True)
                    signature = hashlib.sha1(str(asset).encode()).hexdigest()[:12]
                    if signature not in model_cache:
                        try:
                            geometry = parse_obj_layers(asset)
                        except ValueError as exc:
                            if 'sans triangles statiques' not in str(exc):
                                raise
                            geometry = ([], {'rigid': [], 'draped': []}, {'rigid': None, 'draped': None})
                        light_data = parse_obj_lights(asset)
                        model_cache[signature] = (geometry, light_data)
                        lights, lit, commands, unknown = light_data
                        stats = log['night_lights']
                        for k, n in commands.items():
                            stats['source_commands'][k] = stats['source_commands'].get(k, 0) + n
                        for k, n in unknown.items():
                            stats['unknown_names'][k] = stats['unknown_names'].get(k, 0) + n
                    parsed, (lights, lit, commands, unknown) = model_cache[signature]
                    _, layers, textures = parsed
                    if p[0]=='OBJECT_MSL':
                        keyword, altitude='OBJECT_STATIC',float(p[5])
                    elif args.object_placement=='agl':
                        keyword='OBJECT_STATIC_AGL'
                        altitude=float(p[5]) if p[0]=='OBJECT_AGL' else 0.0
                    else:
                        keyword='OBJECT_STATIC';altitude=args.elevation_m
                    for layer in ('rigid','draped'):
                        if not layers[layer]: continue
                        outname = signature + ('_draped' if layer=='draped' else '') + '.ac'
                        outpath = tile_dir/outname
                        if not outpath.exists():
                            count=convert_obj(asset,outpath,tile_dir,layer,parsed)
                            log['models']+=1;log['triangles']+=count
                        placedname=outname
                        if layer=='rigid' and lit:
                            # FlightGear material animation toggles day/night texture.
                            from PIL import Image
                            candidate=(asset.parent/lit).resolve()
                            if not candidate.is_file():
                                log.setdefault('night_warnings',[]).append('Missing TEXTURE_LIT: '+str(candidate))
                            elif textures['rigid']:
                                digest=hashlib.sha1(str(candidate).encode()).hexdigest()[:10]
                                litname=digest+'_'+candidate.stem+'.png'
                                if not (tile_dir/litname).exists():
                                    with Image.open(candidate) as image:
                                        image.save(tile_dir/litname,'PNG')
                                dayprefix=hashlib.sha1(str((asset.parent/textures['rigid']).resolve()).encode()).hexdigest()[:10]
                                dayname=dayprefix+'_'+Path(textures['rigid']).name
                                if Path(dayname).suffix.lower()=='.dds':dayname=str(Path(dayname).with_suffix('.png'))
                                xmlpath=tile_dir/(signature+'_lit.xml')
                                write_lit_xml(xmlpath,outname,dayname,litname)
                                placedname=xmlpath.name
                                log['night_lights']['texture_lit_models']+=1
                        entries[tile_dir/f'{idx}.stg'].append(f'{keyword} {placedname} {lon:.8f} {lat:.8f} {altitude:.2f} {(-heading)%360:.3f}')
                        if layer=='draped':
                            log.setdefault('draped_placements',[]).append({'model':outname,'source':str(asset),'texture':textures[layer], 'stg':str(tile_dir/f'{idx}.stg')})
                        log['placed']+=1
                    if lights:
                        nlight=tile_dir/(signature+'_lights.ac')
                        emitted=write_lights_ac(nlight,lights)
                        if emitted:
                            wrapper=tile_dir/(signature+'_lights.xml')
                            write_lights_xml(wrapper,nlight.name)
                            entries[tile_dir/f'{idx}.stg'].append(f'{keyword} {wrapper.name} {lon:.8f} {lat:.8f} {altitude:.2f} {(-heading)%360:.3f}')
                            log['night_lights']['lights_emitted']+=emitted
                            log['night_lights']['assets_with_lights']+=1
                            log['placed']+=1
                except (ValueError, IndexError, FileNotFoundError, OSError) as exc:
                    log['skipped'].append({'dsf': str(dsf), 'line': line, 'reason': str(exc)})
    log['unresolved_asset_refs'] = dict(sorted(unresolved_refs.items(), key=lambda kv: -kv[1]))
    diagnostic = library_resolution_report(unresolved_refs, libs, library_stats)
    (target/'library-resolution-report.json').write_text(json.dumps(diagnostic, indent=2, ensure_ascii=False), encoding='utf-8')
    print('LIBRARY_INDEX: packages=%d exported_OBJ_paths=%d' % (len(library_stats), len(libs)))
    target_key = 'lib/airport/lights/slow/taxi_edge.obj'
    target_candidates = libs.get(target_key, [])
    print('TAXI_EDGE_EXPORT: %s' % (
        ', '.join(str(p) + (' [OK]' if p.is_file() else ' [MISSING FILE]') for p in target_candidates)
        if target_candidates else 'NOT FOUND IN INDEX'), flush=True)
    diagnostic['checked_refs'] = {
        target_key: {
            'export_found': bool(target_candidates),
            'candidates': [str(p) for p in target_candidates],
            'target_files_found': sum(p.is_file() for p in target_candidates),
            'unresolved_placements': unresolved_refs.get(target_key, 0),
        }
    }
    (target/'library-resolution-report.json').write_text(json.dumps(diagnostic, indent=2, ensure_ascii=False), encoding='utf-8')
    if unresolved_refs:
        print('UNRESOLVED_LIBRARY_REFS: see library-resolution-report.json')
    for path, lines in entries.items(): path.write_text('# xp2fg generated; AGL or MSL per entry\n' + '\n'.join(lines) + '\n')
    (target / 'report.json').write_text(json.dumps(log, indent=2, ensure_ascii=False))
    print(json.dumps({k: v if k != 'skipped' else len(v) for k, v in log.items()}, indent=2))
    print('NOTE: nightly emissive light sprites are approximate; original X-Plane spill and animated effects are not fully supported.')


def main():
    ap = argparse.ArgumentParser(description='X-Plane overlay DSF/OBJ8 to FlightGear STG/AC3D (proof of concept)')
    ap.add_argument('source', type=Path, help='X-Plane scenery package')
    ap.add_argument('output', type=Path, help='Destination FlightGear scenery folder')
    ap.add_argument('--airport-mode', choices=['auto', 'only', 'off'], default='auto', help='Prepare airport apt.dat in addition to 3D objects')
    ap.add_argument('--apt-dat', type=Path, help='Explicit apt.dat file; default source/Earth nav data/apt.dat')
    ap.add_argument('--icao', help='Extract only this airport')
    ap.add_argument('--genapts', nargs='?', const='genapts', help='Run TerraGear genapts for selected airport; needs installed TerraGear')
    ap.add_argument('--dsftool', default='DSFTool', help='Path to X-Plane DSFTool executable')
    ap.add_argument('--library', action='append', type=Path, default=[], help='Additional X-Plane library scenery package (repeatable)')
    ap.add_argument('--elevation-m', default=None, type=float, help='FIXED absolute altitude AMSL (test only)')
    ap.add_argument('--object-placement', choices=['agl', 'msl'], default='agl', help='Place X-Plane OBJECT at ground (default) or absolute altitude')
    ap.add_argument('--build-terrain', action='store_true', help='Build actual Terrain/*.btg.gz via native TerraGear')
    ap.add_argument('--terragear-env', type=Path, help='Path to macOS TerraGear wrapper (terragear-env)')
    ap.add_argument('--terrain-work', type=Path, help='Reuse prepared TerraGear work tree')
    ap.add_argument('--hgt', type=Path, action='append', default=[], help='SRTM HGT elevation file; repeat as needed')
    ap.add_argument('--hgt-resolution', type=int, choices=[1, 3], default=1)
    ap.add_argument('--elevation-layer', default='SRTMGL1', help='TerraGear elevation layer name')
    ap.add_argument('--landcover-layer', action='append', default=[], help='Prepared TerraGear land-cover layer; repeatable')
    ap.add_argument('--bounds', nargs=4, type=int, metavar=('MIN_LON','MAX_LON','MIN_LAT','MAX_LAT'))
    ap.add_argument('--priorities', type=Path, help='TerraGear default_priorities.txt')
    ap.add_argument('--threads', type=int, default=2, help='genapts thread count (recommend <= 4)')
    ap.add_argument('--dry-run', action='store_true', help='Print commands in terrain-report.json without executing')
    args = ap.parse_args()
    if args.build_terrain and args.airport_mode == 'off': ap.error('--build-terrain requires airport-mode auto/only')
    if args.airport_mode != 'only' and args.object_placement == 'msl' and args.elevation_m is None:
        ap.error('--elevation-m is required when converting objects (fixed AMSL placeholder)')
    try: run(args)
    except (ValueError, RuntimeError, subprocess.CalledProcessError) as exc: ap.error(str(exc))

if __name__ == '__main__': main()
