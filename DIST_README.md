# Assetto Corsa to GTR 2 - Standalone Track Converter

Standalone Windows distribution for converting Assetto Corsa (AC), tracks into GTR 2 locations. No pre-installed Python, Blender, Assetto Corsa, GTR 2, or rFactor 2 is required. Drag and drop an AC, track folder (or `.zip` archive) onto `ac2gtr2.exe`.

Converted tracks are automatically installed into the `out\` folder next to `ac2gtr2.exe`. To play a converted track in GTR 2, copy `out\<track>\` into `<GTR2>\GameData\Locations\`.

Additionally the tracks from rFactor 1 and Stock Car Extreme are also supported as an input - these tracks are only re-packaged with minimal changes (no Blender run) and conversion options are not supported for them.

## Changelog

### 28 Nov 2026

- **rFactor 1 shader, tangent, and JPG/PNG texture fixes (#1)**: Maps rF1 `*LERPT1*VERTEXALPHA` bump and diffuse shaders to valid stock GTR2 multiply shaders (`L2BUMPT0XT1`, `L*DIFFUSET0XT1*`) with exact stage counts and re-indexed bump UVs, fixes the rF1->GTR2 mesh tangent stream offset (`+0x1C`), and converts `.jpg`/`.jpeg`/`.png` textures packed inside rF1 `.mas` archives to DX9 `.dds` instead of replacing them with blank fallbacks.
- **CSP hidden mesh support (`ext_config.ini`)**: Honors unconditional CSP `[MESH_ADJUSTMENT]` rules with `IS_RENDERABLE = 0` / `IS_VISIBLE = 0` / `IS_ACTIVE = 0`, preventing invisible physics bump overlays and replaced 2D crowd placeholders (e.g., magenta surfaces/crowds on tracks like Sebring 60s) from rendering visibly in GTR2 while keeping physical collision intact.

### 27 Nov 2026

- **HAT crash fix for large AC tracks**: Strips zero-area degenerate faces, long needle slivers (`> 50 m` span, `< 10 m` wide), and oversized (`> 50 m`) or distant (`> 200 m` from AIW) off-road terrain HAT triangles, sets `HATTarget=False` on collision walls, and prevents AC meshes from overwriting `skyboxi.gmt` / pit gate GMTs.
- **Portable dist-local tool paths & rFactorTools bundling**: Removed all hardcoded local tool paths, made `dist` builds always resolve tools locally (with direct Python runs loading tools from `<script_path>/dist/`), and bundled `rFactorTools` (`masunpack.exe`, `rfactorcrypt.exe`, and runtime libraries) into `tools\rfactortools\` so rF1/SCE conversions work standalone.

### 26 Nov 2026

- **Stock Car Extreme (GSCE/SCE) track support**: Auto-detects SCE tracks, decrypts GMTs/textures, remaps Reiza shaders, recovers shared `ReizaCommon` textures, and fixes AIW/material compatibility.
- **rFactor 1 track support**: Auto-detects rF1 tracks and `.zip` archives, unpacks `.mas` archives, converts GMT v2.00 meshes to GTR2 GMT v2.10, and generates GTR2 scenes, AIW, and UI images.
- **Track brightness (`track_brightness`)**: brightening the track is toned down, applies twice less brightness than in the previous version, so default value `0.5` produces the same results as `0.25` in the previous version.

### Initial Release

- **AC-to-GTR2 pipeline**: Converts single-layout, multi-layout, and `.zip` Assetto Corsa tracks into GTR2 locations with menu/loading images.
- **Native GTR2 GMT v2.10 converter**: Converts rF2 GMT v2.52 exports directly into unencrypted GTR2 GMT v2.10 meshes and stock shaders.
- **Surface physics mapping**: Maps AC surfaces to GTR2 `specialfx.tec` prefixes and writes road/kerb grip values to `.gdb`.
- **Performance optimizations**: Added `track_shadows`, 250 m spatial mesh splitting, LOD culling, and tighter rearview clipping.
- **Config file (`ac2gtr2.ini`)**: Added `[track]` settings for texture scaling, road smoothing, mesh coarsening, unused GMT stripping, and shadows.
- **Upstream AC fixes**: Included MultiMap alpha/tint baking, DDS validation/re-encoding, skinned KN5 parsing, road smoothing, and 31-char instance name capping.
- **Track brightness (`track_brightness`)**: Added `[track] track_brightness` (`0.0`–`1.0`, default `0.5`) to brighten daytime AC track lighting, materials, and diffuse textures.

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
| `tools\rfactortools\` | **rFactorTools** (`masunpack.exe`, `rfactorcrypt.exe`, and runtime libraries) for unpacking rFactor 1 / Stock Car Extreme `.mas` archives and decrypting Stock Car Extreme assets. |
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
| **rFactorTools** (`masunpack`, `rfactorcrypt`) | `tools\rfactortools\` | **GNU GPL v3** — Ingo Ruhnke (https://github.com/Grumbel/rfactortools). Full license text included in `tools\rfactortools\COPYING.txt`. |

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
