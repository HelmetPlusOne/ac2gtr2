"""Assemble a GTR2 track from the converted parts.

GTR2 uses unencrypted GMT v2.10 and DDS files inside a location folder:

  Locations/<venue>/
      <layout>.trk  .gdb  .AIW  .cam  .grd
      <layout[:4]>_LOD.jpg  <layout[:4]>_LOD_2.jpg
      <layout[:3]>Thmb.tga  <layout>Track.bmp
      gmt/*.gmt
      maps/*.dds

The scene (.trk) file is rebuilt from the GMT files that were actually exported
rather than from the pre-export plan.
"""
import os
import json
import shutil
import argparse
import hashlib
import re
import struct
import subprocess
import tempfile

TRK_HEADER = """CUBEASF
// {venue}, converted from Assetto Corsa

SearchPath=GAMEDATA\\LOCATIONS\\{venue}
SearchPath=GAMEDATA\\LOCATIONS\\{venue}\\gmt
SearchPath=GAMEDATA\\LOCATIONS\\{venue}\\maps
SearchPath=GAMEDATA\\LOCATIONS\\SHARED

{mas_files}
MASFile=Skies_central.gtr
MASFile=TeamMaps2.gtr
MASFile=Misc.gtr

View=mainview
{{
  Clear=True
  Color=(0, 0, 0)
  Size=(1.0, 1.0) Center=(0.5, 0.5)
  FOV=(77.75, 62.50)
  ClipPlanes=(0.50, 15000.00)
  View=rearview
  {{
    Clear=True
    Color=(149, 218, 254)
    Size=(512.0, 256.0) Center=(256.0, 128.0)
    FOV=(77.5, 62.5)
    ClipPlanes=(0.50, 400.00)
  }}
}}

GroupMethod=Dynamic
MaxShadowRange=({max_shadow_range:.1f})

{skybox}
"""

GDB = """{layout}
{{
  AI Vehicle Filter = {venue}

  TestDayDay = Wednesday
  TestDayStart = {hour}
  Practice1Day = Friday
  Practice1Start = {hour}
  Practice2Day = Friday
  Practice2Start = {hour}
  Qualify1Day = Saturday
  Qualify1Start = {hour}
  Qualify2Day = Saturday
  Qualify2Start = {hour}
  WarmupDay = Sunday
  WarmupStart = {hour}
  RaceDay = Sunday
  RaceStart = {hour}
  RaceLaps = 10
  RaceTime = 60
  RaceTimeScaled = 60

  Attrition = 30

  TrackName = {venue}
  GrandPrixName = {venue}
  EventName = {venue}
  VenueName = {venue}
  Location = converted from Assetto Corsa
  Length = {mi:.3f} mi/{km:.3f} km
  Track Record = N/A

  SettingsFolder = {venue}
  SettingsCopy = {venue}.svm
  SettingsAI = {venue}.svm
  Qualify Laptime = {lap_sec:.3f}
  Race Laptime = {race_sec:.3f}

  RoadDryGrip = {road_dry:.3f}
  AIDryGrip = {ai_dry:.3f}
  RoadWetGrip = {road_wet:.3f}
  AIWetGrip = {ai_wet:.3f}
  RumbleDryGrip = {rumble_dry:.3f}
  RumbleWetGrip = {rumble_wet:.3f}
  PlayerTireWear = 1.0
  FrontTireHeatMult = 1.00
  RearTireHeatMult = 0.90

  // Locality
  Region = Central
  Average rain = 0.2
  RainLightScale = 0.85
  RainFogScale = 0.95
  RainFogColourScale = 0.15
  RainCloudScale = 1.0

  // Scene Lighting
  ShadowMinSunAngle = 15.0

  SkyBlendSunAngles = (-20.5, -1.0, 11.5, 25.5)

  SunriseAmbientColour = ({sunrise_amb[0]},{sunrise_amb[1]},{sunrise_amb[2]})
  SunriseDirectionalColour = (229,204,178)
  SunriseFogColour = (30,35,55)
  SunriseFogIn = 0.0
  SunriseFogOut = 2150.0
  RainSunriseAmbientColour = (85,85,85)
  RainSunriseDirectionalColour = (45,45,45)
  RainSunriseFogColour = (43,53,55)
  RainSunriseFogIn = -10.0
  RainSunriseFogOut = 650.0

  DayAmbientColour = ({day_amb[0]},{day_amb[1]},{day_amb[2]})
  DayDirectionalColour = ({day_sun[0]},{day_sun[1]},{day_sun[2]})
  DayFogColour = (185,211,243)
  DayFogIn = 250.0
  DayFogOut = 4500.0
  RainDayAmbientColour = ({rain_day_amb[0]},{rain_day_amb[1]},{rain_day_amb[2]})
  RainDayDirectionalColour = ({rain_day_sun[0]},{rain_day_sun[1]},{rain_day_sun[2]})
  RainDayFogColour = (77,80,81)
  RainDayFogIn = -25.0
  RainDayFogOut = 1050.0

  SunsetAmbientColour = ({sunset_amb[0]},{sunset_amb[1]},{sunset_amb[2]})
  SunsetDirectionalColour = (220,190,150)
  SunsetFogColour = (60,63,75)
  SunsetFogIn = 100.0
  SunsetFogOut = 1700.0
  RainSunsetAmbientColour = (72,70,70)
  RainSunsetDirectionalColour = (35,32,32)
  RainSunsetFogColour = (37,41,35)
  RainSunsetFogIn = -10.0
  RainSunsetFogOut = 650.0

  NightAmbientColour = (28,30,34)
  NightDirectionalColour = (15,16,17)
  NightFogColour = (4,7,10)
  NightFogIn = 220.0
  NightFogOut = 1900.0
  RainNightAmbientColour = (25,25,25)
  RainNightDirectionalColour = (15,15,15)
  RainNightFogColour = (23,25,24)
  RainNightFogIn = -10.0
  RainNightFogOut = 750.0

  // Sun position
  Latitude = 25
  NorthDir = 270
  RaceDate = July 1

  HorizonRadius = 1500.0
  HorizonMaxHeight = 50.0
  HorizonMinHeight = 30.0

  Max Vehicles = 36
}}
"""

GRD_TEMPLATE = """PreRacePresentation  // Pre-race Presentation script
{
  CinematicEvent
  {
    Type              = GRIDWALK
    Probability       = 1.00
    Time              = +0.0
    Iterations        = 1
    Wait              = YES
  }

  CinematicEvent
  {
    Type              = SCRIPT_END
    Probability       = 1.00
    Time              = +1.5
    Iterations        = 1
  }

}   // End of PreRacePresentation
"""

