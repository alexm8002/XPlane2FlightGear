# XPlane2FlightGear

**Convert X-Plane airport scenery into FlightGear scenery.**

**Version:** 0.3.14.6  
**Platform:** Developed and tested primarily on macOS Apple Silicon (ARM64)  
**Interface:** Python / PySide6 GUI, with a command-line workflow

XPlane2FlightGear is a Python-based scenery conversion toolkit that combines airport terrain generation with the conversion and placement of X-Plane DSF/OBJ8 scenery objects. The goal is to make custom X-Plane airports usable in FlightGear without rebuilding the airport manually.

The project was developed using **Digital Design LFLL Lyon** as its main test scenery. Other airports can be processed, although compatibility varies with the source scenery and the features it uses.

## Features

- Builds FlightGear airport terrain using **TerraGear**, based on airport definitions in `apt.dat`.
- Reads X-Plane DSF object placements using **DSFTool** and converts supported OBJ8 models into FlightGear-compatible scenery objects.
- Converts object coordinates, orientations and placements for FlightGear.
- Adjusts object elevations against generated BTG terrain.
- Projects supported X-Plane draped object geometry onto the FlightGear terrain surface.
- Converts supported OBJ8 lighting commands, including `LIGHT_NAMED`, `LIGHT_PARAM` and night texture declarations (`TEXTURE_LIT`).
- Resolves X-Plane virtual asset paths (`lib/...`) through `library.txt` export definitions.
- Reads multiple libraries, including packages in `Resources/default scenery`, `Custom Scenery`, and user-supplied third-party library folders.
- Provides a scrollable **PySide6 GUI**, a conversion log and 2D/3D scenery inspection tools.
- Produces JSON reports to help diagnose missing assets, terrain generation and lighting conversion.

## Requirements

- **Python 3** and the dependencies in `requirements.txt`:
  - PySide6 >= 6.6
  - PyOpenGL >= 3.1
  - Pillow >= 10.0
- A working **TerraGear** toolchain, exposed through the `terragear-env` launcher.
- **DSFTool** (Laminar Research X-Plane scenery tools) to read X-Plane DSF files.
- **GDAL**, including the `ogr2ogr` executable, available in `PATH`.
- Source scenery containing the relevant airport data (typically `apt.dat`, DSF files and local objects).
- Elevation data (HGT/SRTM): supply local HGT files, or allow the terrain builder to retrieve the required tiles where network access permits.

**An installed copy of X-Plane is not required.** It is only useful when the source scenery refers to assets from X-Plane's default or other installed libraries. Those assets must be available separately to be converted. TerraGear and DSFTool remain external prerequisites.

## Installation

Clone the repository or download a source archive, then install the Python dependencies:

```bash
python3 -m pip install -r requirements.txt
```

Make sure `terragear-env`, `DSFTool` and `ogr2ogr` are installed and executable. On macOS, the TerraGear and DSFTool paths can be configured in the GUI.

## Graphical interface

```bash
python3 gui.py
```

On macOS, `launch.command` also starts the GUI from its containing directory.

In the **Conversion** tab:

1. Select the X-Plane scenery folder.
2. Select the airport ICAO code, if necessary.
3. Choose a **new** FlightGear output directory.
4. Set the TerraGear launcher and DSFTool paths.
5. Optionally specify the X-Plane installation root and any additional third-party library directories.
6. Start the full scenery build and follow progress in the **Journal** tab.

The Conversion tab supports vertical scrolling, and the interface includes scenery inspection tools.

## Command-line usage

Example for an airport with ICAO code `LFLL`:

```bash
python3 build_all.py \
  --source "/path/to/Custom Scenery/My Airport" \
  --output "$HOME/Desktop/LFLL-FlightGear" \
  --icao LFLL \
  --terragear-env "/path/to/TerraGear-macOS-arm64/terragear-env" \
  --dsftool "/path/to/DSFTool" \
  --elevation apply
```

To load a separate X-Plane installation and multiple external libraries:

```bash
python3 build_all.py \
  --source "/path/to/My Airport" \
  --output "$HOME/Desktop/MyAirport-FlightGear" \
  --icao LFLL \
  --terragear-env "/path/to/terragear-env" \
  --dsftool "/path/to/DSFTool" \
  --xplane-root "/path/to/X-Plane 12" \
  --library "/path/to/ThirdPartyLibraryA" \
  --library "/path/to/ThirdPartyLibraries" \
  --elevation apply
```

The `--library` option is repeatable. It accepts a package containing `library.txt` or a parent folder with several library packages.

To disable automatic library discovery:

