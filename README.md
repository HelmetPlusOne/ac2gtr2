# AC to GTR2

Converts Assetto Corsa (AC) and rFactor 1 (rF1) tracks into GTR2 locations. The
output contains loose unencrypted GTR2 GMT v2.10 meshes and DX9 DDS textures,
GTR2-compatible `.trk`, `.gdb`, `.grd`, `.AIW`, and `.cam` files, and composed
GTR2 menu/loading images.

Converted content contains the source track's models and textures. Do not
redistribute it unless you have permission from the original author.

## Requirements

- Windows
- GTR2 installed
- Python 3.10 or newer and NumPy (`python -m pip install numpy`)
- `texconv.exe` (by default taken from an installed rFactor 2; otherwise pass
  its path with `--texconv`)
- For Assetto Corsa tracks: Assetto Corsa installed and Blender 3.6 LTS with
  the `io_rf2_gmt_release` exporter add-on
- For rFactor 1 tracks: `masunpack.exe` (auto-detected from
  `D:\Mods\Automobilista\Tools\rfactortools-0.3.1.win32\masunpack.exe` or
  passed via `--masunpack`; Blender and Assetto Corsa are not required)

Unlike Automobilista 1, GTR2 uses unencrypted `.gmt` and `.dds` assets, so no
encryption tool is required. The converter performs a full binary conversion
from the rFactor 2 GMT v2.52 output emitted by `io_rf2_gmt_release` into GTR2's
native GMT v2.10 format (`GMOTOR_GMT_2.10\0` at offset `0x08`, 532-byte header,
108-byte mesh descriptors, 36-byte `FaceData` with plane distance `D` and
mesh-local vertex indices, and stock `GFX.GTR` shaders such as `L2DIFFUSET0`,
`L2BUMPT0XT1`, and `L2BUMPSPECMAPT0` with `MATH` stages). Unrecognized GMT
formats are rejected explicitly.

Physical surfaces are prefixed to match GTR2's `GameData\Locations\specialfx.tec`
(`road`, `conc`, `rmbl`, `gras`, `grvl`, `dirt`, `cwal`, `twall`, `grdr`), and
dry/wet road and rumble grip values from AC's `surfaces.ini` are written into
the track `.gdb` file.

When the AC track includes a rasterized track map (`<layout>\map.png` or
`map.png`), UI outline (`ui\<layout>\outline.png` or `ui\outline.png`), or
preview (`ui\<layout>\preview.png` or `ui\preview.png`), the converter composes
GTR2's menu and loading images (`<layout[:4]>_LOD.jpg`,
`<layout[:4]>_LOD_2.jpg`, `<layout[:3]>Thmb.tga`, and `<layout>Track.bmp`).
Long layout identifiers are shortened deterministically across the `.gdb`,
`.trk`, `.AIW`, `.cam`, `.grd`, and menu image filenames. If both `<venue>.gtr`
and `<venue>MAP.gtr` are placed in `out\<track>`, the converter stages them and
registers them in the `.trk` file.

It writes plain working files under `out\<track>` and GTR2-ready files under
`out\<track>\gtr2\<track>`.

Run `python check_setup.py` to check dependencies. If a game installation is not
auto-detected, provide its path with `--gtr2` or `--ac`.

## Convert a track

```text
python build_track.py "<AC track folder>" --install auto
```

`--install auto` installs into `<GTR2>\GameData\Locations\<track>`. Without
`--install`, the staged GTR2-ready location remains under
`out\<track>\gtr2\<track>`.

For a track with multiple layouts, use `--layout <name>` (the part after
`models_`), for example:

```text
python build_track.py "<AC track folder>" --layout gp --install auto
```

On Windows, drag an AC track folder or its download ZIP onto `convert.bat` to
run a full conversion and install it automatically. For multi-layout tracks,
the launcher prompts for a layout. Additional `build_track.py` options can be
passed after the track path, for example:

```text
convert.bat "<AC track folder>" --gtr2 "D:\Games\GTR2\GTR2_HQ_PnG3"
```

Useful options:

- `--hour HH:MM` sets the session start time.
- `--skip-geometry` reuses meshes from the previous build.
- `--jobs <n>` controls parallel Blender and texture jobs.
- `--gtr2 "<GTR2 folder>"` and `--ac "<Assetto Corsa folder>"` override
  automatic discovery.
- `--texconv "<path to texconv.exe>"` selects the texture conversion utility.
- `--masunpack "<path to masunpack.exe>"` selects the MAS unpacker used for
  rFactor 1 tracks (auto-detected from `D:\Mods\Automobilista\Tools\rfactortools-0.3.1.win32\masunpack.exe`).
- `--track-shadows` (`compact` / `off` / `full`) overrides `ac2gtr2.ini`
  `[track] track_shadows` for both Assetto Corsa and rFactor 1 tracks.
- `--texture-scale`, `--texture-scale-min-size`, `--road-smoothness`,
  `--mesh-coarsening`, `--strip-unused-gmt-data`, and `--track-brightness`
  (`0.0` disabled, `0.5` default subtle daylight lift, `1.0` bright sunny look)
  override `ac2gtr2.ini` (Assetto Corsa tracks only; automatically ignored for
  rFactor 1 inputs).

### rFactor 1 track inputs

Input track type (Assetto Corsa `.kn5` vs rFactor 1 `.scn` + `.gdb` + `.mas`)
is detected automatically for both folders and `.zip` archives:

```text
python build_track.py "<rF1 track folder or zip>" --install auto
```

Key details for rFactor 1 conversions:

- **No Blender or Assetto Corsa required**: `.mas` archives are unpacked with
  `masunpack.exe` and rF1 GMT v2.00 meshes are converted directly to GTR2 GMT
  v2.10 (`gmt_gtr2.py`), preserving per-submesh material indices, animated and
  `.bik` textures, remapping rF1-only shaders (`L2USEVERTEXONLY`,
  `L*CMAPADDT0*`, `*LERPT1*VERTEXALPHA`) to stock GTR2 `GFX.GTR` shaders,
  inserting GTR2's required `MATH` stages on `L2BUMPSPECMAPT0` / `L2BUMPSPECT0`,
  and remapping rF1 `.tdf` surface prefixes to GTR2 `specialfx.tec`.
- **Scene, AIW, and texture fixes**: `.scn` -> `.trk` replaces rF1 skyboxes
  with GTR2's `SHARED` skybox, caps `Instance=` names at 31 characters,
  sanitizes whitespace in texture filenames, re-encodes non-DX9 DDS textures at
  full resolution, clamps `.AIW` `garagespots` to GTR2's valid `1..3` range,
  and preserves all layouts when `--layout` is omitted (assigning unique layout
  IDs and `VenueName` entries so every layout is selectable in GTR2).
- **Options**: `[track] track_shadows` (`compact` / `off` / `full`, or
  `--track-shadows`) applies to rF1 `.scn` -> `.trk` conversion (`Dynamic,
  Texture` casters are always stripped). All other AC-specific conversion
  options (`texture_scale`, `road_smoothness`, `mesh_coarsening`,
  `strip_unused_gmt_data`, `track_brightness`) are ignored for rF1 inputs.

Check an installed result with:

```text
python verify_install.py "<GTR2>\GameData\Locations\<track>" out\<track>
```

The verifier checks GTR2 GMT v2.10 headers, material texture references, DX9
DDS dimensions and byte sizes, hidden meshes, alpha-test thresholds and ColorKey
bits, upright normals, draw ranges, shadows, and the 31-character `Instance=`
name limit. The final compatibility check is loading and driving the track in
GTR2.