MAX_LAYOUT_ID_LENGTH = 16
# gMotor 2.0's TInstance struct holds a 32-byte name buffer right before a
# linked-list pointer. Instance= names > 31 chars overflow into that pointer
# and crash when exiting a track.
MAX_INSTANCE_NAME_LENGTH = 31


def instance_identifier(name, used=None):
    """Keep TRK Instance= names within gMotor 2.0's 31-char limit (+ NUL = 32 bytes)."""
    if len(name) <= MAX_INSTANCE_NAME_LENGTH and (
        used is None or name.casefold() not in used
    ):
        if used is not None:
            used.add(name.casefold())
        return name
    digest = hashlib.sha1(name.casefold().encode("utf-8")).hexdigest()[:8]
    prefix = name[:MAX_INSTANCE_NAME_LENGTH - 9].rstrip("_") or "inst"
    cand = "%s_%s" % (prefix, digest)
    if used is not None:
        counter = 2
        while cand.casefold() in used:
            tag = "_%d_%s" % (counter, digest[:6])
            base = name[:MAX_INSTANCE_NAME_LENGTH - len(tag)].rstrip("_") or "inst"
            cand = base + tag
            counter += 1
        used.add(cand.casefold())
    return cand


def layout_identifier(venue):
    """Keep generated GTR2 filenames concise (<= 16-char stem, <= 20-char .trk)."""
    if len(venue) <= MAX_LAYOUT_ID_LENGTH:
        return venue
    prefix = re.sub(r"[^A-Za-z0-9]", "", venue)[:7].lower() or "track"
    suffix = hashlib.sha1(venue.casefold().encode("utf-8")).hexdigest()[:8]
    return "%s_%s" % (prefix, suffix)


def install_location(stage, locations, venue):
    dest = os.path.join(locations, venue)
    os.makedirs(locations, exist_ok=True)
    if os.path.isdir(dest) and not os.listdir(dest):
        shutil.copytree(stage, dest, dirs_exist_ok=True)
        return dest

    swap_root = tempfile.mkdtemp(prefix=".ac2gtr2-install-", dir=locations)
    candidate = os.path.join(swap_root, venue)
    previous = os.path.join(swap_root, "previous")
    previous_saved = False
    try:
        shutil.copytree(stage, candidate)
        if os.path.exists(dest):
            os.replace(dest, previous)
            previous_saved = True
        try:
            os.replace(candidate, dest)
        except OSError as install_error:
            if previous_saved:
                try:
                    os.replace(previous, dest)
                    previous_saved = False
                except OSError as restore_error:
                    raise RuntimeError(
                        "installation failed and the previous location could "
                        "not be restored; its backup is at %s: %s"
                        % (previous, restore_error)) from install_error
            raise
        if previous_saved:
            try:
                shutil.rmtree(previous)
                previous_saved = False
            except OSError as exc:
                print("installed, but could not remove previous location "
                      "backup %s: %s" % (previous, exc))
        return dest
    finally:
        if not previous_saved:
            try:
                shutil.rmtree(swap_root)
            except OSError as exc:
                print("could not clean temporary install folder %s: %s"
                      % (swap_root, exc))


DRIVABLE_PREFIXES = ("road", "conc", "rmbl", "gras", "grvl", "dirt")
COMPACT_SHADOW_MAX_RADIUS = 60.0
COMPACT_SHADOW_MAX_TRIS = 8000
FULL_SHADOW_MAX_RADIUS = 150.0


def is_shadow_receiver(meta, row, mode="compact"):
    """Return True when this GTR2 instance should receive projected shadows.

    GTR2 projects every active ShadowCaster texture onto every overlapping
    ShadowReceiver mesh in a separate pass. Stock GTR2 tracks set
    ShadowReceiver=True only on road/ground surfaces (~10-25% of instances)
    rather than on trees, fences, and props, keeping car shadows intact while
    preventing N_casters * N_receivers pass explosions.
    """
    if not row.get("render", True) or row.get("timing"):
        return False
    if meta is None:
        return bool(row.get("physical"))
    if meta.get("decal") or meta.get("backdrop"):
        return False
    if meta.get("alpha_test") or meta.get("needs_blend"):
        return False
    if meta.get("shader") in ("ksTree", "ksGrass"):
        return False
    if mode == "full":
        return True
    mat_prefix = str(meta.get("rf2_material", "")).split("_", 1)[0].lower()
    if row.get("physical") and mat_prefix in DRIVABLE_PREFIXES:
        return True
    if meta.get("ground_like") or meta.get("track_surface"):
        return True
    return float(meta.get("upward", 0.0)) >= 0.5


def shadow_caster(meta, cutouts, mode="compact", radius=0.0, tris=0):
    """Return ``(kind, map_size)`` for GTR2 ShadowCaster, or None.

    Modes:
      - ``off``: no track ShadowCaster entries (cars still cast shadows onto
        ShadowReceiver=True ground/road surfaces).
      - ``compact`` (default): only compact opaque structures (radius <= 60 m,
        <= 8000 tris) at 32x32 or 64x64; skips foliage/alpha-tested Texture
        shadow maps to match stock GTR2 shadow budgets.
      - ``full``: opaque and alpha-tested cutouts up to 150 m radius at 64x64.
    """
    if mode == "off" or meta is None:
        return None
    if not meta.get("render", True) or not meta.get("cast_shadows"):
        return None
    if meta.get("decal") or meta.get("backdrop"):
        return None
    if meta.get("needs_blend") and not meta.get("alpha_test"):
        return None
    if float(meta.get("upward", 0.0)) >= 0.9:
        return None

    r = float(radius or meta.get("radius", 0.0) or 0.0)
    t = int(tris or meta.get("tris", 0) or 0)

    if mode == "compact":
        if meta.get("alpha_test") or meta.get("shader") in ("ksTree", "ksGrass"):
            return None
        if r > COMPACT_SHADOW_MAX_RADIUS or t > COMPACT_SHADOW_MAX_TRIS:
            return None
        size = 32 if r <= 25.0 else 64
        return ("Solid", size)

    # mode == "full"
    if r > FULL_SHADOW_MAX_RADIUS:
        return None
    if meta.get("alpha_test"):
        return ("Texture", 64) if meta.get("rf2_material") in cutouts else None
    return ("Solid", 64)


