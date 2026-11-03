"""Native TerraGear orchestration (does not attempt to convert X-Plane mesh)."""
from pathlib import Path
import json
import math
import shutil
import subprocess


def _geodistance_km(a, b):
    """Approximate great-circle distance; only used to reject distant apt.dat outliers."""
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat, dlon = lat2-lat1, lon2-lon1
    h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 12742.0 * math.asin(min(1.0, math.sqrt(max(0.0, h))))


def airport_coordinate_diagnostics(airport, margin=0.05, radius_km=12.0):
    """Prefer explicit WED 130 Airport Boundary polygons, then airport geometry.

    apt.dat coordinates are lat/lon, but tiled bounds use lon/lat. Do not treat
    nodes of unrelated 120 linear features as airport area boundaries.
    """
    if not math.isfinite(margin) or not 0.0 <= margin <= 1.0:
        raise ValueError('La marge doit etre comprise entre 0 et 1 degre')

    def point(lat, lon):
        try:
            lat, lon = float(lat), float(lon)
            if math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
                return (lat, lon)
        except (TypeError, ValueError):
            pass
        return None

    runways, surfaces, boundaries = [], [], []
    active = None
    for line in airport['records']:
        p = line.split()
        if not p:
            continue
        kind = p[0]
        if kind in ('110', '120', '130'):
            active = kind
            continue
        if kind == '100' and len(p) >= 20:
            # 100 width ... runway_id lat lon ... runway_id lat lon
            pair = (point(p[9], p[10]), point(p[18], p[19]))
            if all(pair):
                runways.append(pair)
            active = None
        elif kind == '101' and len(p) >= 9:
            pair = (point(p[4], p[5]), point(p[7], p[8]))
            if all(pair):
                runways.append(pair)
            active = None
        elif kind == '102' and len(p) >= 4:
            v = point(p[2], p[3])
            if v: surfaces.append(v)
            active = None
        elif kind in ('111', '112', '113', '114', '115', '116') and len(p) >= 3:
            v = point(p[1], p[2])
            if v:
                if active == '130': boundaries.append(v)
                elif active == '110': surfaces.append(v)
            if kind in ('113', '114', '115', '116'):
                active = None
        elif kind not in ('1302',):
            active = None

    valid_runways = [pair for pair in runways if 0.03 <= _geodistance_km(*pair) <= 12.0]
    if valid_runways:
        main = max(valid_runways, key=lambda pair: _geodistance_km(*pair))
        anchor = ((main[0][0]+main[1][0])/2, (main[0][1]+main[1][1])/2)
    elif boundaries:
        anchor = (sorted(v[0] for v in boundaries)[len(boundaries)//2],
                  sorted(v[1] for v in boundaries)[len(boundaries)//2])
    elif surfaces:
        anchor = (sorted(v[0] for v in surfaces)[len(surfaces)//2],
                  sorted(v[1] for v in surfaces)[len(surfaces)//2])
    else:
        raise ValueError('Aucune coordonnee aeroport exploitable dans apt.dat : choisir les bornes Manuelles.')

    # The boundary is the canonical airport extent when WED explicitly supplies it.
    # Filter grossly invalid remote boundary nodes against the runway/reference.
    boundary_points = [v for v in boundaries if _geodistance_km(v, anchor) <= radius_km]
    if len(boundary_points) >= 3:
        points = boundary_points
        method = 'Airport Boundary WED (130)'
        ignored = len(boundaries) - len(points)
    elif valid_runways:
        candidates = [v for pair in valid_runways for v in pair] + surfaces
        points = [v for v in candidates if _geodistance_km(v, anchor) <= radius_km]
        ignored = len(candidates) - len(points)
        method = 'pistes et pavements (repli sans boundary utilisable)'
    else:
        points = [v for v in surfaces if _geodistance_km(v, anchor) <= radius_km]
        ignored = len(surfaces) - len(points)
        method = 'pavements (repli sans boundary ni piste utilisable)'

    if not points:
        raise ValueError('Coordonnees apt.dat incoherentes : choisir les bornes Manuelles.')
    lats, lons = zip(*points)
    if max(lons) - min(lons) > 180:
        raise ValueError('Emprise traversant l antimeridien : utiliser des bornes manuelles.')
    bounds = [math.floor(min(lons)-margin), math.floor(max(lons)+margin)+1,
              math.floor(min(lats)-margin), math.floor(max(lats)+margin)+1]
    return {'bounds': bounds, 'method': method, 'anchor': anchor,
            'boundary_nodes': len(boundaries), 'boundary_nodes_used': len(boundary_points),
            'points_kept': len(points), 'points_ignored': ignored,
            'extent_degrees': [min(lons), max(lons), min(lats), max(lats)]}


def bounds_from_airport(airport, margin=0.05):
    return airport_coordinate_diagnostics(airport, margin)['bounds']

def command(tool, args):
    return [str(tool), *map(str, args)]


def execute(cmd, logs, dry_run=False):
    logs.append({'command': cmd, 'status': 'planned' if dry_run else 'running'})
    if dry_run:
        return
    try:
        p = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except FileNotFoundError as exc:
        logs[-1]['status'] = 'missing_executable'
        raise RuntimeError(f'Executable not found: {cmd[0]}') from exc
    logs[-1].update(status='ok' if p.returncode == 0 else 'error', returncode=p.returncode, output_tail=p.stdout[-6000:])
    if p.returncode:
        raise RuntimeError(f'Command failed ({p.returncode}): {" ".join(cmd)}\n{p.stdout[-2000:]}')


def tool_cmd(args, name, argv):
    if args.terragear_env:
        return [str(args.terragear_env.resolve()), name, *map(str, argv)]
    executable = shutil.which(name)
    if not executable:
        raise ValueError(f'{name} not found on PATH. Use --terragear-env /path/to/terragear-env')
    return [executable, *map(str, argv)]


def build_terrain(args, target, airport_info, airport_blocks):
    if airport_info.get('status') == 'missing':
        raise ValueError('No apt.dat. Cannot build airport terrain.')
    if len(airport_blocks) != 1:
        raise ValueError('Choose one airport with --icao for terrain build.')
    bb = list(args.bounds) if args.bounds else bounds_from_airport(airport_blocks[0])
    minlon, maxlon, minlat, maxlat = bb
    if minlon >= maxlon or minlat >= maxlat:
        raise ValueError('Invalid --bounds values')
    airport_dir = target / 'AirportBuild'
    work = args.terrain_work.resolve() if args.terrain_work else airport_dir / 'work'
    terrain = target / 'Terrain'
    if not args.dry_run:
        work.mkdir(parents=True, exist_ok=True)
        terrain.mkdir(parents=True, exist_ok=True)
    log = []
    result = {'status': 'planned' if args.dry_run else 'started', 'bounds': bb,
              'work': str(work), 'terrain': str(terrain), 'commands': log}
    report = airport_dir / 'terrain-report.json'
    def save():
        airport_dir.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    try:
        if args.hgt:
            elev_dir = work / args.elevation_layer
            if not args.dry_run: elev_dir.mkdir(parents=True, exist_ok=True)
            for hgt in args.hgt:
                hgt = hgt.resolve()
                if not hgt.is_file(): raise ValueError(f'HGT missing: {hgt}')
                execute(tool_cmd(args, 'hgtchop', [args.hgt_resolution, hgt, elev_dir]), log, args.dry_run)
            execute(tool_cmd(args, 'terrafit', [elev_dir]), log, args.dry_run)
        elif not (work / args.elevation_layer).is_dir():
            raise ValueError('Missing elevation: supply --hgt FILE (repeatable) or --terrain-work with an existing elevation layer')
        # Geometry from selected apt.dat; preserve all X-Plane pavement/runway records.
        apt = Path(airport_info['apt_dat'])
        if not args.dry_run and not apt.is_file(): raise ValueError('apt.dat export missing')
        opts = [f'--input={apt}', f'--work={work}', f'--min-lon={minlon}', f'--max-lon={maxlon}',
                f'--min-lat={minlat}', f'--max-lat={maxlat}', f'--threads={args.threads}']
        execute(tool_cmd(args, 'genapts', opts), log, args.dry_run)
        layers = [args.elevation_layer]
        if args.landcover_layer:
            layers += list(args.landcover_layer)
        else:
            result['warning'] = 'No land cover supplied; --ignore-landmass produces airport terrain without natural ground cover.'
        layers += ['AirportArea', 'AirportObj']
        opts = [f'--work-dir={work}', f'--output-dir={terrain}',
                f'--min-lon={minlon}', f'--max-lon={maxlon}', f'--min-lat={minlat}', f'--max-lat={maxlat}']
        if args.priorities: opts += [f'--priorities={args.priorities.resolve()}']
        if not args.landcover_layer: opts += ['--ignore-landmass']
        opts += layers
        execute(tool_cmd(args, 'tg-construct', opts), log, args.dry_run)
        result['status'] = 'planned' if args.dry_run else 'completed'
        if not args.dry_run and not list(terrain.rglob('*.btg*')):
            result['status'] = 'no_btg_output'
            raise RuntimeError('tg-construct finished but no .btg or .btg.gz produced; check terrain report')
    except (ValueError, RuntimeError) as exc:
        result['status'] = 'failed'
        result['error'] = str(exc)
        save()
        raise
    save()
    return result
