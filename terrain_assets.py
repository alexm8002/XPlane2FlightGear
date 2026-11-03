"""Shared Natural Earth/SRTM download and TerraGear resource discovery utilities."""
import os
from pathlib import Path
import shutil
import urllib.request
NE_URL = 'https://naturalearth.s3.amazonaws.com/10m_physical/ne_10m_land.zip'

def download(url, destination):
    if destination.exists() and destination.stat().st_size > 1000:
        print('Reuse:', destination)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_suffix(destination.suffix + '.part')
    print('Download:', url, flush=True)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent':'XPlane2FlightGear/0.3.1'}), timeout=90) as response, part.open('wb') as out:
            shutil.copyfileobj(response, out)
        part.replace(destination)
    finally:
        part.unlink(missing_ok=True)


def find_priorities(wrapper, script_dir):
    """Find the real upstream priorities file; never synthesize a fake one."""
    paths = []
    roots = [wrapper.parent, *list(wrapper.parents)[:4], script_dir,
             Path.home() / 'Downloads', Path.home() / 'Documents',
             Path.home() / 'Developer', Path.home() / 'src',
             Path('/opt/homebrew'), Path('/usr/local')]
    suffixes = [Path('share/TerraGear/default_priorities.txt'),
                Path('share/terragear/default_priorities.txt'),
                Path('TerraGear/share/TerraGear/default_priorities.txt'),
                Path('share/default_priorities.txt'),
                Path('default_priorities.txt'),
                Path('src/BuildTiles/Main/default_priorities.txt'),
                Path('src/BuildTiles/default_priorities.txt')]
    for base in roots:
        for suffix in suffixes:
            paths.append(base / suffix)
    for candidate in paths:
        if candidate.is_file() and candidate.stat().st_size > 0:
            print('TerraGear priorities:', candidate, flush=True)
            return candidate.resolve()
    # Look inside nearby source/build trees, but do not crawl the entire home.
    for base in [wrapper.parent, script_dir, Path.home() / 'Downloads']:
        if not base.is_dir(): continue
        for candidate in base.glob('*/**/default_priorities.txt'):
            if candidate.is_file() and candidate.stat().st_size > 0:
                print('TerraGear priorities:', candidate, flush=True)
                return candidate.resolve()
    raise SystemExit(
        'ERROR: TerraGear default_priorities.txt is missing.\n'
        'It is normally shipped in share/TerraGear/ next to the installation.\n'
        'Locate it on your Mac with:\n'
        '  find "$HOME/Downloads" "$HOME/Developer" /opt/homebrew '
        '-name default_priorities.txt -type f 2>/dev/null\n'
        'Then rerun: python3 build_lfll.py --priorities "/actual/path/default_priorities.txt"\n'
        'If none is found, obtain the ORIGINAL file from the TerraGear source/install tree; '
        'do not use an empty placeholder.'
    )


