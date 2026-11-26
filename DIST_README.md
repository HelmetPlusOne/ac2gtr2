# Assetto Corsa to GTR2 - Standalone Track Converter

Standalone Windows distribution for converting Assetto Corsa (AC), rFactor 1 (rF1), and Stock Car Extreme (GSCE/SCE) tracks into GTR2 locations. No pre-installed Python, Blender, Assetto Corsa, GTR2, or rFactor 2 is required. Drag and drop an AC, rF1, or SCE track folder (or `.zip` archive) onto `ac2gtr2.exe`.

Converted tracks are automatically installed into the `out\` folder next to `ac2gtr2.exe`. To play a converted track in GTR2, copy `out\<track>\` into `<GTR2>\GameData\Locations\`.

## Changelog

### Stock Car Extreme & rFactor 1 Input Support, Track Brightness

- **Automatic Stock Car Extreme (GSCE/SCE) track conversion**: Detects SCE tracks automatically (via Reiza `.scn`/`.gdb` markers or encrypted GMTs), decrypts GSCE GMT binaries and encrypted textures (such as `SKIDHARD.TGA`) via `rfactorcrypt.exe`, clears secondary `UV4..7` mesh flags while migrating bump `uv4 -> uv2`, strips unsupported GSCE/rF1 local material/texture flags (`0x00040000`/`0x00000200`/`0x00000100`) and disambiguates conflicting cross-GMT material definitions to prevent GTR2 `Material global/local mismatch` errors, remaps Reiza/GSCE custom shaders (`SIMPLEROADSHADERV2`, `ADVANCEDNONLINEDROADSHADERV2`, `TERRAINV2MAPPEDVC`, `TREESHADERBB`, `RZRUMBLESTRIP`, `RZGRASSSPRITES*`, `DIFFUSE*ALPHASPEC`, etc.) to stock GTR2 `GFX.GTR` shaders with clean per-level texture slicing, recovers referenced shared textures and animated frames from installed `ReizaCommon` / `commonmaps.MAS`, clamps `.AIW` `startinggrid`/`pitspots`/`garagespots` to actual slot counts, and sanitizes venue folder spaces.
- **Automatic rFactor 1 (`.scn` + `.gdb` + `.mas`) track conversion (`rf1_track.py`)**: Detects AC vs rFactor 1 vs SCE track folders and `.zip` archives automatically, unpacks `.mas` archives via `masunpack.exe`, converts rF1 GMT v2.00 meshes directly to GTR2 GMT v2.10 (fixing per-submesh material indices, remapping rF1-only shaders to stock `GFX.GTR` shaders, appending required `MATH` stages for `L2BUMPSPECMAPT0`/`L2BUMPSPECT0`, supporting animated/BIK textures, and remapping `.tdf` surface prefixes to `specialfx.tec`), sanitizes spaces in texture filenames, clamps `.AIW` `garagespots` to `<= 3`, assigns GTR2-safe multi-layout IDs and `VenueName` entries, and composes GTR2 menu/loading images.
- **`track_shadows` support in rF1/SCE mode**: Applies `[track] track_shadows` (`compact` / `off` / `full`, or `--track-shadows`) to `.scn` -> `.trk` conversion while always stripping `Dynamic, Texture` casters; all other AC-specific conversion options remain ignored for rF1 and SCE tracks.
- **Track brightness boost (`track_brightness`)**: Added `[track] track_brightness` (`[0.0, 1.0]`, default `0.5`, CLI `--track-brightness`) for AC tracks (`0.0` disabled, `0.5` subtle daylight lift, `1.0` bright sunny look), smoothly brightening GMT diffuse/ambient material colors, daytime `.gdb` scene lighting, and stage-0 diffuse DDS textures while keeping night lighting dark.

### Initial Release

- **Full AC-to-GTR2 conversion pipeline**: Converts Assetto Corsa tracks (single-layout, multi-layout, or `.zip` archives) into GTR2 locations with native unencrypted GMT v2.10 meshes, DX9 power-of-two DDS textures, `.trk`, `.gdb`, `.grd`, `.AIW`, `.cam`, and GTR2 UI images (`_LOD.jpg`, `_LOD_2.jpg`, `Thmb.tga`, `Track.bmp`).
- **Native GTR2 GMT v2.10 binary converter (`gmt_gtr2.py`)**: Converts `io_rf2_gmt_release` rF2 GMT v2.52 exports directly into GTR2 GMT v2.10 (`GMOTOR_GMT_2.10\0` at offset `0x08`, 532-byte header, 108-byte mesh descriptors, 36-byte `FaceData` with plane distance `D` and mesh-local vertex indices, rebuilt face normals and bounding-sphere radii, and stock `GFX.GTR` shaders `L2DIFFUSET0`, `L2BUMPT0XT1`, and `L2BUMPSPECMAPT0` + `MATH` stages).
- **GTR2 surface physics mapping**: Maps AC surfaces to GTR2 `specialfx.tec` material prefixes (`road`, `conc`, `rmbl`, `gras`, `grvl`, `dirt`, `cwal`, `twall`, `grdr`) and writes AC road/kerb dry and wet grip values into `.gdb`.
- **GTR2 performance optimizations (shadows & spatial culling)**: Added `[track] track_shadows` (`compact` default / `off` / `full`) in `ac2gtr2.ini` (and `--track-shadows` CLI override) to match stock GTR2's projected shadow budgets (`ShadowReceiver=True` on road/ground only so cars cast shadows onto the track, `ShadowCaster=(Static, Solid, 32/64)` only on compact opaque structures `<= 60 m` / `<= 8000` tris, no expensive foliage `Texture` shadow maps, `MaxShadowRange=(200.0)`); added 250 m spatial tile splitting for wide AC meshes in `blender_stage.py`, stock-style `LODOut` distance culling for grass/foliage/small props, and tighter rearview clip plane (`400 m`).
- **Configuration file support (`ac2gtr2.ini`)**: Supports `[track]` `texture_scale`, `texture_scale_min_size`, `road_smoothness`, `mesh_coarsening`, `strip_unused_gmt_data`, and `track_shadows`, with matching CLI overrides.
- **All upstream AC pipeline fixes included**: MultiMap alpha and tint baking, all-zero alpha preservation in `bake_layers.py`, malformed DDS re-encoding in `fix_textures.py`, 58-char texture filename cap, skinned KN5 mesh parsing, tolerant AC `.ini` parsing, deterministic Blender QEM mesh coarsening, C2 triweight quadratic MLS road smoothing, and 31-character `Instance=` name capping.

## Usage

### 1. Drag and Drop (Windows Explorer)

Drag and drop any of the following directly onto `ac2gtr2.exe`:

- **An AC track folder** (e.g. `targa_tas_21_s1_high_plains`)
  — if the track has multiple layouts (`models_*.ini`), the console will list
  them and prompt you to pick one.
- **Multiple AC track folders** at once — converts each track sequentially.
- **An AC track `.zip` archive** — extracts it into `work\_zips\` and converts it.
- **A batch `.txt` track list** — runs `convert_multiple_tracks` on the file.

When launched via drag-and-drop, the console window stays open after completion
so you can review the output.

### 2. Single Track CLI (`build_track`)

```bat
ac2gtr2.exe build_track "<path to AC track folder or zip>"
```

You can also pass the track path directly without `build_track`:

```bat
ac2gtr2.exe "<path to AC track folder or zip>" [--layout <name>]
```

Common options:

- `--layout <name>` — layout name for multi-layout tracks (the part after `models_` in `models_<name>.ini`).
- `--hour HH:MM` — session start time (default `12:00`).
- `--texture-scale <0..1>` — texture resolution scale per dimension (overrides `ac2gtr2.ini`; default `0.5`).
- `--texture-scale-min-size <px>` — minimum `max(width, height)` (inclusive) to scale textures (overrides `ac2gtr2.ini`; default `1024`, so `1024x1024`+ are scaled while `512x512` and smaller are kept unscaled). Also acts as a destination floor for scaled textures: `max(width, height)` is not reduced below the largest power-of-two size smaller than this threshold.
- `--road-smoothness <0..1>` — C2 triweight quadratic MLS Y-smoothing strength for physical road meshes (overrides `ac2gtr2.ini`; default `0.5`). Mapped uniformly to effective `road_smooth_sigma` in `[0m, 8m]` (`0.0` disables smoothing, `0.5 -> 4m` default, `1.0 -> 8m` max).
- `--mesh-coarsening <0..1>` — deterministic boundary-preserving Blender QEM mesh coarsening strength (overrides `ac2gtr2.ini`; default `0.0` disabled, `1.0` strongest safe reduction keeping major track geometry intact).
- `--strip-unused-gmt-data` / `--no-strip-unused-gmt-data` — strip unused collision `FaceData`/`NormalData` tables from `CollTarget=False` visual GMTs and drop dummy `UVTangents` on flat-normal/flat-spec cutouts (`L2BUMPSPECMAPT0` → `L2DIFFUSET0`) without altering geometry or road meshes (overrides `ac2gtr2.ini`; default enabled).
- `--track-shadows <compact|off|full>` — GTR2 track shadow casting mode (overrides `ac2gtr2.ini`; default `compact`: `ShadowReceiver=True` on road/ground only, `ShadowCaster=(Static, Solid, 32/64)` only on compact structures `<= 60 m` / `<= 8000` tris, `MaxShadowRange=(200.0)`; `off` disables track casters while keeping car shadows on the road; `full` enables opaque + foliage casters up to `150 m`). Applies to both AC and rF1 tracks.
- `--track-brightness <0..1>` — track brightness boost for AC tracks (overrides `ac2gtr2.ini`; `0.0` disabled, `0.5` default subtle daylight lift, `1.0` bright sunny look; night lighting stays dark).
- `--jobs <n>` — number of parallel Blender and texture conversion workers.
- `--skip-geometry` — reuse previously exported GMT meshes in `work\<track>\gmt\`.
- `--install "<dir>"` — override the automatic install destination (defaults to `out\` next to `ac2gtr2.exe`).

Run `ac2gtr2.exe build_track --help` for the full option list.

### 3. Batch Conversion CLI (`convert_multiple_tracks`)

Create a text file with one `<path to track>;<layout name>` entry per line
(`<layout name>` may be left empty; blank lines and `#` comments are ignored):