```bash
python3 build_all.py \
  --source "/path/to/My Airport" \
  --output "$HOME/Desktop/MyAirport-FlightGear" \
  --icao LFLL \
  --terragear-env "/path/to/terragear-env" \
  --dsftool "/path/to/DSFTool" \
  --no-auto-libraries
```

Run `python3 build_all.py --help` for the complete list of options, including terrain bounds, HGT files, draping and elevation controls. An existing output directory is not overwritten unless explicitly requested with `--overwrite-output`; use this flag with care.

## How X-Plane libraries are resolved

X-Plane `lib/...` references are **virtual asset names**, not literal directories. Their real files are declared in the `EXPORT` entries of a package's `library.txt`.

For example, the stock X-Plane 12 package:

```text
Resources/default scenery/sim objects/library.txt
```

contains a mapping equivalent to:

```text
EXPORT lib/airport/lights/slow/taxi_edge.obj apt_lights/slow/elevated_edge_twy_B.obj
```

The physical OBJ8 file is located relative to that package:

```text
Resources/default scenery/sim objects/apt_lights/slow/elevated_edge_twy_B.obj
```

The converter discovers `library.txt` files across multiple packages, including both:

```text
Resources/default scenery/airport scenery/library.txt
Resources/default scenery/sim objects/library.txt
```

For a full X-Plane 12 installation, point **X-Plane library** to the X-Plane root directory, **not** to an individual `lib/` directory. Additional libraries can be added independently, and none is mandatory for converting self-contained scenery.

If referenced assets are unavailable, conversion continues where possible and the missing references are recorded in the reports. Some third-party scenery may require additional libraries such as SAM or openSAM.

## Output and FlightGear launch

The output directory contains generated scenery and diagnostic files, including (as applicable):

```text
MyAirport-FlightGear/
├── Terrain/
├── Objects/
├── AirportBuild/
├── report.json
├── build-summary.json
├── draped-build-report.json
└── library-resolution-report.json
```

Load the generated scenery into FlightGear:

```bash
fgfs --airport=LFLL \
  --fg-scenery="$HOME/Desktop/LFLL-FlightGear" \
  --timeofday=midnight
```

Change the ICAO code, output path and time of day for your airport.

## Known limitations

- This is **not** a lossless or complete implementation of every X-Plane scenery format.
- X-Plane-specific DSF `.pol`/`.lin` features, facades, forests, orthophoto/custom mesh data and advanced OBJ8 animations/materials are not fully reproduced.
- `LIGHT_NAMED`, `LIGHT_PARAM` and `TEXTURE_LIT` are supported to varying degrees. Some custom light types, spotlight effects and halos require additional work; `LIGHT_SPILL_CUSTOM` is not fully reproduced.
- Converted lighting may not exactly match X-Plane's intensity, beam direction or bloom.
- Some lights generated in BTG terrain may be hidden by overlaid draped surfaces; the behavior depends on source geometry and elevation.
- Assets referenced through a library cannot be converted unless their corresponding source files are available.
- Certain asset formats such as `.agp` require additional handling and should not be assumed equivalent to a standalone `.obj`.
- The full workflow depends on the availability of external TerraGear, DSFTool and GDAL executables.
- Development and practical validation have focused on macOS ARM64; other platforms may require adjustments.

## Troubleshooting

If scenery objects or lights are missing, inspect:

- `report.json` — conversion details and unresolved asset references.
- `library-resolution-report.json` — library mappings and missing asset diagnostics.
- `build-summary.json` — overall build counts and results.
- `draped-build-report.json` — projected or disabled draped surfaces.
- The **Journal** tab — paths of discovered `library.txt` files, plus stock package and `TAXI_EDGE_EXPORT` diagnostics.

For missing airport edge lights, verify that the X-Plane `sim objects` library is indexed and that its `apt_lights/slow/elevated_edge_twy_B.obj` file exists. A successfully resolved asset still needs to be rendered and checked in FlightGear.

## Project status

**v0.3.14.6** is the current project snapshot documented here. It incorporates the library resolution and night-lighting work validated during development on LFLL. The converter remains an evolving tool, not a guaranteed one-click converter for every X-Plane airport.

## Licensing and third-party assets

This repository is intended to host the **converter's source code**, not commercial X-Plane scenery, copyrighted airport packages, X-Plane default assets or third-party libraries. Users must obtain and use those resources under their own applicable licenses.

**Before publishing:** add a `LICENSE` file specifying the license you have chosen for your original code. Do not assume that publishing a GitHub repository automatically grants an open-source license. Check licenses of any bundled or adapted external code separately.

## Contributions

Bug reports and reproducible examples are welcome. Useful reports include the converter version, Python version, OS, the `build-summary.json` and relevant missing-asset entries from `report.json`. Do not upload commercial scenery assets without permission.
