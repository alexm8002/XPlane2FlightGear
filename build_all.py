#!/usr/bin/env python3
"""Generic airport full orchestration: terrain + X-Plane static DSF objects + texture copies.
Not an exact complete airport conversion: X-Plane-specific features remain unsupported.
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run(cmd):
    print('\n+', ' '.join(map(str, cmd)), flush=True)
    subprocess.run(list(map(str, cmd)), check=True)

def main():
    ap = argparse.ArgumentParser(description='Build airport terrain and DSF/OBJ8 object overlay in one run')
    ap.add_argument('--source', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--icao', help='Select an airport from apt.dat')
    ap.add_argument('--bounds', nargs=4, type=int, metavar=('W','E','S','N'))
    ap.add_argument('--max-tiles', type=int, default=16)
    ap.add_argument('--margin', type=float, default=0.05, help='Automatic bounds padding (degrees).')
    ap.add_argument('--terragear-env', type=Path, default=Path.home()/'Downloads/TerraGear-macOS-arm64/terragear-env')
    ap.add_argument('--priorities', type=Path)
    ap.add_argument('--hgt', type=Path, action='append', default=[])
    ap.add_argument('--dsftool', type=Path, help='Location of Laminar Research DSFTool')
    ap.add_argument('--library', type=Path, action='append', default=[], help='Optional library scenery package (repeatable)')
    ap.add_argument('--xplane-root', type=Path, help='Optional X-Plane 12 installation root for stock libraries')
    ap.add_argument('--no-auto-libraries', action='store_true', help='Disable automatic discovery of X-Plane libraries beside the source scenery')
    ap.add_argument('--skip-terrain', action='store_true', help='Keep validated terrain, only rebuild objects')
    ap.add_argument('--elevation', choices=['off','preview','apply'], default='apply', help='Sample actual BTG elevation after objects build; off by default to avoid unreviewed changes')
    ap.add_argument('--elevation-clearance', type=float, default=0.0, help='Metres above sampled terrain')
    ap.add_argument('--overwrite-output', action='store_true', help='Explicitly remove an existing destination for a fresh full build')
    ap.add_argument('--drape-edge', type=float, default=25., help='Maximum edge in metres for draped triangulation')
    a = ap.parse_args()
    if a.skip_terrain:
        ap.error('v0.3.11 complete rebuild requires terrain generation; --skip-terrain is disabled')
    if a.elevation!='apply':
        ap.error('v0.3.11 full pipeline requires --elevation apply')
    source, output = a.source.expanduser(), a.output.expanduser()
    if not source.is_dir(): ap.error(f'Scenery package does not exist: {source}')
    source_abs=source.resolve()
    output_abs=output.resolve()
    if (output_abs in (Path('/'), Path.home().resolve(), source_abs, source_abs.parent.resolve())
            or output_abs in source_abs.parents or source_abs in output_abs.parents
            or output_abs==Path('/Volumes') or output_abs==Path('/Users')):
        ap.error('Unsafe output directory; source, home, system and ancestor paths are protected')
    if output.exists():
        if not a.overwrite_output: ap.error(f'Output exists: {output}. Choose a new destination, or explicitly pass --overwrite-output')
        if output.resolve() == source.resolve() or output.resolve() == source.parent.resolve() or output.resolve() == Path.home():
            ap.error('Refusing to delete source or home folder')
        shutil.rmtree(output)
    output.mkdir(parents=True)
    if not a.skip_terrain:
        terrain_cmd = [sys.executable, ROOT/'build_airport.py', '--source', source, '--output', output,
                       '--terragear-env', a.terragear_env.expanduser()]
        if a.priorities: terrain_cmd += ['--priorities', a.priorities.expanduser()]
        if a.icao: terrain_cmd += ['--icao',a.icao]
        if a.bounds: terrain_cmd += ['--bounds',*map(str,a.bounds)]
        terrain_cmd += ['--max-tiles',str(a.max_tiles),'--margin',str(a.margin)]
        for hgt in a.hgt: terrain_cmd += ['--hgt', hgt.expanduser()]
        run(terrain_cmd)
    elif not list((output/'Terrain').rglob('*.btg*')):
        ap.error('--skip-terrain requires an existing generated Terrain with BTG files')

    dsftool = a.dsftool.expanduser() if a.dsftool else Path(shutil.which('DSFTool') or '')
    if not dsftool.is_file():
        ap.error('DSFTool is required to convert objects. Pass --dsftool /absolute/path/to/DSFTool')
    # Optional external X-Plane libraries. Only the selected scenery is required.
    # Automatic discovery can be disabled, and no installed X-Plane is necessary.
    packages = []
    if not a.no_auto_libraries:
        custom = source.parent
        if custom.name == 'Custom Scenery' and custom.is_dir():
            packages.extend(sorted(d for d in custom.iterdir()
                                   if d.is_dir() and (d / 'library.txt').is_file()))
            guessed_root = custom.parent
            default_root = guessed_root / 'Resources' / 'default scenery'
            if default_root.is_dir() and a.xplane_root is None:
                packages.extend(sorted(x.parent for x in default_root.rglob('library.txt')))
    if a.xplane_root is not None:
        xp_root = a.xplane_root.expanduser().resolve()
        if not xp_root.is_dir():
            ap.error(f'--xplane-root is not a directory: {xp_root}')
        defaults = xp_root / 'Resources' / 'default scenery'
        if defaults.is_dir():
            packages.extend(sorted(x.parent for x in defaults.rglob('library.txt')))
        custom = xp_root / 'Custom Scenery'
        if custom.is_dir():
            packages.extend(sorted(d for d in custom.iterdir()
                                   if d.is_dir() and (d / 'library.txt').is_file()))
        if not defaults.is_dir():
            print('WARNING: Stock X-Plane libraries are missing in', defaults, flush=True)
    for library_root in a.library:
        library_root = library_root.expanduser().resolve()
        if not library_root.is_dir():
            print('WARNING: Optional library directory does not exist:', library_root, flush=True)
            continue
        if (library_root / 'library.txt').is_file():
            packages.append(library_root)
        else:
            # A selected parent may contain several third-party scenery libraries.
            # Do not require an installed X-Plane distribution.
            found = sorted(p.parent for p in library_root.rglob('library.txt') if p.is_file())
            if not found:
                print('WARNING: No library.txt found in optional library folder:', library_root, flush=True)
            packages.extend(found)
    packages = list(dict.fromkeys(p.resolve() for p in packages))
    if not packages:
        print('INFO: No external libraries; local OBJ8 conversion continues.', flush=True)
    print('LIBRARY_PACKAGES:', len(packages), flush=True)
    for package in packages:
        print('  LIBRARY_TXT:', package / 'library.txt', flush=True)
    stock_packages = [p for p in packages if 'Resources/default scenery' in p.as_posix()]
    if a.xplane_root is not None or stock_packages:
        print('STOCK_LIBRARY_PACKAGES:', len(stock_packages), flush=True)
        for name in ('airport scenery', 'sim objects'):
            found = [p for p in stock_packages if p.name == name]
            print('  STOCK_%s: %s' % (name.upper().replace(' ', '_'),
                  ', '.join(map(str, found)) if found else 'NOT FOUND'), flush=True)
    # Clear only previous object overlay, not freshly built terrain.
    if (output/'Objects').exists(): shutil.rmtree(output/'Objects')
    cmd = [sys.executable, ROOT/'xp2fg.py', source, output, '--airport-mode', 'off',
           '--object-placement', 'agl', '--dsftool', dsftool]
    for package in dict.fromkeys(packages): cmd += ['--library', package]
    run(cmd)
    reportpath = output/'report.json'
    report = json.loads(reportpath.read_text()) if reportpath.exists() else {}
    btg = len(list((output/'Terrain').rglob('*.btg*')))
    stg = len(list((output/'Objects').rglob('*.stg')))
    summary = {'terrain_btg': btg, 'object_stg': stg, 'objects_placed': report.get('placed',0),
               'objects_skipped': len(report.get('skipped',[])), 'models': report.get('models',0),
               'unsupported': ['Native X-Plane DSF .pol/.lin, facades/forests',
                               'orthophotos/custom DSF mesh', 'OBJ8 animations and advanced materials']}
    (output/'build-summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print('\nCOMPLETE PIPELINE SUMMARY:\n'+json.dumps(summary, indent=2), flush=True)
    if summary['objects_placed'] == 0:
        print('WARNING: No placed objects; check output/report.json (missing library or OBJ format).', flush=True)
    if a.elevation != 'off':
        elevation_cmd=[sys.executable, ROOT/'fix_elevation.py','--scenery',output,'--clearance',str(a.elevation_clearance),'--model-base','footprint']
        if a.elevation=='apply': elevation_cmd.append('--apply')
        run(elevation_cmd)
    elevation_report=json.loads((output/'elevation-report.json').read_text())
    summary['object_alignment']={'mode':elevation_report['model_base'],'placements':elevation_report['placement_resolved'], 'footprint_objects':len(elevation_report.get('footprint_stats',[])), 'max_lift_m':max((x['lift_m'] for x in elevation_report.get('footprint_stats',[])),default=0)}
    run([sys.executable, ROOT/'project_draped.py', '--scenery', output, '--max-edge', str(a.drape_edge)])
    drape_report = json.loads((output/'draped-build-report.json').read_text())
    summary['draped_projected'] = drape_report['projected']
    summary['draped_disabled'] = drape_report['disabled']
    summary['version']='0.3.15.0'
    summary['optional_library_packages'] = len(packages)
    summary['unresolved_asset_refs'] = report.get('unresolved_asset_refs', {})
    summary['night_lights']=report.get('night_lights',{})
    terrain_report_path=output/'AirportBuild'/'generic-build-report.json'
    if terrain_report_path.exists():summary['airport']=json.loads(terrain_report_path.read_text())['icao']
    (output/'build-summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print('FlightGear: fgfs --airport='+str(summary.get('airport','YOUR_ICAO'))+' --fg-scenery="'+str(output)+'"', flush=True)

if __name__ == '__main__':
    try: main()
    except subprocess.CalledProcessError as e: raise SystemExit(f'ERROR: pipeline step failed ({e.returncode})')
