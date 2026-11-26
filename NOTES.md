# GTR2 compatibility measurements

Measured directly against the installed GTR2 copy at
`D:\Games\GTR2\GTR2_HQ_PnG3` (`GFX.GTR`, 1,729 stock GMTs across `Monza.gtr`
and `Barcelona.gtr`, 100 `.trk` files, `specialfx.tec`, and `shared\`).

## Unencrypted assets

GTR2 does not encrypt `.gmt` or `.dds` files:
- `shared\DAY.GMT` begins directly with the GMT v2.10 header (`mat_off`, `0x20`,
  `GMOTOR_GMT_2.10\0`).
- `shared\DRYINGLINE.dds` begins directly with `DDS `.
- `.gtr` archives (`Monza.gtr`, `GFX.GTR`) are unencrypted `GMOTORMAS10`
  containers with 256-byte directory entries and zlib-compressed payloads.

Loose `.gmt` and `.dds` files are loaded natively via `SearchPath=` in `.trk`
(`GAMEDATA\LOCATIONS\<venue>\gmt` and `GAMEDATA\LOCATIONS\<venue>\maps`).

## GMT v2.10 binary format

Blender's `io_rf2_gmt_release` (24.3.9) exports rFactor 2 GMT v2.52
(`GMOTOR_GMT_2.52\0` at `0x1BC`, 888-byte header, 156-byte mesh descriptors,
32-byte `FaceData` with global vertex indices, interleaved 96-byte `TVertex2`).

GTR2 requires GMT v2.10 (`GMOTOR_GMT_2.10\0` = `c8 cf d2 d8 ce d8 e6 c3 d6 de d8
be bb a7 bf 90`), verified across all 765 GMTs in `Monza.gtr` and 964 GMTs in
`Barcelona.gtr` with 0 mismatches:

- **Header**: 532 bytes (`0x214`, `MeshOffset = 0x210` relative to byte 4):
  - `0x000`: `mat_off - 4` (`uint32`), `0x004`: `gmt_flags = 0x20` (`uint32`)
  - `0x008`: `GMOTOR_GMT_2.10\0` (16 bytes)
  - `0x018`: `radius` (`float32`), `0x01C`: 8 bounding-box corners (`24 float32`)
  - `0x0DC`: `origin` (`3 float32`), `0x0E8`: `pivot` (`3 float32`)
  - `0x0F4`: `lod_in, lod_out` (`2 float32`)
  - `0x16C`: `nmesh, mesh_off-4, nface, face_off-4, nnorm, norm_off-4, nbones, first_vert_off-4` (`8 uint32`)
  - `0x194`: object name (64-byte ASCII)
- **Mesh descriptors**: 108 bytes (`0x6C`) each:
  - `+0x00`: `m_flags` (`0x20004011` plain, `0x20004015` with tangents)
  - `+0x04`: `mat_index`, `+0x08`: `vcount`, `+0x0C`: `voff - 4`
  - `+0x14`: `uvoff - 4`, `+0x1C`: `tanoff - 4` (when `m_flags & 0x04`)
  - `+0x24`: `tcount`, `+0x28`: `trioff - 4`, `+0x5C`: `0xFFFFFFFF`
- **FaceData**: 36 bytes (`<4H 2I 5f`): `v0, v1, v2, flg, mesh_i, ni, plane_d, cx, cy, cz, radius`,
  where `v0, v1, v2` are **mesh-local** vertex indices into `mesh[mesh_i]` and
  `plane_d = dot(normal, p0)`. Verified 480/480 multi-mesh faces in `Monza.gtr`.
- **NormalData**: 16 bytes (`<3f I`): `nx, ny, nz, sg`.
- **Per-mesh vertex streams**: 32-byte `TVertex`, 32-byte `TexturesUV`, optional
  24-byte `UVTangents`, `uint16` triangle indices.
- **Materials**: 144-byte material header, 3 × 264-byte shader level blocks
  (`L0`/`L1`/`L2`), 32-byte pad, `uint32 ntex`, `ntex × 172`-byte textures.

## Stock GTR2 shaders (`GFX.GTR`)

Inspected directly from `GFX.GTR` (`BUMPMAP.GFX`, `DIFFUSE.GFX`, `SPECULAR.GFX`):
- `L2DiffuseT0` (1 stage: `sTex0`) — supports ColorKey `0x80` alpha-testing natively
  (verified on 375 stock materials).
- `L2BumpT0xT1` (3 stages: `0=sTex0`, `1=sTex1`, `2=sBump`) — stock DX9 multiply
  shader with bump mapping, used to map rF2's `L2BUMPSPECMAPT0XT1`.
- `L2BumpSpecMapT0` (5 stages: `0=sBase`, `1=sSpec`, `2=sBump`, `3=MATH`, `4=MATH`) —
  requires two `MATH` pseudo-texture entries (`tex_type=15`) at stages 3 and 4.

## Surfaces (`specialfx.tec`) and `.gdb`

GTR2 uses the global `GameData\Locations\specialfx.tec` with per-track
`[TRACKVARS]` overrides in `.gdb` (`RoadDryGrip`, `RoadWetGrip`, `RumbleDryGrip`,
`RumbleWetGrip`). Material prefixes in `specialfx.tec`:
- Drivable roads/concrete: `road`, `conc` (unlike AMS1's `cem`, which maps to
  cement walls in GTR2).
- Kerbs: `rmbl`; grass: `gras`; sand/gravel: `grvl` (matches `grv`); dirt: `dirt`.
- Barriers: `cwal` (cement wall), `twall` (tire wall), `grdr` (guardrail).

## Scene (`.trk`), skybox, AIW, cameras, and UI images

- Every stock GTR2 track declares `SearchPath=GAMEDATA\LOCATIONS\SHARED`,
  `MASFile=Skies_central.gtr`, `TeamMaps2.gtr`, `Misc.gtr`, and `Instance=skyboxi`
  containing `day`, `night`, `sunrise`, `sunset`, `sun`, `moon`, `dayR`, `nightR`,
  `sunriseR`, `sunsetR`.
- Lighting and fog are driven from `.gdb` (`DayAmbientColour`,
  `DayDirectionalColour`, `DayFogColour`, `Latitude`, `NorthDir`, `RaceDate`)
  rather than static `Light=FDirect01` in `.trk`.
- Timing gates (`xfinish`, `xsector1`, `xsector2`) use `Render=False Change=False`,
  `CollTarget=True HATTarget=False`, `Response=VEHICLE,TIMING`, `Response=VEHICLE,SECTOR`;
  pit gates (`xpitin`, `xpitout`) use `Response=VEHICLE,PITSTOP`.
- Menu/loading image naming across all stock tracks for `<layout>.gdb`:
  - `<layout[:4]>_LOD.jpg` (1024×768 JPEG loading screen)
  - `<layout[:4]>_LOD_2.jpg` (1024×45 JPEG loading bar banner)
  - `<layout[:3]>Thmb.tga` (666×400 32-bit bottom-up RGBA TGA)
  - `<layout>Track.bmp` (145×83 32-bit bottom-up uncompressed RGBA TGA saved as `.bmp`)
- `Instance=` names are capped at 31 characters to avoid gMotor 2.0's 32-byte
  `TInstance` name buffer overflow on track exit.

## GTR2 projected shadows and spatial culling performance

Measured across stock GTR2 tracks (`Monza`, `Barcelona`, `Spa`, `Bahrain`,
`Magione`):
- Stock tracks use small spatial chunks (Monza median radius 30 m, median
  `LOD_Out` 550 m; Barcelona median radius 29 m, median `LOD_Out` 500 m),
  `ShadowReceiver=True` only on road/ground (~9–25% of instances), and small
  `ShadowCaster=` counts (8–45, mostly `16×16` or `32×32`, with 0–1 `Texture`
  casters).
- Because GTR2 renders a separate projected shadow texture per active
  `ShadowCaster=` and runs an extra `L2ShadowProject` pass per overlapping
  `(caster, receiver)` pair, emitting `ShadowCaster=(Static, ..., 128, 128)`
  on ~90 large AC meshes + `ShadowReceiver=True` on ~96% of instances creates
  severe CPU/draw-pass bottlenecks even with aggressive `mesh_coarsening`.
- `blender_stage.py` now splits wide AC meshes (horizontal span > 375 m,
  >= 200 tris) into 250 m horizontal spatial tiles so frustum culling,
  `LODOut`, and `MaxShadowRange` work per tile.
- `finalize_track.py` defaults to `track_shadows=compact`: `ShadowReceiver=True`
  on road/ground only (preserving car shadows), `ShadowCaster=(Static, Solid,
  32/64)` only on compact opaque structures (`<= 60 m` radius, `<= 8000` tris),
  no foliage `Texture` casters, `MaxShadowRange=(200.0)`, `rearview ClipPlanes`
  set to `(0.50, 400.00)`, and stock-like `LODOut` culling for grass (250 m),
  foliage cutouts (800 m), and small props (600 m).

## Track brightness boost (`track_brightness`)

- `[track] track_brightness` (and `--track-brightness`) accepts `[0.0, 1.0]`,
  defaulting to `0.5` (`0.0` disabled / byte-for-byte unchanged, `0.5` subtle
  daylight lift, `1.0` bright sunny look).
- When `> 0.0`, three GTR2 lighting levers are raised smoothly together during
  `finalize_track.py` (so it also works with `--skip-geometry`), scaled by
  `t = 0.5 * track_brightness`:
  1. **GMT material colors** (`gmt_gtr2.py`): lifts `Diffuse` and `Ambient` RGB
     from AC's lower reflectance values halfway toward `1.0` at `1.0` (keeping
     alpha, specular, and emissive unchanged so night racing stays dark).
  2. **Scene `.gdb` lighting** (`finalize_track.py`): lifts daytime, sunrise,
     sunset, and rainy-day ambient lighting (`DayAmbientColour` up to `210` at
     `1.0`), leaving `NightAmbientColour` untouched.
  3. **Diffuse DDS textures** (`finalize_track.py`): brightens stage-0 diffuse
     `DXT1`/`DXT3`/`DXT5` textures directly on their RGB565 endpoints across all
     mipmaps (preserving DXT3/DXT5 alpha blocks, DXT1 opaque vs 1-bit alpha
     semantics, and excluding bump/specular/helper textures).

## rFactor 1 input tracks (`rf1_track.py`, `gmt_gtr2.py`)

- Input type (`ac` vs `rf1`) is auto-detected by `rf1_track.detect_track_type`
  (and inside `.zip` archives by `track_zip.track_folders`).
- `[track] track_shadows` (`compact` / `off` / `full`, overridable via
  `--track-shadows`) applies to rFactor 1 `.scn` -> `.trk` conversion (with
  `Dynamic, Texture` casters always stripped; `compact` caps `MaxShadowRange`
  to `200 m`, keeps `ShadowReceiver=True` only on rendered `HATTarget=True`
  surfaces, strips `Texture` casters, and keeps compact `Static, Solid` casters
  up to `60 m` / `8000` tris at `32`/`64`; `off` strips all track casters while
  preserving car shadows on `HATTarget=True` surfaces). All other AC-only
  conversion options (`texture_scale`, `road_smoothness`, `mesh_coarsening`,
  `strip_unused_gmt_data`, `track_brightness`) are ignored for rFactor 1 inputs.
- rFactor 1 `.mas` archives are unpacked using `masunpack.exe`
  (`D:\Mods\Automobilista\Tools\rfactortools-0.3.1.win32\masunpack.exe`,
  configurable via `--masunpack` / `AC2GTR2_MASUNPACK`).
- rFactor 1 GMT v2.00 binaries share GTR2 v2.10's vertex/UV/tangent/triangle
  and 3-level material/animated/BIK texture formats, differing in the header
  (no 16-byte magic at `0x08`, `counts` at `0x174`), 132-byte mesh descriptors
  (vs 108-byte), and 32-byte `FaceData` without `plane_d` (vs 36-byte).
- `gmt_gtr2.convert_rf1_to_gtr2()` converts v2.00 → v2.10 cleanly (reading
  each rF1 mesh's material index from offset `+0x38` of the 132-byte mesh
  descriptor), maps rF1-only shaders missing from GTR2's `GFX.GTR`
  (`L2USEVERTEXONLY` → `L2USEVERTEXPIXEL`, `L0/L1/L2CMAPADDT0*` →
  `L0/L1/L2CMAPT0*`, `*LERPT1*VERTEXALPHA` → stage-compatible `L*DIFFUSET0*`
  shaders), appends the two GTR2 `MATH` pseudo-texture stages required by
  `L2BUMPSPECMAPT0` (3 → 5) and `L2BUMPSPECT0` (2 → 4), remaps rF1-only `.tdf`
  surface prefixes (`rdax`/`rdta`/`rdcp`/`rged`/`stick`, `grxs`, `dstg`/`gtrp`,
  `twal`, `cem`/`wall`/`safer`) to GTR2 `specialfx.tec` prefixes, and resolves
  stem mismatches (`.tga`/`.bmp` → `.dds`).
- Multi-layout rF1 venues (e.g. `Mainz-Finthen` with `MFinthen_DTM` and
  `MFinthen_DTMwet`) convert all layouts into one GTR2 location when `--layout`
  is omitted, isolating colliding per-layout overrides into `gmt_<layout>/` and
  `maps_<layout>/`.