def gtr2_lod_range(meta, row):
    """Return ``(lod_in, lod_out)`` for a .trk instance to aid GTR2 distance culling.

    Preserves AC's explicit LODIn/LODOut when set; otherwise applies stock-like
    distance culling to grass, foliage cutouts, and small non-ground props
    while keeping roads, terrain, and backdrops visible to the horizon.
    """
    lin = float(row.get("lod_in") or 0.0)
    lout = float(row.get("lod_out") or 0.0)
    if lout > 0.0:
        return lin, lout
    if meta is None or row.get("physical") or meta.get("backdrop"):
        return 0.0, 0.0
    if meta.get("ground_like") or meta.get("track_surface"):
        return 0.0, 0.0
    shader = meta.get("shader") or ""
    r = float(row.get("radius") or meta.get("radius") or 0.0)
    if shader == "ksGrass":
        return 0.0, 250.0
    if (shader == "ksTree" or meta.get("alpha_test")) and 0.0 < r <= 300.0:
        return 0.0, 800.0
    if 0.0 < r <= 30.0:
        return 0.0, 600.0
    return 0.0, 0.0


def _lerp_rgb(base, target, t):
    return tuple(
        max(0, min(255, int(round(a + (b - a) * t))))
        for a, b in zip(base, target)
    )


def gdb_lighting_colors(ambient=165, sun=255, brightness=0.0):
    """Compute GTR2 .gdb scene lighting RGB tuples for ``brightness`` in [0, 1].

    At ``brightness == 0.0`` returns exact baseline colors. Higher values lift
    daytime, sunrise/sunset, and rainy-day ambient light while keeping night
    lighting dark.
    """
    b = max(0.0, min(1.0, float(brightness or 0.0)))
    amb = max(0, min(255, int(ambient)))
    sn = max(0, min(255, int(sun)))
    return {
        "day_amb": _lerp_rgb((amb, amb, amb), (255, 255, 255), b),
        "day_sun": _lerp_rgb((sn, sn, sn), (255, 255, 255), b),
        "sunrise_amb": _lerp_rgb((146, 138, 125), (225, 215, 198), b),
        "sunset_amb": _lerp_rgb((136, 121, 105), (220, 200, 172), b),
        "rain_day_amb": _lerp_rgb((105, 105, 105), (190, 190, 190), b),
        "rain_day_sun": _lerp_rgb((45, 45, 45), (135, 135, 135), b),
    }


def _rgb565_brightness_luts(brightness):
    """Build monotonic RGB565 channel lookup tables (5-bit R/B, 6-bit G)."""
    import numpy as np

    b = max(0.0, min(1.0, float(brightness)))
    gain = 1.0 + 0.80 * b
    gamma = 1.0 - 0.35 * b

    def _curve(max_val):
        x = np.arange(max_val + 1, dtype=np.float64) / float(max_val)
        y = np.power(np.clip(x * gain, 0.0, 1.0), gamma)
        return np.clip(np.rint(y * max_val), 0, max_val).astype(np.uint16)

    return _curve(31), _curve(63)


def brighten_dds_rgb565(path, brightness, luts=None):
    """Brighten a DX9 DXT1/DXT3/DXT5 DDS file in-place via its RGB565 endpoints.

    Preserves the 128-byte DDS header, mipmap chain, DXT3/DXT5 alpha blocks,
    and DXT1 opaque vs 1-bit punch-through alpha semantics bit-for-bit.
    Returns True when the file was modified.
    """
    b = float(brightness or 0.0)
    if b <= 0.0:
        return False
    import numpy as np

    with open(path, "rb") as f:
        raw = bytearray(f.read())
    if len(raw) < 128 or raw[:4] != b"DDS ":
        return False
    fourcc = bytes(raw[84:88])
    if fourcc == b"DXT1":
        block_size, color_off = 8, 0
    elif fourcc in (b"DXT3", b"DXT5"):
        block_size, color_off = 16, 8
    else:
        return False

    payload_len = len(raw) - 128
    nblocks = payload_len // block_size
    if nblocks <= 0:
        return False

    lut5, lut6 = luts if luts is not None else _rgb565_brightness_luts(b)
    buf = np.frombuffer(raw, dtype=np.uint8, offset=128, count=nblocks * block_size)
    blocks = buf.reshape(nblocks, block_size)
    cbytes = blocks[:, color_off:color_off + 4].view("<u2").reshape(nblocks, 2)
    c0 = cbytes[:, 0].copy()
    c1 = cbytes[:, 1].copy()

    def _map_rgb565(c):
        r = lut5[(c >> 11) & 0x1F]
        g = lut6[(c >> 5) & 0x3F]
        bl = lut5[c & 0x1F]
        return ((r << 11) | (g << 5) | bl).astype(np.uint16)

    nc0 = _map_rgb565(c0)
    nc1 = _map_rgb565(c1)

    if fourcc == b"DXT1":
        idx = blocks[:, 4:8].view("<u4").reshape(nblocks)
        was_opaque = c0 > c1

        # Opaque block whose endpoints crossed (nc0 < nc1): swap + flip 2-bit indices.
        swap_op = was_opaque & (nc0 < nc1)
        if np.any(swap_op):
            tmp = nc0[swap_op].copy()
            nc0[swap_op] = nc1[swap_op]
            nc1[swap_op] = tmp
            idx[swap_op] ^= np.uint32(0x55555555)

        # Opaque block whose endpoints collapsed (nc0 == nc1): keep nc0 > nc1
        # so index 3 never flips to 1-bit transparent black.
        eq_op = was_opaque & (nc0 == nc1)
        if np.any(eq_op):
            gt0 = eq_op & (nc0 > 0)
            nc1[gt0] = nc0[gt0] - np.uint16(1)
            eq0 = eq_op & (nc0 == 0)
            nc0[eq0] = np.uint16(1)

        # 1-bit transparent block (c0 <= c1) whose endpoints crossed (nc0 > nc1):
        # swap endpoints and swap index codes 00 <-> 01 while preserving 10 & 11.
        swap_tr = (~was_opaque) & (nc0 > nc1)
        if np.any(swap_tr):
            tmp = nc0[swap_tr].copy()
            nc0[swap_tr] = nc1[swap_tr]
            nc1[swap_tr] = tmp
            t_idx = idx[swap_tr]
            low_mask = ((~t_idx) >> np.uint32(1)) & np.uint32(0x55555555)
            idx[swap_tr] = t_idx ^ low_mask

    cbytes[:, 0] = nc0
    cbytes[:, 1] = nc1

    with open(path, "wb") as f:
        f.write(raw)
    return True


def brighten_staged_diffuse_textures(stage, brightness):
    """Brighten all diffuse stage-0 DDS files in ``stage/maps`` for GTR2."""
    b = float(brightness or 0.0)
    if b <= 0.0:
        return 0
    import gmt_gtr2

    gmt_dir = os.path.join(stage, "gmt")
    map_dir = os.path.join(stage, "maps")
    if not os.path.isdir(map_dir):
        return 0
    diffuse = gmt_gtr2.collect_diffuse_textures(gmt_dir)
    if not diffuse:
        return 0
    luts = _rgb565_brightness_luts(b)
    changed = 0
    for fname in sorted(os.listdir(map_dir)):
        if fname.lower() in diffuse and fname.lower().endswith(".dds"):
            if brighten_dds_rgb565(os.path.join(map_dir, fname), b, luts=luts):
                changed += 1
    return changed


