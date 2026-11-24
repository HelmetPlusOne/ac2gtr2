# AC to GTR2

Converts an Assetto Corsa track into a GTR2 location. The output contains loose
unencrypted GTR2 GMT v2.10 meshes and DX9 DDS textures, GTR2-compatible `.trk`,
`.gdb`, `.grd`, `.AIW`, and `.cam` files, and composed GTR2 menu/loading images.

Converted content contains the source track's models and textures. Do not
redistribute it unless you have permission from the original author.

## Requirements

- Windows
- Assetto Corsa and GTR2 installed
- Python 3.10 or newer and NumPy (`python -m pip install numpy`)
- Blender 3.6 LTS with the `io_rf2_gmt_release` exporter add-on
- `texconv.exe` (by default taken from an installed rFactor 2; otherwise pass
  its path with `--texconv`)

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
- `--texture-scale`, `--texture-scale-min-size`, `--road-smoothness`,
  `--mesh-coarsening`, `--strip-unused-gmt-data`, and `--track-shadows`
  (`compact` / `off` / `full`) override `ac2gtr2.ini`.

Check an installed result with:

```text
python verify_install.py "<GTR2>\GameData\Locations\<track>" out\<track>
```

The verifier checks GTR2 GMT v2.10 headers, material texture references, DX9
DDS dimensions and byte sizes, hidden meshes, alpha-test thresholds and ColorKey
bits, upright normals, draw ranges, shadows, and the 31-character `Instance=`
name limit. The final compatibility check is loading and driving the track in
GTR2.