```text
C:\tracks\targa_tas_21_s1_high_plains;
C:\tracks\ks_nordschleife;nordschleife
C:\tracks\ks_nordschleife;endurance
```

Then run:

```bat
ac2gtr2.exe convert_multiple_tracks "<path to tracks.txt>"
```

Each entry is converted and installed automatically into `out\`. Any entry that
fails is logged and skipped so the remaining tracks still complete.

### 4. Diagnostic Commands

```bat
ac2gtr2.exe check_setup ["<path to AC track>"]
ac2gtr2.exe verify_install "out\<track>" "work\<track>"
```

## Directory Layout & Bundled Tools

All tool and data paths are resolved relative to `ac2gtr2.exe`:

| Path | Contents / Purpose |
| --- | --- |
| `ac2gtr2.exe` | Single executable bundling **Python 3.10**, **NumPy**, and the AC-to-GTR2 converter scripts (`build_track`, `convert_multiple_tracks`, `check_setup`, `verify_install`, and all conversion stages). |
| `ac2gtr2.ini` | Configuration file read from the same folder as `ac2gtr2.exe` (`[track]` `texture_scale`, `texture_scale_min_size`, `road_smoothness`, `mesh_coarsening`, `strip_unused_gmt_data`, `track_shadows`, `track_brightness`). |
| `tools\blender\` | Portable **Blender 3.6 LTS** (`blender.exe`) with the **rFactor 2 GMT Exporter add-on (`io_rf2_gmt_release`)** pre-installed under `3.6\scripts\addons\io_rf2_gmt_release\`. |
| `tools\blender_scripts\` | Plain Python scripts executed inside Blender's own interpreter (`blender_stage.py`, `kn5.py`, `backdrop.py`, `mesh_coarsen.py`). |
| `tools\texconv\texconv.exe` | **Microsoft DirectXTex `texconv.exe`** for DX9 power-of-two DDS conversion, mipmap generation, multilayer material baking, and menu/loading image composition. |
| `data\ac\system\data\surfaces.ini` | Default Assetto Corsa surface physics definitions. |
| `data\gtr2\GameData\Locations\specialfx.tec` | GTR2 terrain/surface feedback reference file. |
| `out\` | Ready-to-install GTR2 track folders produced by conversions. |
| `work\` | Intermediate working files (extracted textures, baked layers, raw GMTs, AIW/CAM, extracted zips). |

## Licenses & Third-Party Notices

This standalone bundle includes third-party tools and runtime libraries, each
governed by its own license:

| Component | Location | License & Source |
| --- | --- | --- |
| **Python 3.10** | `ac2gtr2.exe` | **PSF License Version 2** — Python Software Foundation (https://docs.python.org/3/license.html) |
| **NumPy** | `ac2gtr2.exe` | **BSD 3-Clause License** — NumPy Developers (https://numpy.org/doc/stable/license.html) |
| **PyInstaller Bootloader** | `ac2gtr2.exe` | **GNU GPL v2 or later with Bootloader Exception** (https://pyinstaller.org/en/stable/license.html) |
| **Blender 3.6 LTS** | `tools\blender\` | **GNU GPL v2 or later** (with GPL-3.0-compatible third-party libraries). Full license texts are included in `tools\blender\copyright.txt` and `tools\blender\license\`. Source: https://www.blender.org/ |
| **rFactor 2 GMT Exporter (`io_rf2_gmt_release` v24.3.9)** | `tools\blender\3.6\scripts\addons\io_rf2_gmt_release\` | Authored by **rFactor2 IO Group** (distributed via the Studio-397 community forum: https://forum.studio-397.com/index.php?threads/blender-rfactor2-export-scripts.74821/). Shipped without a separate license file; check the author's terms before redistributing. |
| **DirectXTex `texconv.exe`** | `tools\texconv\texconv.exe` | **MIT License** — Microsoft Corporation (https://github.com/microsoft/DirectXTex/blob/main/LICENSE). |

This converter is based on `ac-to-ams1` and https://github.com/Mewzapchu/ac-to-rf2.

## Included Game Files (Assetto Corsa & GTR2)

To allow standalone conversion on a Windows machine where Assetto Corsa and
GTR2 are not installed, the `data\` directory contains two small reference files
copied from local game installations:

- **Assetto Corsa** (© Kunos Simulazioni):
  - `data\ac\system\data\surfaces.ini` — default AC surface physics table.
- **GTR2** (© SimBin Studios):
  - `data\gtr2\GameData\Locations\specialfx.tec` — GTR2 terrain/surface feedback reference.

## Notes

- Converted tracks contain the original track's models and textures; do not
  redistribute converted content without permission from the original authors.