# GTR2 track UI image dimensions (measured from stock Monza, Barcelona, Magione):
#   <layout[:4]>_LOD.jpg    : 1024x768 JPEG loading screen
#   <layout[:4]>_LOD_2.jpg  : 1024x45  JPEG loading footer/banner bar
#   <layout[:3]>Thmb.tga    : 666x400  32-bit bottom-up uncompressed RGBA TGA
#   <layout>Track.bmp       : 145x83   32-bit bottom-up uncompressed RGBA TGA named .bmp
LOD_SIZE = (1024, 768)
LOD_FIT = (840, 600)
LOD2_SIZE = (1024, 45)
THMB_SIZE = (666, 400)
THMB_FIT = (580, 340)
TRACK_BMP_SIZE = (145, 83)
TRACK_BMP_FIT = (129, 69)
MAX_DECODE_DIM = 2048


def _png_dimensions(path):
    try:
        with open(path, "rb") as f:
            head = f.read(24)
    except OSError:
        return None
    if len(head) >= 24 and head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
        return struct.unpack_from(">II", head, 16)
    return None


def _run_texconv(command, description):
    result = subprocess.run(
        command, capture_output=True, text=True, errors="replace"
    )
    if result.returncode:
        details = (result.stdout or "") + (result.stderr or "")
        raise RuntimeError("texconv failed %s:\n%s" % (description, details[-2000:]))


def _read_tga_bgra(path):
    import numpy as np

    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 18:
        raise RuntimeError("truncated TGA image: %s" % path)
    id_len = data[0]
    img_type = data[2]
    width, height = struct.unpack_from("<HH", data, 12)
    bpp = data[16]
    descriptor = data[17]
    if img_type != 2 or bpp not in (24, 32) or not width or not height:
        raise RuntimeError(
            "unsupported TGA format in %s (type=%d, bpp=%d, %dx%d)"
            % (path, img_type, bpp, width, height)
        )
    channels = bpp // 8
    start = 18 + id_len
    need = width * height * channels
    if len(data) < start + need:
        raise RuntimeError("truncated TGA pixel data: %s" % path)
    pixels = np.frombuffer(
        data, dtype=np.uint8, count=need, offset=start
    ).reshape(height, width, channels)
    if (descriptor & 0x20) == 0:
        pixels = pixels[::-1]
    if channels == 3:
        bgra = np.empty((height, width, 4), dtype=np.uint8)
        bgra[:, :, :3] = pixels
        bgra[:, :, 3] = 255
        return bgra
    return pixels.copy()


def _write_tga_bgra(path, bgra, bottom_up=True):
    import numpy as np

    height, width, channels = bgra.shape
    if channels != 4:
        raise ValueError("expected 4-channel BGRA image, got %d" % channels)
    descriptor = 8 if bottom_up else 40
    header = struct.pack(
        "<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, width, height, 32, descriptor
    )
    body = bgra[::-1] if bottom_up else bgra
    with open(path, "wb") as f:
        f.write(header)
        f.write(np.ascontiguousarray(body, dtype=np.uint8).tobytes())


def _dilate_track(bgra, radius):
    """Slightly thicken thin transparent track maps so they survive downscaling."""
    import numpy as np

    if radius <= 0:
        return bgra
    out = bgra.copy()
    height, width, _ = bgra.shape
    for _ in range(radius):
        padded = np.pad(out, ((1, 1), (1, 1), (0, 0)), mode="edge")
        best = out.copy()
        best_score = (
            best[:, :, 3].astype(np.int32) * 768
            + best[:, :, 0].astype(np.int32)
            + best[:, :, 1].astype(np.int32)
            + best[:, :, 2].astype(np.int32)
        )
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dy == 0 and dx == 0:
                    continue
                nb = padded[1 + dy:1 + dy + height, 1 + dx:1 + dx + width]
                nb_score = (
                    nb[:, :, 3].astype(np.int32) * 768
                    + nb[:, :, 0].astype(np.int32)
                    + nb[:, :, 1].astype(np.int32)
                    + nb[:, :, 2].astype(np.int32)
                )
                mask = nb_score > best_score
                best[mask] = nb[mask]
                best_score[mask] = nb_score[mask]
        out = best
    return out


def _resize_tga_bgra(bgra, width, height, texconv, temp_dir, tag):
    src_path = os.path.join(temp_dir, tag + "_src.tga")
    out_dir = os.path.join(temp_dir, tag + "_out")
    os.makedirs(out_dir, exist_ok=True)
    _write_tga_bgra(src_path, bgra, bottom_up=False)
    _run_texconv(
        [texconv, "-y", "-ft", "tga", "-f", "B8G8R8A8_UNORM",
         "-w", str(width), "-h", str(height), "-o", out_dir, src_path],
        "resizing %s" % tag,
    )
    resized_path = os.path.join(out_dir, tag + "_src.TGA")
    if not os.path.isfile(resized_path):
        raise RuntimeError("texconv did not produce %s" % resized_path)
    return _read_tga_bgra(resized_path)


def _loading_background(width=LOD_SIZE[0], height=LOD_SIZE[1]):
    """Dark charcoal radial-gradient canvas for GTR2 loading screens."""
    import numpy as np

    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    dist = np.sqrt(
        ((xx - width * 0.5) / (width * 0.55)) ** 2
        + ((yy - height * 0.5) / (height * 0.65)) ** 2
    )
    val = np.clip(32.0 - 22.0 * np.clip(dist, 0.0, 1.0), 10.0, 32.0).astype(np.uint8)
    canvas = np.empty((height, width, 4), dtype=np.uint8)
    canvas[:, :, 0] = val
    canvas[:, :, 1] = val
    canvas[:, :, 2] = val
    canvas[:, :, 3] = 255
    return canvas


