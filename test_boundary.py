"""Quick regression test using user-supplied apt.dat as parameter, not bundled."""
import sys
from pathlib import Path
from airport import parse_airports
from terrain import airport_coordinate_diagnostics
from build_airport import needed_tiles

def main(path):
    _, airports = parse_airports(Path(path))
    for airport in airports:
        result = airport_coordinate_diagnostics(airport)
        print(airport['icao'], result['method'], result['bounds'],len(needed_tiles(result['bounds'])))

if __name__ == '__main__':
    main(sys.argv[1])
