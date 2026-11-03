"""Extract X-Plane apt.dat airports and prepare TerraGear airport geometry."""
from pathlib import Path
import json
import shutil
import subprocess

HEADERS = {'1', '16', '17'}
SUPPORTED_VERSIONS = {850, 1000, 1050, 1100, 1130, 1200}


def parse_airports(path):
    """Return airport blocks including all records until next airport header."""
    lines = path.read_text(encoding='utf-8-sig', errors='replace').splitlines()
    version_line = next((ln for ln in lines[:12] if ln.strip().split()[:1] and ln.strip().split()[0].isdigit() and int(ln.strip().split()[0]) >= 800), None)
    if version_line is None:
        raise ValueError('apt.dat: version line not found')
    version = int(version_line.strip().split()[0])
    if version not in SUPPORTED_VERSIONS:
        raise ValueError(f'apt.dat version {version} not yet supported (expected {sorted(SUPPORTED_VERSIONS)})')
    result = []
    block = None
    for line in lines:
        p = line.strip().split()
        if not p or p[0].startswith('#'):
            if block is not None: block['records'].append(line)
            continue
        if p[0] in HEADERS and len(p) >= 5:
            if block: result.append(block)
            block = {'icao': p[4], 'name': ' '.join(p[5:]), 'records': [line], 'runways': 0, 'pavements': 0, 'helipads': 0, 'linear_features': 0}
        elif p[0] == '99':
            if block: result.append(block)
            block = None
        elif block is not None:
            block['records'].append(line)
            if p[0] in ('100', '101'): block['runways'] += 1
            elif p[0] == '102': block['helipads'] += 1
            elif p[0] == '110': block['pavements'] += 1
            elif p[0] == '120': block['linear_features'] += 1
    if block: result.append(block)
    return version, result


def find_apt(source, override=None):
    if override:
        path = Path(override).resolve()
        if not path.is_file(): raise ValueError(f'apt.dat not found: {path}')
        return path
    options = [source / 'Earth nav data' / 'apt.dat', source / 'apt.dat']
    return next((x for x in options if x.exists()), None)


def prepare_airport(source, target, apt_override=None, icao=None, genapts=None):
    path = find_apt(source, apt_override)
    if path is None:
        return {'status': 'missing', 'message': 'No apt.dat found in scenery; airport surfaces not converted'}
    version, airports = parse_airports(path)
    if icao:
        airports = [a for a in airports if a['icao'].upper() == icao.upper()]
        if not airports: raise ValueError(f'Airport {icao} not found in {path}')
    if not airports: raise ValueError(f'No airports found in {path}')
    airport_root = target / 'AirportBuild'
    airport_root.mkdir(parents=True, exist_ok=True)
    extracted = airport_root / 'apt.dat'
    # Source version and complete airport blocks preserved; no lossy conversion.
    header = ['I', f'{version} Version - airport subset exported by xp2fg']
    for a in airports: header.extend(a['records'])
    header.append('99')
    extracted.write_text('\n'.join(header) + '\n', encoding='utf-8')
    result = {'status': 'prepared', 'source': str(path), 'apt_dat': str(extracted),
              'version': version, 'airports': [{k:v for k,v in a.items() if k != 'records'} for a in airports],
              'terragear_work': str(airport_root / 'work'),
              'note': 'Airport geometry requires TerraGear genapts + terrain construction (tg-construct); apt.dat alone is not rendered by FlightGear.'}
    if genapts:
        tool = shutil.which(str(genapts)) or str(genapts)
        if len(airports) > 1 and not icao:
            raise ValueError('For running genapts with multiple airports, select one with --icao')
        work = airport_root / 'work'
        work.mkdir(exist_ok=True)
        cmd = [tool, f'--input={extracted}', f'--work={work}', f'--airport={airports[0]["icao"]}']
        result['genapts_command'] = cmd
        proc = subprocess.run(cmd, capture_output=True, text=True)
        result['genapts_exit_code'] = proc.returncode
        result['genapts_stdout_tail'] = proc.stdout[-3000:]
        result['genapts_stderr_tail'] = proc.stderr[-3000:]
        result['status'] = 'geometry_prepared' if proc.returncode == 0 else 'genapts_failed'
    (airport_root / 'airport-report.json').write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    return result