def _compose_on_canvas(bgra, has_alpha, canvas_size, fit_size, texconv, temp_dir,
                       tag, transparent_bg=False):
    import numpy as np

    src_h, src_w = bgra.shape[:2]
    out_w, out_h = canvas_size
    fit_w, fit_h = fit_size
    scale = min(fit_w / float(src_w), fit_h / float(src_h))
    dst_w = max(1, int(round(src_w * scale)))
    dst_h = max(1, int(round(src_h * scale)))
    src = bgra
    if has_alpha and scale < 0.6:
        radius = min(4, max(1, int(round(0.8 / max(scale, 0.05))) // 2))
        src = _dilate_track(bgra, radius)
    resized = _resize_tga_bgra(src, dst_w, dst_h, texconv, temp_dir, tag)
    x0 = (out_w - dst_w) // 2
    y0 = (out_h - dst_h) // 2
    if transparent_bg:
        canvas = np.zeros((out_h, out_w, 4), dtype=np.uint8)
        canvas[y0:y0 + dst_h, x0:x0 + dst_w] = resized
        return canvas
    canvas = _loading_background(out_w, out_h)
    if has_alpha:
        a = resized[:, :, 3:4].astype(np.float32) / 255.0
        bg = canvas[y0:y0 + dst_h, x0:x0 + dst_w, :3].astype(np.float32)
        fg = resized[:, :, :3].astype(np.float32)
        canvas[y0:y0 + dst_h, x0:x0 + dst_w, :3] = np.clip(
            fg * a + bg * (1.0 - a), 0.0, 255.0
        ).astype(np.uint8)
    else:
        canvas[y0:y0 + dst_h, x0:x0 + dst_w, :3] = resized[:, :, :3]
    return canvas


def _write_jpeg_via_texconv(canvas, out_path, texconv, temp_dir, tag):
    src_tga = os.path.join(temp_dir, tag + "_canvas.tga")
    _write_tga_bgra(src_tga, canvas, bottom_up=False)
    _run_texconv(
        [texconv, "-y", "-ft", "jpg", "-wicq", "0.92", "-o", temp_dir, src_tga],
        "creating %s" % os.path.basename(out_path),
    )
    produced = os.path.join(temp_dir, tag + "_canvas.JPG")
    if not os.path.isfile(produced):
        raise RuntimeError("texconv did not produce %s" % produced)
    shutil.copy2(produced, out_path)


def stage_menu_images(preview, track_dir, layout, texconv):
    """Create GTR2's loading screens, thumbnail, and Track.bmp minimap.

    GTR2 naming convention (verified across all stock locations):
      - ``<layout[:4]>_LOD.jpg``   (1024x768 JPEG loading screen)
      - ``<layout[:4]>_LOD_2.jpg`` (1024x45  JPEG loading bar background)
      - ``<layout[:3]>Thmb.tga``   (666x400  32-bit bottom-up RGBA TGA)
      - ``<layout>Track.bmp``      (145x83   32-bit bottom-up RGBA TGA named .bmp)
    """
    import numpy as np

    if not preview:
        return 0
    if not os.path.isfile(preview):
        raise FileNotFoundError("track preview image not found: %s" % preview)

    with tempfile.TemporaryDirectory() as temp_dir:
        decode_cmd = [texconv, "-y", "-ft", "tga", "-f", "B8G8R8A8_UNORM"]
        dims = _png_dimensions(preview)
        if dims and max(dims) > MAX_DECODE_DIM:
            scale = MAX_DECODE_DIM / float(max(dims))
            decode_cmd += [
                "-w", str(max(1, int(round(dims[0] * scale)))),
                "-h", str(max(1, int(round(dims[1] * scale)))),
            ]
        decode_cmd += ["-o", temp_dir, preview]
        _run_texconv(decode_cmd, "decoding %s" % os.path.basename(preview))

        decoded = os.path.join(
            temp_dir, os.path.splitext(os.path.basename(preview))[0] + ".TGA"
        )
        if not os.path.isfile(decoded):
            raise RuntimeError("texconv did not produce %s" % decoded)

        bgra = _read_tga_bgra(decoded)
        alpha = bgra[:, :, 3]
        has_alpha = bool((alpha < 250).any() and (alpha > 16).any())
        if has_alpha:
            ys, xs = np.where(alpha > 8)
            if len(xs):
                bgra = bgra[ys.min():ys.max() + 1, xs.min():xs.max() + 1].copy()
            visible = bgra[bgra[:, :, 3] > 32]
            if len(visible):
                luma = (
                    0.114 * visible[:, 0]
                    + 0.587 * visible[:, 1]
                    + 0.299 * visible[:, 2]
                )
                if np.percentile(luma, 90) < 40.0:
                    bgra[:, :, :3] = 255 - bgra[:, :, :3]

        lod_prefix = layout[:4]
        thmb_prefix = layout[:3]

        # 1. Primary loading screen: 1024x768 JPEG
        lod_canvas = _compose_on_canvas(
            bgra, has_alpha, LOD_SIZE, LOD_FIT, texconv, temp_dir, "lod",
            transparent_bg=False,
        )
        _write_jpeg_via_texconv(
            lod_canvas,
            os.path.join(track_dir, lod_prefix + "_LOD.jpg"),
            texconv, temp_dir, "lod",
        )

        # 2. Loading bar footer: 1024x45 JPEG
        lod2_canvas = _loading_background(LOD2_SIZE[0], LOD2_SIZE[1])
        _write_jpeg_via_texconv(
            lod2_canvas,
            os.path.join(track_dir, lod_prefix + "_LOD_2.jpg"),
            texconv, temp_dir, "lod2",
        )

        # 3. Track thumbnail: 666x400 32-bit bottom-up RGBA TGA
        thmb_canvas = _compose_on_canvas(
            bgra, has_alpha, THMB_SIZE, THMB_FIT, texconv, temp_dir, "thmb",
            transparent_bg=False,
        )
        _write_tga_bgra(
            os.path.join(track_dir, thmb_prefix + "Thmb.tga"),
            thmb_canvas,
            bottom_up=True,
        )

        # 4. Track map outline: 145x83 32-bit bottom-up RGBA TGA saved as *Track.bmp
        # (Stock GTR2 *Track.bmp files are 32-bit uncompressed TGA images.)
        map_canvas = _compose_on_canvas(
            bgra, has_alpha, TRACK_BMP_SIZE, TRACK_BMP_FIT, texconv, temp_dir, "trackbmp",
            transparent_bg=True,
        )
        _write_tga_bgra(
            os.path.join(track_dir, layout + "Track.bmp"),
            map_canvas,
            bottom_up=True,
        )
    return 4


def stage_gtr_archives(source_dir, stage_dir, venue):
    """Optionally stage pre-packed <venue>.gtr and <venue>MAP.gtr archives."""
    names = (venue + ".gtr", venue + "MAP.gtr")
    sources = [os.path.join(source_dir, name) for name in names]
    present = [os.path.isfile(path) for path in sources]
    if any(present) and not all(present):
        raise RuntimeError("both %s and %s are required for GTR packaging" % names)
    if not all(present):
        return ""
    for source, name in zip(sources, names):
        shutil.copy2(source, os.path.join(stage_dir, name))
    return "\n".join("MASFile=" + name for name in names)


def stage_skybox(gmt_dir):
    """Stage GTR2's required skyboxi hierarchy using stock SHARED sky domes.

    Every stock GTR2 track defines Instance=skyboxi with day/night/sunrise/
    sunset/sun/moon/dayR/nightR/sunriseR/sunsetR sub-instances whose GMTs live
    in GameData\\Locations\\SHARED and Skies_central.gtr.
    """
    import gmt_gtr2

    with open(os.path.join(gmt_dir, "skyboxi.gmt"), "wb") as f:
        f.write(gmt_gtr2.make_minimal_gtr2_gmt("skyboxi", "_invisible.dds"))

    return (
        "Instance=skyboxi\n"
        "{\n"
        "  Moveable=True\n"
        "  MeshFile=skyboxi.gmt CollTarget=False HATTarget=False Render=False\n"
        "  Instance=day\n"
        "  {\n"
        "    Moveable=True\n"
        "    MeshFile=day.gmt CollTarget=False HATTarget=False\n"
        "  }\n"
        "  Instance=night\n"
        "  {\n"
        "    Moveable=True\n"
        "    MeshFile=night.gmt CollTarget=False HATTarget=False\n"
        "  }\n"
        "  Instance=sunrise\n"
        "  {\n"
        "    Moveable=True\n"
        "    MeshFile=sunrise.gmt CollTarget=False HATTarget=False\n"
        "  }\n"
        "  Instance=sunset\n"
        "  {\n"
        "    Moveable=True\n"
        "    MeshFile=sunset.gmt CollTarget=False HATTarget=False\n"
        "  }\n"
        "  Instance=sun\n"
        "  {\n"
        "    Moveable=True VisGroups=(32)\n"
        "    MeshFile=sun.gmt CollTarget=False HATTarget=False\n"
        "  }\n"
        "  Instance=moon\n"
        "  {\n"
        "    Moveable=True VisGroups=(32)\n"
        "    MeshFile=moon.gmt CollTarget=False HATTarget=False\n"
        "  }\n"
        "  Instance=dayR\n"
        "  {\n"
        "    Moveable=True\n"
        "    MeshFile=dayR.gmt CollTarget=False HATTarget=False\n"
        "  }\n"
        "  Instance=nightR\n"
        "  {\n"
        "    Moveable=True\n"
        "    MeshFile=nightR.gmt CollTarget=False HATTarget=False\n"
        "  }\n"
        "  Instance=sunriseR\n"
        "  {\n"
        "    Moveable=True\n"
        "    MeshFile=sunriseR.gmt CollTarget=False HATTarget=False\n"
        "  }\n"
        "  Instance=sunsetR\n"
        "  {\n"
        "    Moveable=True\n"
        "    MeshFile=sunsetR.gmt CollTarget=False HATTarget=False\n"
        "  }\n"
        "}\n"
    )


def _pit_gate_corners(pos, perp, half_width=8.0, height=8.0):
    x, y, z = pos
    px, _py, pz = perp
    plen = (px * px + pz * pz) ** 0.5 or 1.0
    px, pz = px / plen, pz / plen
    y0 = y - 1.0
    y1 = y + height
    return (
        (x - px * half_width, y0, z - pz * half_width),
        (x + px * half_width, y0, z + pz * half_width),
        (x + px * half_width, y1, z + pz * half_width),
        (x - px * half_width, y1, z - pz * half_width),
    )


def stage_pit_gates(gmt_dir, aiw_path):
    """Stage GTR2's required xpitin.gmt and xpitout.gmt trigger gates."""
    import gmt_gtr2

    pit_wps = []
    if aiw_path and os.path.isfile(aiw_path):
        with open(aiw_path, encoding="utf-8", errors="replace") as f:
            txt = f.read()
        blocks = txt.split("wp_pos=")[1:]
        for blk in blocks:
            if "wp_branchID=(1)" not in blk:
                continue
            pm = re.match(r"\(([-\d.]+),([-\d.]+),([-\d.]+)\)", blk)
            rm = re.search(r"wp_perp=\(([-\d.]+),([-\d.]+),([-\d.]+)\)", blk)
            if pm and rm:
                pos = tuple(float(v) for v in pm.groups())
                perp = tuple(float(v) for v in rm.groups())
                pit_wps.append((pos, perp))

    if len(pit_wps) >= 2:
        in_pos, in_perp = pit_wps[0]
        out_pos, out_perp = pit_wps[-1]
    elif pit_wps:
        in_pos, in_perp = pit_wps[0]
        out_pos, out_perp = pit_wps[0]
    else:
        in_pos, in_perp = (0.0, 0.0, 0.0), (1.0, 0.0, 0.0)
        out_pos, out_perp = (0.0, 0.0, 10.0), (1.0, 0.0, 0.0)

    blocks = []
    for name, pos, perp in (("xpitin", in_pos, in_perp),
                            ("xpitout", out_pos, out_perp)):
        corners = _pit_gate_corners(pos, perp)
        with open(os.path.join(gmt_dir, name + ".gmt"), "wb") as f:
            f.write(gmt_gtr2.make_quad_gtr2_gmt(name, corners, "_invisible.dds"))
        blocks.append(
            "Instance=%s\n"
            "{\n"
            "  Render=False Change=False\n"
            "  MeshFile=%s.gmt CollTarget=True HATTarget=False\n"
            "  Response=VEHICLE,PITSTOP\n"
            "}\n" % (name, name)
        )
    return blocks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir", help="out/<track> from convert_track.py")
    ap.add_argument("--minimal", action="store_true",
                    help="diagnostic: only collidable meshes, no scenery")
    ap.add_argument("--ambient", type=int, default=165,
                    help="daytime ambient colour level, 0-255")
    ap.add_argument("--sun", type=int, default=255,
                    help="daytime directional light level, 0-255")
    ap.add_argument("--hour", default="12:00",
                    help="time of day for every session, e.g. 12:00")
    ap.add_argument("--install", default=None,
                    help="GTR2 GameData\\Locations folder to copy into")
    ap.add_argument("--preview",
                    help="AC track map or UI preview image for GTR2 menu/loading screens")
    ap.add_argument("--texconv", default="texconv.exe",
                    help="path to texconv.exe for converting menu images")
    ap.add_argument("--road-smoothness", type=float, default=None,
                    help="road surface smoothing in [0.0, 1.0] mapped uniformly to sigma in [0m, 8m] (0 disables, 0.5 -> 4m default, 1.0 -> 8m max; default from ac2gtr2.ini / 0.5)")
    ap.add_argument("--mesh-coarsening", type=float, default=None,
                    help="mesh coarsening strength in [0.0, 1.0] (controls GTR2 vertex normal dedup tolerance; default from ac2gtr2.ini / 0.0)")
    ap.add_argument("--strip-unused-gmt-data",
                    action=argparse.BooleanOptionalAction, default=None,
                    help="strip unused collision FaceData/NormalData on CollTarget=False GMTs and dummy UVTangents on flat-normal cutouts (default from ac2gtr2.ini / true)")
    ap.add_argument("--track-shadows", choices=("compact", "off", "full"), default=None,
                    help="GTR2 track shadow casting mode: compact (default), off, or full (overrides ac2gtr2.ini [track] track_shadows)")
    ap.add_argument("--track-brightness", type=float, default=None,
                    help="track brightness boost in [0.0, 1.0] (0.0 disabled, 1.0 very bright; overrides ac2gtr2.ini [track] track_brightness)")
    args = ap.parse_args()

    import steam_paths
    try:
        _cfg = steam_paths.load_config()
    except ValueError as exc:
        raise SystemExit("config error: %s" % exc)
    if args.road_smoothness is None:
        args.road_smoothness = _cfg["road_smoothness"]
    try:
        eff_road_smooth_sigma = steam_paths.road_smoothness_to_sigma(args.road_smoothness)
    except ValueError as exc:
        raise SystemExit("--road-smoothness: %s" % exc)
    if args.mesh_coarsening is None:
        args.mesh_coarsening = _cfg["mesh_coarsening"]
    try:
        args.mesh_coarsening = steam_paths.validate_mesh_coarsening(args.mesh_coarsening)
    except ValueError as exc:
        raise SystemExit("--mesh-coarsening: %s" % exc)
    if args.strip_unused_gmt_data is None:
        args.strip_unused_gmt_data = _cfg["strip_unused_gmt_data"]
    if args.track_shadows is None:
        args.track_shadows = _cfg["track_shadows"]
    else:
        args.track_shadows = steam_paths.validate_track_shadows(args.track_shadows)
    if args.track_brightness is None:
        args.track_brightness = _cfg["track_brightness"]
    else:
        try:
            args.track_brightness = steam_paths.validate_track_brightness(args.track_brightness)
        except ValueError as exc:
            raise SystemExit("--track-brightness: %s" % exc)

    out_dir = os.path.abspath(args.out_dir)
    venue = os.path.basename(out_dir)
    layout = layout_identifier(venue)
    gmt_dir = os.path.join(out_dir, "gmt")
    with open(os.path.join(gmt_dir, "exported.json")) as f:
        exported = json.load(f)

    mapping = os.path.join(out_dir, "material_map.json")
    mdata = None
    cutouts = {}
    surface_grips = {}
    if os.path.exists(mapping):
        with open(mapping) as f:
            mdata = json.load(f)
        cutouts = mdata.get("alpha_ref", {})
        surface_grips = mdata.get("surface_grips", {})
        flags = {m["mesh"]: m for m in mdata["meshes"]}
        by_id = {m["id"]: m for m in mdata["meshes"] if "id" in m}
        for row in exported:
            cur = (by_id.get(row["id"]) if row.get("id")
                   else flags.get(row.get("mesh")))
            if cur is not None:
                row["render"] = cur.get("render", True)
                row["lod_in"] = cur.get("lod_in", 0.0)
                row["lod_out"] = cur.get("lod_out", 0.0)
                row["shadow"] = shadow_caster(
                    cur, cutouts, mode=args.track_shadows,
                    radius=row.get("radius", 0.0), tris=row.get("tris", 0),
                )
                row["shadow_receiver"] = is_shadow_receiver(cur, row, mode=args.track_shadows)
                row["_meta"] = cur
            else:
                row["shadow"] = None
                row["shadow_receiver"] = is_shadow_receiver(None, row, mode=args.track_shadows)
                row["_meta"] = None
    else:
        for row in exported:
            row["shadow"] = None
            row["shadow_receiver"] = is_shadow_receiver(None, row, mode=args.track_shadows)
            row["_meta"] = None

    stage = os.path.join(out_dir, "gtr2", venue)
    if os.path.isdir(stage):
        shutil.rmtree(stage)
    gmts = os.path.join(stage, "gmt")
    maps = os.path.join(stage, "maps")
    for d in (stage, gmts, maps):
        os.makedirs(d, exist_ok=True)
    mas_files = stage_gtr_archives(out_dir, stage, venue)

    aiw_src = os.path.join(out_dir, venue + ".AIW")
    lap_km = 0.0
    if os.path.exists(aiw_src):
        with open(aiw_src, encoding="utf-8", errors="replace") as f:
            m = re.search(r"lap_length=([\d.]+)", f.read())
        if m:
            lap_km = float(m.group(1)) / 1000.0

    if args.minimal:
        keep = [r for r in exported if r["physical"]]
        print("MINIMAL build: %d of %d instances" % (len(keep), len(exported)))
        exported = keep

    # Opaque geometry first, then transparent, biggest first.
    exported.sort(key=lambda r: (bool(r.get("blend")), -float(r.get("radius", 0.0))))

    skybox = stage_skybox(gmts)
    max_shadow_range = 250.0 if args.track_shadows == "full" else 200.0
    lines = [TRK_HEADER.format(
        venue=venue, mas_files=mas_files, skybox=skybox,
        max_shadow_range=max_shadow_range,
    )]
    used_instances = {
        "skyboxi", "day", "night", "sunrise", "sunset", "sun", "moon",
        "dayr", "nightr", "sunriser", "sunsetr", "xpitin", "xpitout",
    }
    phys = 0
    casters = 0
    receivers = 0
    for row in exported:
        name = instance_identifier(os.path.splitext(row["gmt"])[0], used_instances)
        coll = "True" if row["physical"] else "False"
        if row["physical"]:
            phys += 1
        hat = "False" if row.get("timing") else coll
        if row.get("timing"):
            lines.append(
                "Instance=%s\n{\n"
                "  Render=False Change=False\n"
                "  MeshFile=%s CollTarget=True HATTarget=False\n"
                "  Response=VEHICLE,TIMING\n"
                "  Response=VEHICLE,SECTOR\n}\n"
                % (name, row["gmt"])
            )
            continue
        if not row.get("render", True):
            lines.append(
                "Instance=%s\n{\n"
                "  Render=False Change=False\n"
                "  MeshFile=%s CollTarget=%s HATTarget=%s\n}\n"
                % (name, row["gmt"], coll, hat)
            )
            continue

        lin, lout = gtr2_lod_range(row.get("_meta"), row)
        extra = (" LODIn=(%.1f) LODOut=(%.1f)" % (lin, lout)) if lout > 0.0 else ""
        if row.get("shadow_receiver"):
            extra += " ShadowReceiver=True"
            receivers += 1
        else:
            extra += " ShadowReceiver=False"
        sc = row.get("shadow")
        if sc:
            kind, size = sc
            extra += " ShadowCaster=(Static, %s, %d, %d)" % (kind, size, size)
            casters += 1
        lines.append("Instance=%s\n{\n  MeshFile=%s CollTarget=%s HATTarget=%s%s\n}\n"
                     % (name, row["gmt"], coll, hat, extra))
    if not args.minimal:
        lines.extend(stage_pit_gates(gmts, aiw_src))

    road_dry = float(surface_grips.get("road", 1.00))
    rumble_dry = float(surface_grips.get("rmbl", 0.96))
    lap_sec = max(30.0, (lap_km * 1000.0) / 42.0) if lap_km > 0 else 100.0

    lighting = gdb_lighting_colors(
        ambient=args.ambient, sun=args.sun, brightness=args.track_brightness
    )
    write(os.path.join(stage, layout + ".trk"), "\n".join(lines))
    write(os.path.join(stage, layout + ".gdb"),
          GDB.format(
              venue=venue,
              layout=layout,
              km=lap_km,
              mi=lap_km * 0.621371,
              hour=args.hour,
              day_amb=lighting["day_amb"],
              day_sun=lighting["day_sun"],
              sunrise_amb=lighting["sunrise_amb"],
              sunset_amb=lighting["sunset_amb"],
              rain_day_amb=lighting["rain_day_amb"],
              rain_day_sun=lighting["rain_day_sun"],
              lap_sec=lap_sec,
              race_sec=lap_sec * 1.02,
              road_dry=road_dry,
              ai_dry=min(1.15, road_dry * 1.02),
              road_wet=road_dry * 0.85,
              ai_wet=road_dry * 0.86,
              rumble_dry=rumble_dry,
              rumble_wet=rumble_dry * 0.85,
          ))
    write(os.path.join(stage, layout + ".grd"), GRD_TEMPLATE)

    menu_images = stage_menu_images(args.preview, stage, layout, args.texconv)
    if menu_images:
        print("  menu images %d (_LOD.jpg, _LOD_2.jpg, Thmb.tga, Track.bmp)" % menu_images)

    aiw = os.path.join(out_dir, venue + ".AIW")
    has_aiw = os.path.exists(aiw)
    if has_aiw:
        shutil.copy2(aiw, os.path.join(stage, layout + ".AIW"))

    wanted = {r["gmt"].lower() for r in exported}
    n_gmt = 0
    for f in os.listdir(gmt_dir):
        if not f.lower().endswith(".gmt"):
            continue
        if args.minimal and f.lower() not in wanted:
            continue
        shutil.copy2(os.path.join(gmt_dir, f), gmts)
        n_gmt += 1

    n_tex = 0
    tex_dir = os.path.join(out_dir, "textures")
    if os.path.isdir(tex_dir):
        for f in os.listdir(tex_dir):
            src = os.path.join(tex_dir, f)
            if not os.path.isfile(src):
                continue
            shutil.copy2(src, maps)
            n_tex += 1

    names = {os.path.splitext(r["gmt"])[0].lower() for r in exported}
    need = {"xfinish", "xsector1", "xsector2"}
    missing = sorted(need - names)
    if missing and not args.minimal:
        raise SystemExit("refusing to install: timing strips missing (%s). "
                         "Re-run the geometry stage." % ", ".join(missing))

    print("staged %s" % stage)
    print("  instances   %d  (%d collidable)" % (len(exported), phys))
    print("  shadows     mode=%s, %d casters, %d receivers (MaxShadowRange=%.0fm)"
          % (args.track_shadows, casters, receivers, max_shadow_range))
    print("  gmt files   %d" % n_gmt)
    print("  textures    %d" % n_tex)
    print("  waypoints   %s" % ("yes" if has_aiw else "MISSING"))

    cam = os.path.join(out_dir, venue + ".cam")
    if os.path.exists(cam):
        shutil.copy2(cam, os.path.join(stage, layout + ".cam"))
        print("  cameras     converted from AC")

    if eff_road_smooth_sigma > 0.0:
        import road_smooth
        rs = road_smooth.smooth_staged_road_gmts(
            gmts, exported, mdata=mdata, sigma=eff_road_smooth_sigma)
        if rs["meshes"]:
            print("  road smooth %d meshes, %d/%d verts (road_smoothness=%s, effective road_smooth_sigma=%sm, dY rms=%.2fmm, max=%.1fmm)"
                  % (rs["meshes"], rs["smoothed"], rs["verts"],
                     args.road_smoothness, eff_road_smooth_sigma,
                     rs["dy_rms_mm"], rs["dy_max_mm"]))
    else:
        print("  road smooth disabled (road_smoothness=%s, effective road_smooth_sigma=0.0m)"
              % (args.road_smoothness,))

    if args.strip_unused_gmt_data:
        collidable_gmts = {
            row["gmt"].lower()
            for row in exported
            if row.get("physical") or row.get("timing")
        }
        collidable_gmts.update({"xpitin.gmt", "xpitout.gmt", "skyboxi.gmt"})
    else:
        collidable_gmts = None

    import gmt_gtr2
    converted = gmt_gtr2.convert_assets(
        stage,
        mesh_coarsening=args.mesh_coarsening,
        collidable_gmts=collidable_gmts,
        drop_flat_tangents=bool(args.strip_unused_gmt_data),
        track_brightness=args.track_brightness,
    )
    if collidable_gmts is not None:
        visual_stripped = max(0, n_gmt - len(collidable_gmts - {"xpitin.gmt", "xpitout.gmt", "skyboxi.gmt"}))
        print("  converted   %d GMT files to GTR2 v2.10 (stripped unused faces on %d visual GMTs + flat-normal tangents)"
              % (converted, visual_stripped))
    else:
        print("  converted   %d GMT files to GTR2 v2.10 (unused GMT data stripping disabled)" % converted)
    if args.track_brightness > 0.0:
        bright_tex = brighten_staged_diffuse_textures(stage, args.track_brightness)
        print("  brightness  track_brightness=%.2f (DayAmbient=%s, lifted %d diffuse DDS textures + GMT materials)"
              % (args.track_brightness, lighting["day_amb"][0], bright_tex))

    if args.install:
        try:
            dest = install_location(stage, args.install, venue)
            print("installed -> %s" % dest)
        except (OSError, RuntimeError) as exc:
            print("could not install: %s" % exc)
            print("staged copy: %s" % stage)
            raise SystemExit(1)


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


if __name__ == "__main__":
    main()
