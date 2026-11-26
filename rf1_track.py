"""Convert rFactor 1 tracks directly to GTR2.

rFactor 1 and GTR2 share the ISI gMotor 2.0 architecture:
  - rF1 .mas archives unpack into loose GMT v2.00 meshes and DDS/TGA/BMP/BIK assets.
  - rF1 GMT v2.00 binaries convert cleanly to GTR2 GMT v2.10 (532-byte header with
    GMOTOR_GMT_2.10 magic, 108-byte mesh descriptors, 36-byte FaceData with plane_d,
    L2USEVERTEXONLY -> L2USEVERTEXPIXEL, and rF1 .tdf -> GTR2 specialfx.tec surface
    prefix remapping).
  - rF1 .scn -> GTR2 .trk (GTR2 SearchPath, SHARED GTRs, native GTR2 skybox,
    <= 31-char Instance= names, dropping unreferenced missing meshes).
  - rF1 .gdb -> GTR2 .gdb (translating RGB lighting, NorthDirection, and .tdf grips).
  - rF1 .AIW and .cam are natively compatible with GTR2.

The track_shadows option (compact/off/full) applies to rF1 inputs; all other
AC-specific conversion options (texture_scale, road_smoothness, mesh_coarsening,
strip_unused_gmt_data, track_brightness) are ignored for rF1 inputs.
"""
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile

import finalize_track
import fix_textures
import gmt_gtr2
import steam_paths

TEXTURE_EXTS = {".dds", ".tga", ".bmp", ".bik"}
SKYBOX_GMTS = {
    "skyboxi.gmt", "horizon.gmt", "horizonlights.gmt", "sky.gmt",
    "day.gmt", "night.gmt", "sunrise.gmt", "sunset.gmt", "sun.gmt", "moon.gmt",
    "dayr.gmt", "nightr.gmt", "sunriser.gmt", "sunsetr.gmt",
}
COMMON_MAS_NAMES = {"commonmaps.mas", "commonmaps.gtr"}


def _is_skybox_gmt(filename_lower):
    """True for rF1/SCE skybox meshes replaced by GTR2's SHARED skybox."""
    return filename_lower in SKYBOX_GMTS or filename_lower.startswith("srpl_")


def _is_skipped_mas(filename_lower):
    """True for shared/sky MAS archives handled separately or replaced by GTR2 SHARED."""
    return (
        filename_lower in COMMON_MAS_NAMES
        or filename_lower.startswith(("sky", "clouds"))
    )

RF1_GDB_TEMPLATE = """{layout}
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
  RaceStart = {race_start}
  RaceLaps = {race_laps}
  RaceTime = {race_time}
  RaceTimeScaled = {race_time}

  Attrition = {attrition}

  TrackName = {track_name}
  GrandPrixName = {gp_name}
  EventName = {event_name}
  VenueName = {venue_name}
  Location = {location}
  Length = {length}
  Track Record = {track_record}

  SettingsFolder = {venue}
  SettingsCopy = {venue}.svm
  SettingsAI = {venue}.svm
  Qualify Laptime = {qual_lap:.3f}
  Race Laptime = {race_lap:.3f}

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
  ShadowMinSunAngle = {shadow_min_sun}

  SkyBlendSunAngles = {sky_blend}

  SunriseAmbientColour = {sunrise_amb}
  SunriseDirectionalColour = {sunrise_sun}
  SunriseFogColour = {sunrise_fog}
  SunriseFogIn = {sunrise_fog_in}
  SunriseFogOut = {sunrise_fog_out}
  RainSunriseAmbientColour = (85,85,85)
  RainSunriseDirectionalColour = (45,45,45)
  RainSunriseFogColour = (43,53,55)
  RainSunriseFogIn = -10.0
  RainSunriseFogOut = 650.0

  DayAmbientColour = {day_amb}
  DayDirectionalColour = {day_sun}
  DayFogColour = {day_fog}
  DayFogIn = {day_fog_in}
  DayFogOut = {day_fog_out}
  RainDayAmbientColour = (105,105,105)
  RainDayDirectionalColour = (45,45,45)
  RainDayFogColour = (77,80,81)
  RainDayFogIn = -25.0
  RainDayFogOut = 1050.0

  SunsetAmbientColour = {sunset_amb}
  SunsetDirectionalColour = {sunset_sun}
  SunsetFogColour = {sunset_fog}
  SunsetFogIn = {sunset_fog_in}
  SunsetFogOut = {sunset_fog_out}
  RainSunsetAmbientColour = (72,70,70)
  RainSunsetDirectionalColour = (35,32,32)
  RainSunsetFogColour = (37,41,35)
  RainSunsetFogIn = -10.0
  RainSunsetFogOut = 650.0

  NightAmbientColour = {night_amb}
  NightDirectionalColour = {night_sun}
  NightFogColour = {night_fog}
  NightFogIn = {night_fog_in}
  NightFogOut = {night_fog_out}
  RainNightAmbientColour = (25,25,25)
  RainNightDirectionalColour = (15,15,15)
  RainNightFogColour = (23,25,24)
  RainNightFogIn = -10.0
  RainNightFogOut = 750.0

  // Sun position
  Latitude = {latitude}
  NorthDir = {north_dir}
  RaceDate = {race_date}

  HorizonRadius = 1500.0
  HorizonMaxHeight = 50.0
  HorizonMinHeight = 30.0

  Max Vehicles = {max_vehicles}
}}
"""


def _find_scn_gdb_in_dir(folder):
    """Return list of layout dicts found directly inside ``folder``."""
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    scns = {}
    gdbs = {}
    aiws = {}
    cams = {}
    tdfs = {}
    for name in names:
        full = os.path.join(folder, name)
        if not os.path.isfile(full):
            continue
        stem, ext = os.path.splitext(name)
        ext_l = ext.lower()
        key = stem.lower()
        if ext_l == ".scn":
            scns[key] = (stem, full)
        elif ext_l == ".gdb":
            gdbs[key] = (stem, full)
        elif ext_l == ".aiw":
            aiws[key] = full
        elif ext_l == ".cam":
            cams[key] = full
        elif ext_l == ".tdf":
            tdfs[key] = full

    if not scns or not gdbs:
        return []

    layouts = []
    # Match exact stem pairs first.
    matched_scns = set()
    for key in sorted(scns):
        if key in gdbs:
            stem, scn_path = scns[key]
            layouts.append({
                "name": stem,
                "dir": folder,
                "scn": scn_path,
                "gdb": gdbs[key][1],
                "aiw": aiws.get(key) or (next(iter(aiws.values())) if len(aiws) == 1 else None),
                "cam": cams.get(key) or (next(iter(cams.values())) if len(cams) == 1 else None),
                "tdf": tdfs.get(key) or (next(iter(tdfs.values())) if len(tdfs) == 1 else None),
            })
            matched_scns.add(key)

    # Fallback: single .scn + single .gdb with differing stems.
    if not layouts and len(scns) == 1 and len(gdbs) == 1:
        skey, (stem, scn_path) = next(iter(scns.items()))
        gkey, (gstem, gdb_path) = next(iter(gdbs.items()))
        layouts.append({
            "name": gstem or stem,
            "dir": folder,
            "scn": scn_path,
            "gdb": gdb_path,
            "aiw": aiws.get(gkey) or aiws.get(skey) or next(iter(aiws.values()), None),
            "cam": cams.get(gkey) or cams.get(skey) or next(iter(cams.values()), None),
            "tdf": tdfs.get(gkey) or tdfs.get(skey) or next(iter(tdfs.values()), None),
        })
    return layouts


def find_rf1_layouts(track_dir):
    """Discover rFactor 1 layouts and asset root for ``track_dir``.

    Returns ``(asset_root, layouts)`` where ``layouts`` is a list of dicts.
    Supports both a venue folder (containing layout subfolders like ``HRS\\``
    or ``MFinthen_DTM\\`` + ``MFinthen_DTMwet\\``) and a direct layout folder.
    """
    track_dir = os.path.abspath(track_dir)
    if not os.path.isdir(track_dir):
        return track_dir, []

    direct = _find_scn_gdb_in_dir(track_dir)
    if direct:
        parent = os.path.dirname(track_dir)
        asset_root = track_dir
        if parent and os.path.isdir(parent) and parent != track_dir:
            try:
                if any(f.lower().endswith(".mas") for f in os.listdir(parent)):
                    asset_root = parent
            except OSError:
                pass
        return asset_root, direct

    found = []
    try:
        entries = sorted(os.listdir(track_dir))
    except OSError:
        entries = []
    for name in entries:
        sub = os.path.join(track_dir, name)
        if os.path.isdir(sub):
            found.extend(_find_scn_gdb_in_dir(sub))
    return track_dir, found


_SCE_SCN_MARKER_RE = re.compile(
    r"(?im)^\s*(?:SearchPath\s*=\s*.*ReizaCommon\b"
    r"|MASFile\s*=\s*(?:AdPack\w*|CloudsLight|SkyTropical\w*)\.MAS\b)"
)
_SCE_GDB_MARKER_RE = re.compile(
    r"(?im)^\s*Filter\s+Properties\s*=\s*.*\b(?:StockV8|StockCar|GSCE|Reiza)\b"
)


def _is_sce_layout(lay):
    """Return True when an rF1-style layout carries Stock Car Extreme / Reiza markers."""
    for path, pat in ((lay.get("scn"), _SCE_SCN_MARKER_RE), (lay.get("gdb"), _SCE_GDB_MARKER_RE)):
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                head = f.read(4096)
        except OSError:
            continue
        if pat.search(head):
            return True
    return False


def detect_track_type(track_dir):
    """Detect whether ``track_dir`` is AC (``'ac'``), Stock Car Extreme (``'sce'``), or rFactor 1 (``'rf1'``)."""
    if not track_dir or not os.path.isdir(track_dir):
        return None
    try:
        entries = os.listdir(track_dir)
    except OSError:
        return None
    if any(f.lower().endswith(".kn5") for f in entries):
        return "ac"
    _root, layouts = find_rf1_layouts(track_dir)
    if layouts:
        if any(_is_sce_layout(lay) for lay in layouts):
            return "sce"
        return "rf1"
    return None


def rf1_layout_names(track_dir):
    """Return sorted layout names for an rFactor 1 or Stock Car Extreme track directory."""
    _root, layouts = find_rf1_layouts(track_dir)
    return [lay["name"] for lay in layouts]


def _is_plain_tga(head):
    """Return True when ``head`` looks like an unencrypted TGA image header."""
    if len(head) < 18:
        return False
    color_map_type = head[1]
    img_type = head[2]
    w, h = struct.unpack_from("<HH", head, 12)
    bpp = head[16]
    return (
        color_map_type in (0, 1)
        and img_type in (1, 2, 3, 9, 10, 11)
        and bpp in (8, 15, 16, 24, 32)
        and 1 <= w <= 8192
        and 1 <= h <= 8192
    )


def decrypt_sce_textures(map_dirs, rfactorcrypt=None):
    """Decrypt any rfactorcrypt-encrypted texture files (.tga/.dds/.bmp) in-place.

    Stock Car Extreme tracks often ship loose encrypted ``SKIDHARD.TGA`` files;
    GTR2 crashes mid-load if it tries to parse an encrypted TGA header.
    Returns the number of files decrypted.
    """
    suspects = []
    for md in map_dirs:
        if not md or not os.path.isdir(md):
            continue
        for fn in sorted(os.listdir(md)):
            low = fn.lower()
            ext = os.path.splitext(low)[1]
            if ext not in (".dds", ".tga", ".bmp"):
                continue
            path = os.path.join(md, fn)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "rb") as f:
                    head = f.read(32)
            except OSError:
                continue
            if ext == ".dds" and head[:4] == b"DDS ":
                continue
            if ext == ".bmp" and head[:2] == b"BM":
                continue
            if ext == ".tga" and _is_plain_tga(head):
                continue
            suspects.append(path)
    if not suspects:
        return 0
    tool = rfactorcrypt or steam_paths.RFACTORCRYPT
    if not tool or not os.path.isfile(tool):
        return 0
    encrypted = []
    for i in range(0, len(suspects), 200):
        chunk = [os.path.abspath(p) for p in suspects[i:i + 200]]
        res = subprocess.run(
            [tool, "-i"] + chunk,
            capture_output=True, text=True, errors="replace",
        )
        for line in (res.stdout or "").splitlines():
            # Encrypted files report a known game key name (not 'None').
            if "sign:" in line and "'None'" not in line:
                m = re.search(r"'([^']+)'\s*$", line.strip())
                if m and os.path.isfile(m.group(1)):
                    encrypted.append(m.group(1))
    if not encrypted:
        return 0
    for i in range(0, len(encrypted), 200):
        chunk = [os.path.abspath(p) for p in encrypted[i:i + 200]]
        res = subprocess.run(
            [tool, "-d"] + chunk,
            capture_output=True, text=True, errors="replace",
        )
        if res.returncode != 0:
            details = ((res.stdout or "") + "\n" + (res.stderr or "")).strip()
            raise RuntimeError(
                "rfactorcrypt failed decrypting %d texture(s) (exit %d):\n%s"
                % (len(chunk), res.returncode, details[-1500:])
            )
    return len(encrypted)


def decrypt_sce_gmts(gmt_paths, rfactorcrypt=None):
    """Decrypt any encrypted GSCE/SCE GMT files in-place via ``rfactorcrypt.exe -d``.

    Returns the number of files decrypted. Plain rF1/GTR2 GMTs are skipped.
    """
    encrypted = []
    for path in gmt_paths:
        try:
            with open(path, "rb") as f:
                b = f.read()
        except OSError:
            continue
        if gmt_gtr2.is_gtr2_gmt(b) or gmt_gtr2.is_rf1_gmt(b):
            continue
        encrypted.append(path)
    if not encrypted:
        return 0
    tool = rfactorcrypt or steam_paths.RFACTORCRYPT
    if not tool or not os.path.isfile(tool):
        raise SystemExit(
            "rfactorcrypt.exe not found (looked for %s), required to decrypt %d Stock Car Extreme GMT(s). "
            "Pass --rfactorcrypt or set AC2GTR2_RFACTORCRYPT."
            % (tool, len(encrypted))
        )
    chunk_size = 200
    for i in range(0, len(encrypted), chunk_size):
        chunk = [os.path.abspath(p) for p in encrypted[i:i + chunk_size]]
        res = subprocess.run(
            [tool, "-d"] + chunk,
            capture_output=True, text=True, errors="replace",
        )
        if res.returncode != 0:
            details = ((res.stdout or "") + "\n" + (res.stderr or "")).strip()
            raise RuntimeError(
                "rfactorcrypt failed decrypting %d GMT(s) (exit %d):\n%s"
                % (len(chunk), res.returncode, details[-1500:])
            )
    return len(encrypted)


def _recover_reiza_common_textures(missing_refs, maps_dir, sce_common_dirs,
                                   preferred_mas=(), masunpack=None):
    """Copy referenced missing textures from installed SCE/AMS ReizaCommon & commonmaps.

    Only unpacks archives that contain at least one still-missing texture, and
    only copies the exact missing files into ``maps_dir``.
    Returns ``(recovered_lower_set, copied_count)``.
    """
    if not missing_refs or not sce_common_dirs:
        return set(), 0
    # Map lowercased filename and stem -> original requested filename(s).
    need_exact = {t.lower(): t for t in missing_refs}
    need_stems = {}
    for t in missing_refs:
        st = os.path.splitext(t.lower())[0]
        need_stems.setdefault(st, set()).add(t.lower())

    recovered = set()
    copied = 0

    def _still_needed(fname_low):
        if fname_low in need_exact:
            return True
        st = os.path.splitext(fname_low)[0]
        return st in need_stems and bool(need_stems[st])

    def _mark_found(fname_low, src_path):
        nonlocal copied
        safe = sanitize_texture_filename(os.path.basename(src_path))
        dst = os.path.join(maps_dir, safe)
        if not os.path.isfile(dst):
            shutil.copy2(src_path, dst)
            copied += 1
        safe_l = safe.lower()
        recovered.add(safe_l)
        need_exact.pop(fname_low, None)
        need_exact.pop(safe_l, None)
        st = os.path.splitext(safe_l)[0]
        for matched_l in list(need_stems.get(st, ())):
            need_exact.pop(matched_l, None)
        need_stems.pop(st, None)

    # 1. Check loose files in sce_common_dirs first.
    mas_candidates = []
    seen_mas = set()
    pref_order = {m.lower(): idx for idx, m in enumerate(preferred_mas)}
    for d in sce_common_dirs:
        if not os.path.isdir(d):
            continue
        try:
            entries = sorted(os.listdir(d))
        except OSError:
            continue
        for fn in entries:
            fp = os.path.join(d, fn)
            if not os.path.isfile(fp):
                continue
            low = fn.lower()
            ext = os.path.splitext(low)[1]
            if ext in TEXTURE_EXTS and _still_needed(low):
                _mark_found(low, fp)
            elif ext == ".mas" and not low.startswith(("sky", "clouds")):
                key = os.path.normcase(os.path.abspath(fp))
                if key not in seen_mas:
                    seen_mas.add(key)
                    pri = pref_order.get(low, 1000 if low == "commonmaps.mas" else 2000)
                    mas_candidates.append((pri, low, fp))

    if not need_exact:
        return recovered, copied

    # 2. Scan MAS archives in priority order (SCN-referenced AdPack*/commonmaps first),
    #    unpacking only archives that match at least one still-needed texture.
    mas_candidates.sort(key=lambda x: (x[0], x[1]))
    with tempfile.TemporaryDirectory(prefix="ac2gtr2-sce-common-") as tmp:
        for idx, (_pri, low_m, mp) in enumerate(mas_candidates):
            if not need_exact:
                break
            entries = list_mas_entries(mp, masunpack=masunpack)
            hits = [e for e in entries if os.path.splitext(e)[1] in TEXTURE_EXTS and _still_needed(e)]
            if not hits:
                continue
            dest = os.path.join(tmp, "c%02d_%s" % (idx, os.path.splitext(low_m)[0]))
            try:
                unpack_mas(mp, dest, masunpack=masunpack)
            except Exception:
                continue
            for fn in sorted(os.listdir(dest)):
                fp = os.path.join(dest, fn)
                if not os.path.isfile(fp):
                    continue
                fl = fn.lower()
                if os.path.splitext(fl)[1] in TEXTURE_EXTS and _still_needed(fl):
                    _mark_found(fl, fp)

    return recovered, copied


def unpack_mas(mas_path, out_dir, masunpack=None):
    """Unpack a .mas / .gtr archive into a fresh directory using masunpack.exe."""
    tool = masunpack or steam_paths.MASUNPACK
    if not tool or not os.path.isfile(tool):
        raise SystemExit(
            "masunpack.exe not found (looked for %s). Pass --masunpack or set AC2GTR2_MASUNPACK."
            % tool
        )
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(os.path.dirname(os.path.abspath(out_dir)), exist_ok=True)
    res = subprocess.run(
        [tool, os.path.abspath(mas_path), os.path.abspath(out_dir)],
        capture_output=True, text=True, errors="replace",
    )
    if res.returncode != 0:
        details = ((res.stdout or "") + "\n" + (res.stderr or "")).strip()
        raise RuntimeError(
            "masunpack failed on %s (exit %d):\n%s"
            % (mas_path, res.returncode, details[-1500:])
        )


def list_mas_entries(mas_path, masunpack=None):
    """Return lowercased entry basenames inside a .mas / .gtr archive."""
    tool = masunpack or steam_paths.MASUNPACK
    if not tool or not os.path.isfile(tool) or not os.path.isfile(mas_path):
        return set()
    res = subprocess.run(
        [tool, "-l", os.path.abspath(mas_path)],
        capture_output=True, text=True, errors="replace",
    )
    if res.returncode != 0:
        return set()
    return {
        os.path.basename(line.strip()).lower()
        for line in res.stdout.splitlines()
        if line.strip()
    }


def _strip_comment(line):
    idx = line.find("//")
    return line[:idx] if idx >= 0 else line


def parse_scn_mas_files(scn_path):
    """Return ordered list of MASFile= names referenced in an rF1 .scn file."""
    mas_files = []
    with open(scn_path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = _strip_comment(raw).strip()
            m = re.match(r"(?i)^MASFile\s*=\s*(\S+)", line)
            if m:
                mas_files.append(m.group(1).strip())
    return mas_files


def _resolve_case_insensitive(folders, filename):
    target = filename.lower()
    for folder in folders:
        if not folder or not os.path.isdir(folder):
            continue
        direct = os.path.join(folder, filename)
        if os.path.isfile(direct):
            return direct
        try:
            for name in os.listdir(folder):
                if name.lower() == target:
                    cand = os.path.join(folder, name)
                    if os.path.isfile(cand):
                        return cand
        except OSError:
            continue
    return None


def _parse_rf1_gdb(gdb_path):
    """Parse key=value pairs from an rF1 .gdb file (case-insensitive keys)."""
    vals = {}
    with open(gdb_path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = _strip_comment(raw).strip()
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            k = key.strip().lower()
            v = val.strip()
            if k and v and k not in vals:
                vals[k] = v
    return vals


def _parse_tdf_grips(tdf_path):
    """Read road and rumble Dry/Wet grip values from an rF1 .tdf file."""
    grips = {}
    if not tdf_path or not os.path.isfile(tdf_path):
        return grips
    with open(tdf_path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    blocks = re.split(r"(?i)\[\s*FEEDBACK\s*\]", text)
    for blk in blocks[1:]:
        stop = blk.find("[")
        if stop >= 0:
            blk = blk[:stop]
        m_mat = re.search(r"(?im)^\s*Materials\s*=\s*(.+)", blk)
        if not m_mat:
            continue
        mats = [m.strip().lower() for m in _strip_comment(m_mat.group(1)).split(",")]
        m_dry = re.search(r"(?im)^\s*Dry\s*=\s*([0-9.]+)", blk)
        m_wet = re.search(r"(?im)^\s*Wet\s*=\s*([0-9.]+)", blk)
        dry = float(m_dry.group(1)) if m_dry else None
        wet = float(m_wet.group(1)) if m_wet else None
        if any(m.startswith("road") for m in mats) and "road_dry" not in grips:
            if dry is not None:
                grips["road_dry"] = dry
            if wet is not None:
                grips["road_wet"] = wet
        if any(m.startswith("rmbl") for m in mats) and "rumble_dry" not in grips:
            if dry is not None:
                grips["rumble_dry"] = dry
            if wet is not None:
                grips["rumble_wet"] = wet
    return grips


def _safe_float(raw, default):
    if raw is None:
        return default
    m = re.search(r"[-+]?\d+(?:\.\d+)?", str(raw).replace(",", ""))
    return float(m.group(0)) if m else default


def _safe_int(raw, default):
    if raw is None:
        return default
    m = re.search(r"[-+]?\d+", str(raw).replace(",", ""))
    return int(m.group(0)) if m else default


def _clean_rgb_tuple(raw, default):
    if not raw:
        return default
    nums = re.findall(r"\d+", raw)
    if len(nums) >= 3:
        r, g, b = (max(0, min(255, int(x))) for x in nums[:3])
        return "(%d,%d,%d)" % (r, g, b)
    return default


def sanitize_rf1_aiw(src_path, dst_path):
    """Copy an rFactor 1 .AIW to ``dst_path``, clamping GTR2-incompatible [Features].

    GTR2 expects ``garagespots`` to be the per-team garage slot count (1..3,
    matching ``GarPos=(0..2)``). Some rF1 tracks (e.g. Mainz-Finthen) write the
    total number of teams (e.g. 30) into ``garagespots``, which causes GTR2 to
    abort/crash when loading the track. Returns dict with actual slot counts.
    """
    with open(src_path, encoding="utf-8", errors="replace") as f:
        text = f.read()

    # Count actual [GRID] slots and distinct per-team GarPos indices in [PITS].
    secs = re.split(r"(?m)^(\[[A-Za-z]+\])\s*$", text)
    sec_map = {secs[i].upper(): secs[i + 1] for i in range(1, len(secs), 2)}
    grid_count = len(re.findall(r"(?im)^GridIndex\s*=", sec_map.get("[GRID]", "")))
    team_count = len(re.findall(r"(?im)^TeamIndex\s*=", sec_map.get("[PITS]", "")))
    gar_idxs = [int(x) for x in re.findall(r"(?i)GarPos\s*=\s*\(\s*(\d+)\s*,", sec_map.get("[PITS]", ""))]
    actual_gar = min(3, max(1, (max(gar_idxs) + 1) if gar_idxs else 3))

    fixed_gar = False
    fixed_grid = False
    fixed_pits = False

    def _clamp_gar(m):
        nonlocal fixed_gar
        old = int(m.group(2))
        if old > 3 or old < 1:
            fixed_gar = True
            return "%s%d" % (m.group(1), actual_gar)
        return m.group(0)

    def _clamp_grid(m):
        nonlocal fixed_grid
        old = int(m.group(2))
        if grid_count > 0 and old > grid_count:
            fixed_grid = True
            return "%s%d" % (m.group(1), grid_count)
        return m.group(0)

    def _clamp_pits(m):
        nonlocal fixed_pits
        old = int(m.group(2))
        if team_count > 0 and old > team_count:
            fixed_pits = True
            return "%s%d" % (m.group(1), team_count)
        return m.group(0)

    out_text = re.sub(r"(?im)^(\s*garagespots\s*=\s*)(\d+)", _clamp_gar, text, count=1)
    out_text = re.sub(r"(?im)^(\s*startinggrid\s*=\s*)(\d+)", _clamp_grid, out_text, count=1)
    out_text = re.sub(r"(?im)^(\s*pitspots\s*=\s*)(\d+)", _clamp_pits, out_text, count=1)
    with open(dst_path, "w", encoding="utf-8") as f:
        f.write(out_text)
    return {
        "grid_spots": grid_count,
        "team_spots": team_count,
        "garage_spots": actual_gar,
        "clamped_garagespots": fixed_gar,
        "clamped_startinggrid": fixed_grid,
        "clamped_pitspots": fixed_pits,
    }


def assign_rf1_layout_ids(selected):
    """Assign GTR2-safe layout IDs (<= 16 chars) with distinct 3/4-char prefixes.

    GTR2 maps loading screens via ``<layout[:4]>_LOD.jpg``, thumbnails via
    ``<layout[:3]>Thmb.tga``, and selects multi-layout entries strictly without
    shared 4-char prefixes or prefix-overlapping stems. When a multi-layout
    venue has colliding stems (e.g. ``MFinthen_DTM`` and ``MFinthen_DTMwet``),
    prefixes each layout ID with a unique digit/letter (like stock GTR2's
    ``3Monza`` / ``4Monza`` / ``JNMonza``).
    """
    names = [lay["name"] for lay in selected]
    base_ids = [finalize_track.layout_identifier(n) for n in names]
    if len(selected) <= 1:
        return {n: lid for n, lid in zip(names, base_ids)}

    def _has_conflict(ids):
        lows = [s.lower() for s in ids]
        if len(set(lows)) < len(lows):
            return True
        if len({s[:3] for s in lows}) < len(lows):
            return True
        if len({s[:4] for s in lows}) < len(lows):
            return True
        for i in range(len(lows)):
            for j in range(len(lows)):
                if i != j and lows[j].startswith(lows[i]):
                    return True
        return False

    if not _has_conflict(base_ids):
        return {n: lid for n, lid in zip(names, base_ids)}

    tags = "123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    result = {}
    used = set()
    for idx, (n, base) in enumerate(zip(names, base_ids)):
        tag = tags[idx] if idx < len(tags) else str(idx + 1)
        clean = re.sub(r"[^A-Za-z0-9_]", "", n) or "layout"
        max_body = finalize_track.MAX_LAYOUT_ID_LENGTH - len(tag)
        cand = tag + clean[:max_body]
        if cand.lower() in used:
            cand = finalize_track.layout_identifier(tag + "_" + n)
        used.add(cand.lower())
        result[n] = cand
    return result


def assign_rf1_display_names(selected, venue):
    """Assign unique, non-prefix VenueName values for multi-layout GTR2 .gdb files.

    GTR2's track list indexes layouts by VenueName and refuses to select a
    second layout if its VenueName duplicates or starts with an earlier layout's
    VenueName (e.g. ``Mainz-Finthen DTM`` vs ``Mainz-Finthen DTM wet``).
    """
    raw = []
    for lay in selected:
        g = _parse_rf1_gdb(lay["gdb"])
        tn = g.get("trackname") or lay["name"]
        vn = tn if len(selected) > 1 else (g.get("venuename") or tn)
        raw.append([lay["name"], vn])

    if len(raw) <= 1:
        return {n: v for n, v in raw}

    # First disambiguate exact duplicates.
    seen = {}
    for entry in raw:
        key = entry[1].casefold()
        if key in seen:
            entry[1] = "%s (%s)" % (entry[1], entry[0])
        seen[entry[1].casefold()] = True

    # Next ensure no VenueName is a prefix of another VenueName.
    for i in range(len(raw)):
        vi = raw[i][1]
        vi_l = vi.casefold()
        longer = [
            raw[j][1] for j in range(len(raw))
            if i != j and raw[j][1].casefold().startswith(vi_l)
        ]
        if longer:
            suffix = " (Dry)" if any("wet" in s.casefold() for s in longer) else " (Main)"
            cand = vi + suffix
            if any(cand.casefold() == raw[k][1].casefold() for k in range(len(raw)) if k != i):
                cand = "%s (%s)" % (vi, raw[i][0])
            raw[i][1] = cand

    return {n: v for n, v in raw}


def build_gtr2_gdb(rf1_gdb_path, venue, layout, tdf_path=None,
                   used_venue_names=None, max_vehicles=36,
                   venue_name_override=None):
    """Convert an rFactor 1 .gdb file into a complete GTR2 .gdb text."""
    g = _parse_rf1_gdb(rf1_gdb_path)
    grips = _parse_tdf_grips(tdf_path)

    road_dry = grips.get("road_dry", 1.00)
    road_wet = grips.get("road_wet", road_dry * 0.85)
    rumble_dry = grips.get("rumble_dry", 0.96)
    rumble_wet = grips.get("rumble_wet", rumble_dry * 0.85)

    qual_lap = max(20.0, _safe_float(g.get("qualify laptime"), 90.0))
    race_lap = max(20.0, _safe_float(g.get("race laptime"), qual_lap * 1.02))
    attrition = max(0, min(100, _safe_int(g.get("attrition"), 30)))
    race_laps = max(1, _safe_int(g.get("racelaps"), 20))
    race_time = max(10, _safe_int(g.get("racetime"), 60))

    race_start = g.get("racestart", "14:00")
    if not re.match(r"^\d{1,2}:\d{2}$", race_start):
        race_start = "14:00"

    track_name = venue_name_override or g.get("trackname") or layout
    event_name = venue_name_override or g.get("eventname") or track_name
    gp_name = venue_name_override or g.get("grandprixname") or event_name
    # GTR2's track selector indexes layouts by VenueName; two .gdb files sharing
    # the same VenueName (or prefix) cannot both be selected.
    if venue_name_override:
        venue_name = venue_name_override
    else:
        venue_name = track_name if used_venue_names is not None else (g.get("venuename") or track_name)
    if used_venue_names is not None:
        if venue_name.casefold() in used_venue_names:
            cand = "%s (%s)" % (g.get("venuename") or venue, layout)
            if cand.casefold() in used_venue_names:
                idx = 2
                while ("%s %d" % (cand, idx)).casefold() in used_venue_names:
                    idx += 1
                cand = "%s %d" % (cand, idx)
            venue_name = cand
        used_venue_names.add(venue_name.casefold())

    eff_max_vehicles = max(1, min(36, int(max_vehicles)))

    return RF1_GDB_TEMPLATE.format(
        layout=layout,
        venue=venue,
        hour="12:00",
        race_start=race_start,
        race_laps=race_laps,
        race_time=race_time,
        attrition=attrition,
        track_name=track_name,
        gp_name=gp_name,
        event_name=event_name,
        venue_name=venue_name,
        location=g.get("location") or "converted from rFactor 1",
        length=g.get("length") or "3.000 km",
        track_record=g.get("track record") or "N/A",
        qual_lap=qual_lap,
        race_lap=race_lap,
        road_dry=road_dry,
        ai_dry=min(1.15, road_dry * 1.02),
        road_wet=road_wet,
        ai_wet=min(1.10, road_wet * 1.02),
        rumble_dry=rumble_dry,
        rumble_wet=rumble_wet,
        shadow_min_sun=_safe_float(g.get("shadowminsunangle"), 15.0),
        sky_blend=g.get("skyblendsunangles") or "(-20.5, -1.0, 11.5, 25.5)",
        sunrise_amb=_clean_rgb_tuple(g.get("sunriseambientrgb") or g.get("sunriseambientcolour"), "(146,138,125)"),
        sunrise_sun=_clean_rgb_tuple(g.get("sunrisedirectionalrgb") or g.get("sunrisedirectionalcolour"), "(229,204,178)"),
        sunrise_fog=_clean_rgb_tuple(g.get("sunrisefogrgb") or g.get("sunrisefogcolour"), "(30,35,55)"),
        sunrise_fog_in=_safe_float(g.get("sunrisefogin"), 0.0),
        sunrise_fog_out=_safe_float(g.get("sunrisefogout"), 2150.0),
        day_amb=_clean_rgb_tuple(g.get("dayambientrgb") or g.get("dayambientcolour"), "(165,165,165)"),
        day_sun=_clean_rgb_tuple(g.get("daydirectionalrgb") or g.get("daydirectionalcolour"), "(255,255,255)"),
        day_fog=_clean_rgb_tuple(g.get("dayfogrgb") or g.get("dayfogcolour"), "(185,211,243)"),
        day_fog_in=_safe_float(g.get("dayfogin"), 250.0),
        day_fog_out=_safe_float(g.get("dayfogout"), 4500.0),
        sunset_amb=_clean_rgb_tuple(g.get("sunsetambientrgb") or g.get("sunsetambientcolour"), "(136,121,105)"),
        sunset_sun=_clean_rgb_tuple(g.get("sunsetdirectionalrgb") or g.get("sunsetdirectionalcolour"), "(220,190,150)"),
        sunset_fog=_clean_rgb_tuple(g.get("sunsetfogrgb") or g.get("sunsetfogcolour"), "(60,63,75)"),
        sunset_fog_in=_safe_float(g.get("sunsetfogin"), 100.0),
        sunset_fog_out=_safe_float(g.get("sunsetfogout"), 1700.0),
        night_amb=_clean_rgb_tuple(g.get("nightambientrgb") or g.get("nightambientcolour"), "(28,30,34)"),
        night_sun=_clean_rgb_tuple(g.get("nightdirectionalrgb") or g.get("nightdirectionalcolour"), "(15,16,17)"),
        night_fog=_clean_rgb_tuple(g.get("nightfogrgb") or g.get("nightfogcolour"), "(4,7,10)"),
        night_fog_in=_safe_float(g.get("nightfogin"), 220.0),
        night_fog_out=_safe_float(g.get("nightfogout"), 1900.0),
        latitude=_safe_float(g.get("latitude"), 35.0),
        north_dir=_safe_int(g.get("northdirection") or g.get("northdir"), 270),
        race_date=g.get("racedate") or "July 1",
        max_vehicles=eff_max_vehicles,
    )


def _extract_top_level_blocks(text):
    """Yield ``(kind, name, raw_block)`` for top-level named blocks & directives in a .scn."""
    lines = text.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i]
        stripped = _strip_comment(raw).strip()
        if not stripped:
            i += 1
            continue
        m = re.match(r"(?i)^(View|Light|Instance|ReflectionMapper)\s*=\s*(\S+)", stripped)
        if m:
            kind = m.group(1)
            name = m.group(2)
            block_lines = [raw]
            depth = stripped.count("{") - stripped.count("}")
            i += 1
            while i < n and (depth > 0 or (depth == 0 and not any("{" in _strip_comment(bl) for bl in block_lines))):
                cur = lines[i]
                c_strip = _strip_comment(cur)
                if depth == 0 and "{" not in c_strip:
                    if not c_strip.strip():
                        block_lines.append(cur)
                        i += 1
                        continue
                    # Directive without braces (rare), stop here.
                    break
                block_lines.append(cur)
                depth += c_strip.count("{") - c_strip.count("}")
                i += 1
                if depth <= 0 and any("{" in _strip_comment(bl) for bl in block_lines):
                    break
            yield kind, name, "\n".join(block_lines)
        else:
            yield "DIRECTIVE", "", raw
            i += 1


_DYNAMIC_TEX_CASTER_RE = re.compile(
    r"(?i)[ \t]*\bShadow(?:Caster|Object)\s*=\s*\(\s*Dynamic\s*,\s*Texture\b[^)]*\)"
)
_ANY_SHADOW_CASTER_RE = re.compile(
    r"(?i)([ \t]*)\b(Shadow(?:Caster|Object))\s*=\s*\(([^)]*)\)"
)
_SHADOW_RECEIVER_TRUE_RE = re.compile(
    r"(?i)\bShadowReceiver\s*=\s*True\b"
)


def _collect_gmt_metrics(gmt_dirs):
    """Read ``(radius, tris)`` from GTR2 GMT v2.10 files across ``gmt_dirs``.

    Later directories override earlier ones (so layout-specific ``gmt_<id>``
    overrides shared ``gmt/``).
    """
    metrics = {}
    for gd in gmt_dirs:
        if not gd or not os.path.isdir(gd):
            continue
        for fn in os.listdir(gd):
            low = fn.lower()
            if not low.endswith(".gmt"):
                continue
            path = os.path.join(gd, fn)
            try:
                with open(path, "rb") as f:
                    b = f.read()
            except OSError:
                continue
            if not gmt_gtr2.is_gtr2_gmt(b) or len(b) < gmt_gtr2.GTR2_HEADER_SIZE:
                continue
            radius = float(struct.unpack_from("<f", b, 0x018)[0])
            nmesh, moff_raw = struct.unpack_from("<2I", b, 0x16C)
            moff = moff_raw + 4
            tris = 0
            for mi in range(nmesh):
                pos = moff + mi * gmt_gtr2.GTR2_MESH_SIZE
                if pos + 40 > len(b):
                    break
                icnt = struct.unpack_from("<I", b, pos + 36)[0]
                tris += icnt // 3
            metrics[low] = (radius, tris)
    return metrics


def sanitize_texture_filename(name):
    """Replace whitespace runs in an rF1 texture filename with underscores."""
    if not any(ch.isspace() for ch in name):
        return name
    stem, ext = os.path.splitext(name)
    clean_stem = re.sub(r"\s+", "_", stem.strip()) or "tex"
    clean_ext = re.sub(r"\s+", "", ext)
    return (clean_stem[: max(1, 63 - len(clean_ext))] + clean_ext)


def convert_scn_to_trk(scn_path, venue, layout, available_gmts, skybox_block,
                       extra_search_paths=(), track_shadows="compact",
                       gmt_metrics=None):
    """Convert an rFactor 1 .scn file into a GTR2 .trk string.

    ``track_shadows`` controls GTR2 projected shadow handling:
      - ``compact`` (default): caps ``MaxShadowRange`` to 200 m, keeps
        ``ShadowReceiver=True`` only on rendered ``HATTarget=True`` road/ground
        surfaces, strips all ``Texture`` casters, and keeps only compact opaque
        ``Static, Solid`` casters (``radius <= 60 m``, ``<= 8000 tris``) sized
        to ``32x32`` (``<= 25 m``) or ``64x64`` (``<= 60 m``).
      - ``off``: strips all track shadow casters, caps ``MaxShadowRange`` to
        200 m, and keeps ``ShadowReceiver=True`` only on rendered
        ``HATTarget=True`` road/ground surfaces so cars still cast shadows.
      - ``full``: preserves authored static casters, receivers, and
        ``MaxShadowRange`` (``Dynamic, Texture`` casters are still stripped).

    Returns ``(trk_text, stats_dict)``.
    """
    mode = steam_paths.validate_track_shadows(track_shadows)
    metrics = gmt_metrics or {}

    with open(scn_path, encoding="utf-8", errors="replace") as f:
        text = f.read()

    search_lines = ["SearchPath=GAMEDATA\\LOCATIONS\\%s" % venue]
    for rel in extra_search_paths:
        search_lines.append("SearchPath=GAMEDATA\\LOCATIONS\\%s\\%s" % (venue, rel))
    search_lines.extend([
        "SearchPath=GAMEDATA\\LOCATIONS\\%s\\gmt" % venue,
        "SearchPath=GAMEDATA\\LOCATIONS\\%s\\maps" % venue,
        "SearchPath=GAMEDATA\\LOCATIONS\\SHARED",
    ])

    max_shadow_range = 250.0
    m_sr = re.search(r"(?i)MaxShadowRange\s*=?\s*\(\s*([0-9.]+)\s*\)", text)
    if m_sr:
        max_shadow_range = float(m_sr.group(1))
    if mode in ("compact", "off"):
        max_shadow_range = min(max_shadow_range, 200.0)

    header = (
        "CUBEASF\n"
        "// %s (%s), converted from rFactor 1\n\n"
        "%s\n\n"
        "MASFile=Skies_central.gtr\n"
        "MASFile=TeamMaps2.gtr\n"
        "MASFile=Misc.gtr\n\n"
        "View=mainview\n"
        "{\n"
        "  Clear=True\n"
        "  Color=(0, 0, 0)\n"
        "  Size=(1.0, 1.0) Center=(0.5, 0.5)\n"
        "  FOV=(77.75, 62.50)\n"
        "  ClipPlanes=(0.50, 15000.00)\n"
        "  View=rearview\n"
        "  {\n"
        "    Clear=True\n"
        "    Color=(149, 218, 254)\n"
        "    Size=(512.0, 256.0) Center=(256.0, 128.0)\n"
        "    FOV=(77.5, 62.5)\n"
        "    ClipPlanes=(0.50, 400.00)\n"
        "  }\n"
        "}\n\n"
        "GroupMethod=Dynamic\n"
        "MaxShadowRange=(%.1f)\n\n"
        "%s\n"
        % (venue, layout, "\n".join(search_lines), max_shadow_range, skybox_block)
    )

    used_instances = {
        "skyboxi", "day", "night", "sunrise", "sunset", "sun", "moon",
        "dayr", "nightr", "sunriser", "sunsetr",
    }
    kept_blocks = []
    stats = {
        "instances": 0,
        "skipped_missing": 0,
        "renamed_long": 0,
        "lights_kept": 0,
        "stripped_dynamic_casters": 0,
        "stripped_casters": 0,
        "stripped_receivers": 0,
        "shadow_casters": 0,
        "shadow_receivers": 0,
        "max_shadow_range": max_shadow_range,
        "track_shadows": mode,
    }

    for kind, name, blk in _extract_top_level_blocks(text):
        kl = kind.lower()
        if kl in ("directive", "view"):
            continue
        if kl == "light":
            # GTR2 drives directional sunlight from .gdb; drop directional lights
            # and dummy 0m/1m NightLight horizon triggers.
            if re.search(r"(?i)Type\s*=\s*Directional", blk):
                continue
            if re.search(r"(?i)Range\s*=\s*\(\s*0\.0+\s*,\s*(?:0\.0+|1\.0+)\s*\)", blk):
                continue
            kept_blocks.append(blk)
            stats["lights_kept"] += 1
            continue
        if kl == "reflectionmapper":
            kept_blocks.append(blk)
            continue
        if kl == "instance":
            if name.lower() == "skyboxi":
                continue
            mesh_refs = re.findall(r"(?i)\bMeshFile\s*=\s*(\S+)", blk)
            if mesh_refs and not all(m.lower() in available_gmts for m in mesh_refs):
                stats["skipped_missing"] += 1
                continue
            safe_name = finalize_track.instance_identifier(name, used_instances)
            if safe_name != name:
                stats["renamed_long"] += 1
                blk = re.sub(
                    r"(?i)^(\s*Instance\s*=\s*)(\S+)",
                    lambda mm: mm.group(1) + safe_name,
                    blk, count=1,
                )
            # Strip Dynamic, Texture ShadowCasters unconditionally (they force
            # per-frame alpha-test shadow map re-renders in GTR2).
            blk, n_stripped = _DYNAMIC_TEX_CASTER_RE.subn("", blk)
            if n_stripped:
                stats["stripped_dynamic_casters"] += n_stripped

            if mode in ("compact", "off"):
                is_hat_rendered = (
                    bool(re.search(r"(?i)\bHATTarget\s*=\s*True\b", blk))
                    and not bool(re.search(r"(?i)\bRender\s*=\s*False\b", blk))
                )
                if not is_hat_rendered:
                    blk, n_rec = _SHADOW_RECEIVER_TRUE_RE.subn("ShadowReceiver=False", blk)
                    stats["stripped_receivers"] += n_rec

                if mode == "off" or bool(re.search(r"(?i)\bMoveable\s*=\s*True\b", blk)):
                    blk, n_cast = _ANY_SHADOW_CASTER_RE.subn("", blk)
                    stats["stripped_casters"] += n_cast
                else:
                    max_r = max(
                        (metrics[m.lower()][0] for m in mesh_refs if m.lower() in metrics),
                        default=0.0,
                    )
                    total_tris = sum(
                        metrics[m.lower()][1] for m in mesh_refs if m.lower() in metrics
                    )

                    def _rewrite_compact_caster(mm):
                        lead, tag, arg_str = mm.group(1), mm.group(2), mm.group(3)
                        tokens = [t.strip().lower() for t in arg_str.split(",")]
                        if "texture" in tokens:
                            stats["stripped_casters"] += 1
                            return ""
                        if (max_r > finalize_track.COMPACT_SHADOW_MAX_RADIUS
                                or total_tris > finalize_track.COMPACT_SHADOW_MAX_TRIS):
                            stats["stripped_casters"] += 1
                            return ""
                        size = 32 if max_r <= 25.0 else 64
                        return "%s%s=(Static, Solid, %d, %d)" % (lead, tag, size, size)

                    blk = _ANY_SHADOW_CASTER_RE.sub(_rewrite_compact_caster, blk)

            stats["shadow_casters"] += len(_ANY_SHADOW_CASTER_RE.findall(blk))
            stats["shadow_receivers"] += len(_SHADOW_RECEIVER_TRUE_RE.findall(blk))

            # Also enforce 31-char limit on any nested Instance= declarations.
            lines_out = []
            for idx_l, ln in enumerate(blk.splitlines()):
                if idx_l > 0:
                    m_sub = re.match(r"(?i)^(\s*Instance\s*=\s*)(\S+)(.*)$", ln)
                    if m_sub:
                        sub_name = finalize_track.instance_identifier(m_sub.group(2), used_instances)
                        if sub_name != m_sub.group(2):
                            stats["renamed_long"] += 1
                        ln = m_sub.group(1) + sub_name + m_sub.group(3)
                if ln.strip() or idx_l == 0:
                    lines_out.append(ln.rstrip())
            kept_blocks.append("\n".join(lines_out))
            stats["instances"] += 1

    return header + "\n" + "\n\n".join(kept_blocks) + "\n", stats


def _find_rf1_menu_sources(layout_dir, asset_root, stem):
    """Locate best available loading, thumbnail, and track-map images for an rF1 layout."""
    def _pick(patterns):
        for folder in (layout_dir, asset_root):
            if not folder or not os.path.isdir(folder):
                continue
            try:
                names = sorted(os.listdir(folder))
            except OSError:
                continue
            for pat in patterns:
                for n in names:
                    if re.match(pat, n, re.IGNORECASE):
                        full = os.path.join(folder, n)
                        if os.path.isfile(full):
                            return full
        return None

    esc = re.escape(stem)
    loading = _pick([
        r"^%s_?loading\.(jpg|jpeg|png|bmp|tga)$" % esc,
        r".*_loading\.(jpg|jpeg|png|bmp|tga)$",
    ])
    thumb = _pick([
        r"^%sthmb\.(tga|jpg|jpeg|png|bmp)$" % esc,
        r".*thmb\.(tga|jpg|jpeg|png|bmp)$",
    ])
    mini = _pick([
        r"^%smini\.(tga|bmp|png|jpg|jpeg)$" % esc,
        r".*mini\.(tga|bmp|png|jpg|jpeg)$",
    ])
    return loading, thumb, mini


def _decode_image_to_bgra(path, texconv):
    """Decode jpg/png/bmp/tga image to BGRA uint8 array via texconv or TGA reader."""
    if not path or not os.path.isfile(path):
        return None
    if path.lower().endswith(".tga"):
        try:
            return finalize_track._read_tga_bgra(path)
        except Exception:
            pass
    if not texconv or not os.path.isfile(texconv):
        return None
    with tempfile.TemporaryDirectory(prefix="ac2gtr2-rf1-img-") as tmp:
        try:
            finalize_track._run_texconv(
                [texconv, "-y", "-ft", "tga", "-f", "B8G8R8A8_UNORM", "-m", "1",
                 "-o", tmp, path],
                "decoding %s" % os.path.basename(path),
            )
            tga_path = os.path.join(tmp, os.path.splitext(os.path.basename(path))[0] + ".tga")
            if os.path.isfile(tga_path):
                return finalize_track._read_tga_bgra(tga_path)
        except Exception:
            return None
    return None


def stage_rf1_menu_images(layout_dir, asset_root, stem, stage, layout, texconv):
    """Build GTR2's 4 UI images (_LOD.jpg, _LOD_2.jpg, Thmb.tga, Track.bmp) from rF1 images."""
    loading_src, thumb_src, mini_src = _find_rf1_menu_sources(layout_dir, asset_root, stem)
    if not any((loading_src, thumb_src, mini_src)):
        return 0
    if not texconv or not os.path.isfile(texconv):
        return 0

    load_bgra = _decode_image_to_bgra(loading_src or thumb_src or mini_src, texconv)
    thumb_bgra = _decode_image_to_bgra(thumb_src or loading_src or mini_src, texconv)
    mini_bgra = _decode_image_to_bgra(mini_src or thumb_src or loading_src, texconv)
    if load_bgra is None and thumb_bgra is None and mini_bgra is None:
        return 0

    lod_prefix = layout[:4]
    thmb_prefix = layout[:3]
    lod_jpg = os.path.join(stage, lod_prefix + "_LOD.jpg")
    lod2_jpg = os.path.join(stage, lod_prefix + "_LOD_2.jpg")
    thmb_tga = os.path.join(stage, thmb_prefix + "Thmb.tga")
    track_bmp = os.path.join(stage, layout + "Track.bmp")

    def _has_alpha(img):
        return bool((img[:, :, 3] < 250).any())

    written = 0
    with tempfile.TemporaryDirectory(prefix="ac2gtr2-rf1-ui-") as tmp:
        if load_bgra is not None:
            lod_canvas = finalize_track._compose_on_canvas(
                load_bgra, _has_alpha(load_bgra),
                finalize_track.LOD_SIZE, finalize_track.LOD_SIZE,
                texconv, tmp, "lod",
            )
            finalize_track._write_jpeg_via_texconv(lod_canvas, lod_jpg, texconv, tmp, "lod")
            written += 1

            lod2_canvas = finalize_track._loading_background(*finalize_track.LOD2_SIZE)
            finalize_track._write_jpeg_via_texconv(lod2_canvas, lod2_jpg, texconv, tmp, "lod2")
            written += 1

        if thumb_bgra is not None:
            thmb_canvas = finalize_track._compose_on_canvas(
                thumb_bgra, _has_alpha(thumb_bgra),
                finalize_track.THMB_SIZE, finalize_track.THMB_SIZE,
                texconv, tmp, "thmb",
            )
            finalize_track._write_tga_bgra(thmb_tga, thmb_canvas, bottom_up=True)
            written += 1

        if mini_bgra is not None:
            bmp_canvas = finalize_track._compose_on_canvas(
                mini_bgra, _has_alpha(mini_bgra),
                finalize_track.TRACK_BMP_SIZE, finalize_track.TRACK_BMP_FIT,
                texconv, tmp, "track", transparent_bg=_has_alpha(mini_bgra),
            )
            finalize_track._write_tga_bgra(track_bmp, bmp_canvas, bottom_up=True)
            written += 1

    return written


def _write_clear_dxt5(path):
    """Write a minimal 4x4 fully transparent DX9 DXT5 texture (for skyboxi.gmt)."""
    h = bytearray(128)
    h[0:4] = b"DDS "
    struct.pack_into("<I", h, 4, 124)
    struct.pack_into("<I", h, 8, 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000)
    struct.pack_into("<I", h, 12, 4)
    struct.pack_into("<I", h, 16, 4)
    struct.pack_into("<I", h, 20, 16)
    struct.pack_into("<I", h, 28, 1)
    struct.pack_into("<I", h, 76, 32)
    struct.pack_into("<I", h, 80, 0x4)
    h[84:88] = b"DXT5"
    struct.pack_into("<I", h, 108, 0x1000)
    with open(path, "wb") as f:
        f.write(bytes(h))
        f.write(bytes(16))


def _write_solid_dxt1(path, rgb):
    """Write a minimal 4x4 valid DX9 DXT1 texture for missing rF1 COMMONMAPS fallbacks."""
    r, g, b = rgb
    c = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
    h = bytearray(128)
    h[0:4] = b"DDS "
    struct.pack_into("<I", h, 4, 124)
    struct.pack_into("<I", h, 8, 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000)
    struct.pack_into("<I", h, 12, 4)
    struct.pack_into("<I", h, 16, 4)
    struct.pack_into("<I", h, 20, 8)
    struct.pack_into("<I", h, 28, 1)
    struct.pack_into("<I", h, 76, 32)
    struct.pack_into("<I", h, 80, 0x4)
    h[84:88] = b"DXT1"
    struct.pack_into("<I", h, 108, 0x1000)
    with open(path, "wb") as f:
        f.write(bytes(h))
        f.write(struct.pack("<HHI", c, c, 0))


def _gtr2_shared_textures(gtr2_dir, masunpack):
    """Collect texture basenames already supplied by GTR2 SHARED GTR archives."""
    shared = set()
    if not gtr2_dir:
        return shared
    shared_dir = os.path.join(gtr2_dir, "GameData", "Locations", "SHARED")
    if not os.path.isdir(shared_dir):
        return shared
    for name in os.listdir(shared_dir):
        low = name.lower()
        full = os.path.join(shared_dir, name)
        if not os.path.isfile(full):
            continue
        if os.path.splitext(low)[1] in TEXTURE_EXTS:
            shared.add(low)
        elif low in ("misc.gtr", "teammaps2.gtr", "skies_central.gtr"):
            shared.update(list_mas_entries(full, masunpack=masunpack))
    return shared


def _gmt_texture_refs(gmt_path):
    """Return list of non-MATH texture filenames referenced by a GTR2 GMT v2.10.

    For animated textures (``anim_frames > 1``), expands ``name.ext`` into its
    numbered frame filenames (``name00.ext .. name<N-1>.ext``) so missing frames
    are recovered from shared archives or synthesized.
    """
    with open(gmt_path, "rb") as f:
        b = f.read()
    if not gmt_gtr2.is_gtr2_gmt(b) or len(b) < gmt_gtr2.GTR2_HEADER_SIZE:
        return []
    mat_off = struct.unpack_from("<I", b, 0)[0] + 4
    if mat_off + 4 > len(b):
        return []
    nmat = struct.unpack_from("<I", b, mat_off)[0]
    pos = mat_off + 4
    refs = []
    for _ in range(nmat):
        need = (gmt_gtr2.GTR2_MAT_HEADER_SIZE + 3 * gmt_gtr2.GTR2_SHADER_INFO_SIZE
                + gmt_gtr2.GTR2_MAT_POST_SHADERS_PAD + 4)
        if pos + need > len(b):
            break
        pos += need - 4
        ntex = struct.unpack_from("<I", b, pos)[0]
        pos += 4
        for _ in range(ntex):
            if pos + 80 > len(b):
                break
            sz = gmt_gtr2.tex_record_size(b, pos)
            if pos + sz > len(b):
                break
            tname = b[pos:pos + 64].split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()
            anim_frames = struct.unpack_from("<I", b, pos + 72)[0]
            if tname and tname.upper() != "MATH":
                if anim_frames > 1:
                    seq_len = struct.unpack_from("<I", b, pos + 76)[0]
                    seq_max = 0
                    if 0 < seq_len <= 100 and pos + 80 + 4 * seq_len <= len(b):
                        seq_vals = struct.unpack_from("<%dI" % seq_len, b, pos + 80)
                        seq_max = max(seq_vals) if seq_vals else 0
                    n_frames = min(100, max(anim_frames, seq_max + 1))
                    stem, ext = os.path.splitext(tname)
                    for fi in range(n_frames):
                        refs.append("%s%02d%s" % (stem, fi, ext))
                else:
                    refs.append(tname)
            pos += sz
    return refs


def _disambiguate_rf1_materials(gmt_dirs, trk_texts):
    """Prevent GTR2 'Material global/local mismatch' errors across scene GMTs.

    In GTR2, ``Moveable=True`` instances and ``Change=False`` instances (such as
    invisible timing/pit gates) have different material global/local semantics
    from normal static instances. If the same material name appears across
    incompatible instance groups (e.g. ``metalpole`` in static markers and
    moveable balloons, or ``RDLTA`` in ``Startglow`` and ``Change=False``
    ``xpitin``), GTR2 aborts loading with ``Material global/local mismatch``.
    Likewise, two static GMTs defining the same material name with conflicting
    shaders or flags are given distinct suffixed names (preserving surface
    prefixes).

    Material names are rewritten in-place in the fixed 64-byte GMT header slot.
    Returns ``(local_group_renamed, variant_renamed)``.
    """
    moveable_gmts = set()
    nochange_gmts = set()
    static_gmts = set()
    for text in trk_texts:
        for kind, name, blk in _extract_top_level_blocks(text):
            if kind.lower() != "instance" or name.lower() == "skyboxi":
                continue
            meshes = {m.lower() for m in re.findall(r"(?i)\bMeshFile\s*=\s*(\S+)", blk)}
            if re.search(r"(?i)\bMoveable\s*=\s*True\b", blk):
                moveable_gmts |= meshes
            elif re.search(r"(?i)\bChange\s*=\s*False\b", blk):
                nochange_gmts |= meshes
            else:
                static_gmts |= meshes

    gmt_files = []
    for gd in gmt_dirs:
        if not gd or not os.path.isdir(gd):
            continue
        for fn in sorted(os.listdir(gd)):
            low = fn.lower()
            if low.endswith(".gmt") and not _is_skybox_gmt(low):
                gmt_files.append((low, os.path.join(gd, fn)))

    def _scan_mats(b):
        if not gmt_gtr2.is_gtr2_gmt(b) or len(b) < gmt_gtr2.GTR2_HEADER_SIZE:
            return []
        mat_off = struct.unpack_from("<I", b, 0)[0] + 4
        if mat_off + 4 > len(b):
            return []
        nmat = struct.unpack_from("<I", b, mat_off)[0]
        pos = mat_off + 4
        out = []
        for _ in range(nmat):
            need = (gmt_gtr2.GTR2_MAT_HEADER_SIZE + 3 * gmt_gtr2.GTR2_SHADER_INFO_SIZE
                    + gmt_gtr2.GTR2_MAT_POST_SHADERS_PAD + 4)
            if pos + need > len(b):
                break
            mname = b[pos:pos + 64].split(b"\x00", 1)[0].decode("ascii", errors="replace")
            mfl = struct.unpack_from("<I", b, pos + 64)[0]
            shs = []
            for lv in range(3):
                sp = pos + gmt_gtr2.GTR2_MAT_HEADER_SIZE + lv * gmt_gtr2.GTR2_SHADER_INFO_SIZE
                shs.append(b[sp + 8:sp + 72].split(b"\x00", 1)[0].decode("ascii", errors="replace").upper())
            ntex_pos = pos + need - 4
            ntex = struct.unpack_from("<I", b, ntex_pos)[0]
            out.append((pos, mname, (mfl, shs[0], shs[1], shs[2], ntex)))
            pos = ntex_pos + 4
            for _ in range(ntex):
                if pos + 80 > len(b):
                    break
                pos += gmt_gtr2.tex_record_size(b, pos)
        return out

    all_used_names = set()
    static_mat_names = set()
    static_mat_sigs = {}  # mname_low -> {sig: assigned_name}

    for low, path in gmt_files:
        try:
            with open(path, "rb") as f:
                b = f.read()
        except OSError:
            continue
        mats = _scan_mats(b)
        for _pos, mname, sig in mats:
            ml = mname.lower()
            all_used_names.add(ml)
            if low in static_gmts:
                static_mat_names.add(ml)
                sigs = static_mat_sigs.setdefault(ml, {})
                if sig not in sigs:
                    sigs[sig] = mname

    def _alloc_name(base_name, suffix):
        cand = base_name[:max(1, 63 - len(suffix))] + suffix
        idx = 2
        while cand.lower() in all_used_names:
            alt = "%s%d" % (suffix, idx)
            cand = base_name[:max(1, 63 - len(alt))] + alt
            idx += 1
        all_used_names.add(cand.lower())
        return cand

    # Assign unique names to conflicting static variants (same name, different shader/flags).
    static_variant_map = {}  # (mname_low, sig) -> new_name
    for ml, sigs in static_mat_sigs.items():
        if len(sigs) <= 1:
            continue
        for idx, (sig, orig_name) in enumerate(sigs.items()):
            if idx == 0:
                continue
            static_variant_map[(ml, sig)] = _alloc_name(orig_name, "_v%d" % (idx + 1))

    moveable_map = {}   # mname_low -> new_name
    nochange_map = {}   # mname_low -> new_name
    grp_renamed = 0
    var_renamed = 0

    for low, path in gmt_files:
        is_mov_only = (low in moveable_gmts) and (low not in static_gmts)
        is_nc_only = (low in nochange_gmts) and (low not in static_gmts) and (low not in moveable_gmts)
        is_static = (low in static_gmts)
        if not is_mov_only and not is_nc_only and not is_static:
            continue
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except OSError:
            continue
        mats = _scan_mats(raw)
        buf = None
        for mpos, mname, sig in mats:
            ml = mname.lower()
            new_name = None
            if is_mov_only and ml in static_mat_names:
                if ml not in moveable_map:
                    moveable_map[ml] = _alloc_name(mname, "_m")
                new_name = moveable_map[ml]
                grp_renamed += 1
            elif is_nc_only and ml in static_mat_names:
                if ml not in nochange_map:
                    nochange_map[ml] = _alloc_name(mname, "_nc")
                new_name = nochange_map[ml]
                grp_renamed += 1
            elif is_static and (ml, sig) in static_variant_map:
                new_name = static_variant_map[(ml, sig)]
                var_renamed += 1
            if new_name and new_name != mname:
                if buf is None:
                    buf = bytearray(raw)
                buf[mpos:mpos + 64] = gmt_gtr2._pad_ascii(new_name, 64)
        if buf is not None:
            with open(path, "wb") as f:
                f.write(buf)

    return grp_renamed, var_renamed


def _make_tex_resolver(tex_files_lower, stem_to_name, renamed_maps=None):
    """Resolve GMT texture refs to actual sanitized filenames on disk (rF1 stem matching)."""
    renamed = renamed_maps or {}

    def _resolve(tname):
        tl = tname.lower()
        if tl in renamed:
            return renamed[tl]
        safe = sanitize_texture_filename(tname)
        safe_l = safe.lower()
        if safe_l in tex_files_lower:
            return safe
        stem = os.path.splitext(safe_l)[0]
        if stem in stem_to_name:
            return stem_to_name[stem]
        return safe
    return _resolve


def _fix_invalid_dds(map_dir, texconv, jobs=1):
    """Re-encode only DDS files GTR2 cannot load (scale=1.0, never downscales)."""
    if not os.path.isdir(map_dir) or not texconv or not os.path.isfile(texconv):
        return 0
    bad = []
    for name in sorted(os.listdir(map_dir)):
        if not name.lower().endswith(".dds"):
            continue
        path = os.path.join(map_dir, name)
        info = fix_textures.describe(path)
        if info is None:
            bad.append(path)
            continue
        w, h, fourcc, _bpp, mips = info
        with open(path, "rb") as f:
            head = f.read(128)
        caps2 = struct.unpack_from("<I", head, 112)[0] if len(head) >= 128 else 0
        is_cube = bool(caps2 & 0x200)
        if is_cube:
            continue
        exp = fix_textures.expected_dds_bytes(w, h, fourcc, mips)
        actual = os.path.getsize(path)
        if (not fix_textures.is_pow2(w) or not fix_textures.is_pow2(h)
                or fourcc not in fix_textures.BLOCK
                or (exp is not None and actual != exp)):
            bad.append(path)
    if not bad:
        return 0
    fixed = 0
    with tempfile.TemporaryDirectory(prefix="ac2gtr2-rf1-dds-") as tmp:
        for path in bad:
            info = fix_textures.describe(path)
            tw, th = (fix_textures.pow2_size(info[0]), fix_textures.pow2_size(info[1])) if info else (None, None)
            fmt = fix_textures.SAME_FORMAT.get(info[2], "BC3_UNORM") if info else "BC3_UNORM"
            cmd = [texconv, "-y", "-ft", "dds", "-f", fmt, "-sepalpha", "-o", tmp]
            if tw and th:
                cmd += ["-w", str(tw), "-h", str(th)]
            cmd.append(path)
            res = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
            out_p = os.path.join(tmp, os.path.splitext(os.path.basename(path))[0] + ".dds")
            if res.returncode == 0 and os.path.isfile(out_p):
                shutil.copy2(out_p, path)
                fixed += 1
    return fixed


def convert_rf1_track(track_dir, layout=None, install=None, masunpack=None,
                      rfactorcrypt=None, texconv=None, gtr2_dir=None, jobs=1,
                      track_shadows="compact", source_type=None,
                      sce_common_dirs=None):
    """Convert an rFactor 1 or Stock Car Extreme track directory into GTR2."""
    track_shadows = steam_paths.validate_track_shadows(track_shadows)
    track_dir = os.path.abspath(track_dir)
    mas_tool = masunpack or steam_paths.MASUNPACK
    crypt_tool = rfactorcrypt or steam_paths.RFACTORCRYPT
    tex_tool = texconv or steam_paths.TEXCONV
    game_dir = gtr2_dir or steam_paths.GTR2_DIR
    src_type = source_type or detect_track_type(track_dir) or "rf1"
    src_label = "Stock Car Extreme" if src_type == "sce" else "rFactor 1"

    asset_root, all_layouts = find_rf1_layouts(track_dir)
    if not all_layouts:
        raise SystemExit("no %s layouts (.scn + .gdb) found in %s" % (src_label, track_dir))

    if layout:
        chosen = [l for l in all_layouts if l["name"].casefold() == layout.casefold()]
        if not chosen:
            avail = ", ".join(l["name"] for l in all_layouts)
            raise SystemExit(
                "%s track has no layout %r (available: %s)" % (src_label, layout, avail)
            )
        selected = chosen
    else:
        selected = all_layouts

    venue_base = os.path.basename(os.path.normpath(asset_root))
    raw_work = "%s_%s" % (venue_base, layout) if layout else venue_base
    # GTR2 SearchPath / SettingsFolder tokens cannot safely contain spaces.
    work_name = re.sub(r"\s+", "_", raw_work.strip()) or "track"
    out_dir = os.path.join(steam_paths.WORK_ROOT, work_name)
    stage = os.path.join(out_dir, "gtr2", work_name)
    if os.path.isdir(stage):
        shutil.rmtree(stage)
    gmts_dir = os.path.join(stage, "gmt")
    maps_dir = os.path.join(stage, "maps")
    for d in (out_dir, stage, gmts_dir, maps_dir):
        os.makedirs(d, exist_ok=True)

    print("\nconverting %s track %s (%d layout%s: %s)"
          % (src_label, work_name, len(selected), "" if len(selected) == 1 else "s",
             ", ".join(l["name"] for l in selected)))
    print("   options  track_shadows=%s (other AC-specific conversion options are ignored for %s)"
          % (track_shadows, src_label))

    layout_ids = assign_rf1_layout_ids(selected)
    venue_display_names = assign_rf1_display_names(selected, work_name)

    # 1. Determine which MAS files are shared vs layout-specific.
    print("\n[1/4] unpacking MAS archives and collecting assets")
    layout_mas = {}
    mas_usage = {}
    all_scn_mas_refs = []
    for lay in selected:
        refs = parse_scn_mas_files(lay["scn"])
        all_scn_mas_refs.extend(refs)
        resolved = []
        for mname in refs:
            if _is_skipped_mas(mname.lower()):
                continue
            p = _resolve_case_insensitive((lay["dir"], asset_root), mname)
            if p:
                resolved.append(p)
                key = os.path.normcase(p)
                mas_usage[key] = mas_usage.get(key, 0) + 1
        layout_mas[lay["name"]] = resolved

    shared_mas_keys = {
        k for k, cnt in mas_usage.items()
        if cnt == len(selected) or len(selected) == 1
    }

    unpacked_cache = {}
    with tempfile.TemporaryDirectory(prefix="ac2gtr2-rf1-mas-") as tmp_mas:
        # Unpack every needed MAS once.
        idx = 0
        for lay in selected:
            for p in layout_mas[lay["name"]]:
                key = os.path.normcase(p)
                if key in unpacked_cache:
                    continue
                idx += 1
                dest = os.path.join(tmp_mas, "m%02d_%s" % (idx, os.path.splitext(os.path.basename(p))[0]))
                unpack_mas(p, dest, masunpack=mas_tool)
                n_files = sum(1 for _ in os.listdir(dest))
                print("   unpacked %-28s (%d files)" % (os.path.basename(p), n_files))
                unpacked_cache[key] = dest

        # Copy shared MAS assets into stage/gmt and stage/maps in reverse priority
        # order so earlier MASFile= entries override later ones (rF1 search order).
        primary_mas = layout_mas[selected[0]["name"]]
        shared_ordered = []
        seen_k = set()
        for p in primary_mas:
            k = os.path.normcase(p)
            if k in shared_mas_keys and k not in seen_k:
                shared_ordered.append(p)
                seen_k.add(k)
        for lay in selected[1:]:
            for p in layout_mas[lay["name"]]:
                k = os.path.normcase(p)
                if k in shared_mas_keys and k not in seen_k:
                    shared_ordered.append(p)
                    seen_k.add(k)

        shared_gmts = {}   # lower -> actual basename in gmts_dir
        shared_maps = {}   # sanitized lower -> sanitized basename in maps_dir
        renamed_maps = {}  # original lower -> sanitized basename
        claimed_by = {}    # sanitized lower -> original lower

        def _safe_tex_name(orig_name):
            orig_low = orig_name.lower()
            if orig_low in renamed_maps:
                return renamed_maps[orig_low]
            safe = sanitize_texture_filename(orig_name)
            safe_low = safe.lower()
            if safe_low in claimed_by and claimed_by[safe_low] != orig_low:
                stem, ext = os.path.splitext(safe)
                n = 2
                while ("%s_%d%s" % (stem, n, ext)).lower() in claimed_by:
                    n += 1
                safe = "%s_%d%s" % (stem, n, ext)
                safe_low = safe.lower()
            claimed_by[safe_low] = orig_low
            if safe != orig_name:
                renamed_maps[orig_low] = safe
            return safe

        def _stage_file(src, g_dst, m_dst, g_map, m_map):
            name = os.path.basename(src)
            low = name.lower()
            ext = os.path.splitext(low)[1]
            if ext == ".gmt":
                # Do not copy rF1 skybox GMTs (day.gmt, night.gmt, sunrise.gmt,
                # sunset.gmt, horizon.gmt, sky.gmt, skyboxi.gmt); they shadow
                # GTR2's SHARED skybox meshes and reference rF1 sky textures.
                if _is_skybox_gmt(low):
                    return
                shutil.copy2(src, os.path.join(g_dst, name))
                g_map[low] = name
            elif ext in TEXTURE_EXTS:
                safe = _safe_tex_name(name)
                shutil.copy2(src, os.path.join(m_dst, safe))
                m_map[safe.lower()] = safe

        for p in reversed(shared_ordered):
            src_dir = unpacked_cache[os.path.normcase(p)]
            for fn in sorted(os.listdir(src_dir)):
                fp = os.path.join(src_dir, fn)
                if os.path.isfile(fp):
                    _stage_file(fp, gmts_dir, maps_dir, shared_gmts, shared_maps)

        # Loose assets in asset_root (override shared MAS).
        for fn in sorted(os.listdir(asset_root)):
            fp = os.path.join(asset_root, fn)
            if os.path.isfile(fp):
                _stage_file(fp, gmts_dir, maps_dir, shared_gmts, shared_maps)

        # Layout-specific MAS & loose layout folder files.
        layout_extra_paths = {}
        layout_gmts = {}
        layout_maps = {}
        for lay in selected:
            lname = lay["name"]
            lay_id = layout_ids[lname]
            lay_g_rel = "gmt_" + lay_id
            lay_m_rel = "maps_" + lay_id
            lay_g_dir = os.path.join(stage, lay_g_rel)
            lay_m_dir = os.path.join(stage, lay_m_rel)
            lg_map = {}
            lm_map = {}

            # Collect layout-only sources (layout-only MAS in reverse order, then layout dir).
            extra_mas = [p for p in layout_mas[lname] if os.path.normcase(p) not in shared_mas_keys]
            sources = []
            for p in reversed(extra_mas):
                sd = unpacked_cache[os.path.normcase(p)]
                for fn in sorted(os.listdir(sd)):
                    fp = os.path.join(sd, fn)
                    if os.path.isfile(fp):
                        sources.append(fp)
            for fn in sorted(os.listdir(lay["dir"])):
                fp = os.path.join(lay["dir"], fn)
                if os.path.isfile(fp):
                    # Skip UI images that are handled separately as menu images.
                    low = fn.lower()
                    if low.endswith(("_loading.jpg", "mini.tga", "thmb.tga")):
                        continue
                    sources.append(fp)

            for fp in sources:
                fn = os.path.basename(fp)
                low = fn.lower()
                ext = os.path.splitext(low)[1]
                if ext == ".gmt":
                    if _is_skybox_gmt(low):
                        continue
                    if len(selected) == 1 or low not in shared_gmts:
                        shutil.copy2(fp, os.path.join(gmts_dir, fn))
                        shared_gmts[low] = fn
                    else:
                        os.makedirs(lay_g_dir, exist_ok=True)
                        shutil.copy2(fp, os.path.join(lay_g_dir, fn))
                        lg_map[low] = fn
                elif ext in TEXTURE_EXTS:
                    safe = _safe_tex_name(fn)
                    safe_low = safe.lower()
                    if len(selected) == 1 or safe_low not in shared_maps:
                        shutil.copy2(fp, os.path.join(maps_dir, safe))
                        shared_maps[safe_low] = safe
                    else:
                        os.makedirs(lay_m_dir, exist_ok=True)
                        shutil.copy2(fp, os.path.join(lay_m_dir, safe))
                        lm_map[safe_low] = safe

            extras = []
            if lg_map:
                extras.append(lay_g_rel)
            if lm_map:
                extras.append(lay_m_rel)
            layout_extra_paths[lname] = extras
            layout_gmts[lname] = lg_map
            layout_maps[lname] = lm_map

    print("   collected %d GMTs, %d textures/media" % (len(shared_gmts), len(shared_maps)))
    if renamed_maps:
        print("   sanitized spaces in %d texture filenames" % len(renamed_maps))

    # 2. Decrypt any encrypted textures, validate/fix non-DX9 DDS textures (scale=1.0).
    print("\n[2/4] checking textures and converting GMTs to GTR2 v2.10")
    all_map_dirs = [maps_dir] + [
        os.path.join(stage, rel)
        for extras in layout_extra_paths.values()
        for rel in extras if rel.startswith("maps_")
    ]
    n_dec_tex = decrypt_sce_textures(all_map_dirs, rfactorcrypt=crypt_tool)
    if n_dec_tex:
        print("   decrypted %d Stock Car Extreme texture file(s)" % n_dec_tex)
    fixed_dds = 0
    for md in all_map_dirs:
        fixed_dds += _fix_invalid_dds(md, tex_tool, jobs=jobs)
    if fixed_dds:
        print("   re-encoded %d non-DX9 DDS textures (full resolution)" % fixed_dds)

    # Build texture lookup (exact + stem matching) for GMT material texture resolution.
    all_tex_lower = set(shared_maps.keys())
    stem_to_name = {}
    for low, actual in shared_maps.items():
        stem = os.path.splitext(low)[0]
        # Prefer .dds over .tga/.bmp when stems collide.
        if stem not in stem_to_name or low.endswith(".dds"):
            stem_to_name[stem] = actual
    for lm in layout_maps.values():
        for low, actual in lm.items():
            all_tex_lower.add(low)
            stem = os.path.splitext(low)[0]
            if stem not in stem_to_name or low.endswith(".dds"):
                stem_to_name[stem] = actual

    resolver = _make_tex_resolver(all_tex_lower, stem_to_name, renamed_maps=renamed_maps)

    # Decrypt any encrypted Stock Car Extreme GMTs before v2.00 -> v2.10 conversion.
    all_gmt_dirs = [gmts_dir] + [
        os.path.join(stage, rel)
        for extras in layout_extra_paths.values()
        for rel in extras if rel.startswith("gmt_")
    ]
    all_gmt_paths = [
        os.path.join(gd, fn)
        for gd in all_gmt_dirs
        for fn in sorted(os.listdir(gd))
        if fn.lower().endswith(".gmt")
    ]
    n_dec = decrypt_sce_gmts(all_gmt_paths, rfactorcrypt=crypt_tool)
    if n_dec:
        print("   decrypted %d Stock Car Extreme GMT files" % n_dec)
        if src_type == "rf1":
            src_type = "sce"

    # Convert every GMT to GTR2 GMT v2.10 (preserving all geometry/faces/materials).
    n_conv = 0
    for path in all_gmt_paths:
        gmt_gtr2.convert_file(
            path, remap_surfaces=True, tex_resolver=resolver,
        )
        with open(path, "rb") as f:
            head = f.read(gmt_gtr2.GTR2_MAGIC_OFFSET + len(gmt_gtr2.GTR2_GMT_MAGIC))
        if not gmt_gtr2.is_gtr2_gmt(head):
            raise RuntimeError("GTR2 GMT v2.10 verification failed for %s" % path)
        n_conv += 1
    print("   converted %d GMT files to GTR2 v2.10" % n_conv)

    # Collect missing texture references across all converted GMTs.
    gtr2_shared = _gtr2_shared_textures(game_dir, mas_tool)

    def _find_missing_refs():
        missing = {}
        for gd in all_gmt_dirs:
            for fn in sorted(os.listdir(gd)):
                low_g = fn.lower()
                if not low_g.endswith(".gmt") or _is_skybox_gmt(low_g):
                    continue
                for tref in _gmt_texture_refs(os.path.join(gd, fn)):
                    tl = tref.lower()
                    if tl in all_tex_lower or tl in gtr2_shared:
                        continue
                    stem = os.path.splitext(tl)[0]
                    if any(
                        k == stem + ".dds"
                        or (k.startswith(stem) and k.endswith((".dds", ".tga"))
                            and k[len(stem):len(stem) + 2].isdigit())
                        for k in all_tex_lower
                    ):
                        continue
                    missing.setdefault(tl, tref)
        return missing

    missing_map = _find_missing_refs()
    # For SCE/Reiza tracks (or any track referencing shared Reiza assets), recover
    # referenced missing textures from installed Stock Car Extreme / Automobilista
    # ReizaCommon and commonmaps.MAS before synthesizing fallbacks.
    if missing_map:
        common_dirs = (
            list(sce_common_dirs)
            if sce_common_dirs is not None
            else (steam_paths.find_sce_common_dirs() if src_type == "sce" else [])
        )
        if common_dirs:
            recovered, n_rec = _recover_reiza_common_textures(
                list(missing_map.values()), maps_dir, common_dirs,
                preferred_mas=all_scn_mas_refs, masunpack=mas_tool,
            )
            if n_rec:
                all_tex_lower.update(recovered)
                fixed_rec = _fix_invalid_dds(maps_dir, tex_tool, jobs=jobs)
                extra = " (%d re-encoded to DX9)" % fixed_rec if fixed_rec else ""
                print("   recovered %d shared texture(s) from ReizaCommon/commonmaps%s"
                      % (n_rec, extra))
                missing_map = _find_missing_refs()

    # Synthesize tiny neutral fallback DDS for any remaining missing COMMONMAPS
    # texture not supplied by GTR2 SHARED or ReizaCommon archives.
    fallback_made = []
    for tl, tref in sorted(missing_map.items()):
        out_tref = tref if tl.endswith(".dds") else (os.path.splitext(tref)[0] + ".dds")
        out_tl = out_tref.lower()
        if out_tl in all_tex_lower:
            continue
        if "_n" in tl or "_b." in tl or "bump" in tl or "norm" in tl:
            rgb = (128, 128, 255)
        elif "_s" in tl or "spec" in tl:
            rgb = (0, 0, 0)
        else:
            rgb = (128, 128, 128)
        _write_solid_dxt1(os.path.join(maps_dir, out_tref), rgb)
        all_tex_lower.add(out_tl)
        fallback_made.append(out_tref)
    if fallback_made:
        print("   synthesized %d missing COMMONMAPS fallback texture(s): %s"
              % (len(fallback_made), ", ".join(sorted(fallback_made)[:8])))

    # Stage native GTR2 skybox (skyboxi.gmt references _invisible.dds, so write
    # _invisible.dds into maps/ right alongside it).
    skybox_block = finalize_track.stage_skybox(gmts_dir)
    if "_invisible.dds" not in all_tex_lower:
        _write_clear_dxt5(os.path.join(maps_dir, "_invisible.dds"))
        all_tex_lower.add("_invisible.dds")

    # 3. Convert .scn -> .trk, .gdb -> .gdb, copy .AIW/.cam, generate .grd & menu images.
    print("\n[3/4] building GTR2 scene, database, AIW, cameras, and menu images")
    shared_metrics = _collect_gmt_metrics([gmts_dir])
    manifest_layouts = []
    used_venue_names = set()
    generated_trks = []
    for lay in selected:
        lname = lay["name"]
        lay_id = layout_ids[lname]
        avail_gmts = set(shared_gmts.keys()) | set(layout_gmts[lname].keys()) | SKYBOX_GMTS
        lay_metrics = dict(shared_metrics)
        lay_gmt_dir = os.path.join(stage, "gmt_" + lay_id)
        if os.path.isdir(lay_gmt_dir):
            lay_metrics.update(_collect_gmt_metrics([lay_gmt_dir]))
        trk_text, trk_stats = convert_scn_to_trk(
            lay["scn"], work_name, lay_id, avail_gmts, skybox_block,
            extra_search_paths=layout_extra_paths[lname],
            track_shadows=track_shadows,
            gmt_metrics=lay_metrics,
        )
        generated_trks.append(trk_text)
        finalize_track.write(os.path.join(stage, lay_id + ".trk"), trk_text)

        has_aiw = bool(lay.get("aiw") and os.path.isfile(lay["aiw"]))
        aiw_info = {}
        if has_aiw:
            aiw_info = sanitize_rf1_aiw(lay["aiw"], os.path.join(stage, lay_id + ".AIW"))
        max_vehicles = aiw_info.get("grid_spots") or 36
        if aiw_info.get("team_spots") and aiw_info.get("garage_spots"):
            max_vehicles = min(max_vehicles, aiw_info["team_spots"] * aiw_info["garage_spots"])

        # Locate .tdf referenced by .gdb or layout/asset root.
        g_meta = _parse_rf1_gdb(lay["gdb"])
        tdf_path = lay.get("tdf")
        if g_meta.get("terraindatafile"):
            cand_tdf = os.path.normpath(os.path.join(lay["dir"], g_meta["terraindatafile"]))
            if os.path.isfile(cand_tdf):
                tdf_path = cand_tdf
            else:
                tdf_path = _resolve_case_insensitive(
                    (lay["dir"], asset_root), os.path.basename(g_meta["terraindatafile"])
                ) or tdf_path
        gdb_text = build_gtr2_gdb(
            lay["gdb"], work_name, lay_id, tdf_path=tdf_path,
            used_venue_names=used_venue_names, max_vehicles=max_vehicles,
            venue_name_override=venue_display_names[lname],
        )
        finalize_track.write(os.path.join(stage, lay_id + ".gdb"), gdb_text)
        finalize_track.write(os.path.join(stage, lay_id + ".grd"), finalize_track.GRD_TEMPLATE)

        has_cam = bool(lay.get("cam") and os.path.isfile(lay["cam"]))
        if has_cam:
            shutil.copy2(lay["cam"], os.path.join(stage, lay_id + ".cam"))

        n_ui = stage_rf1_menu_images(
            lay["dir"], asset_root, lname, stage, lay_id, tex_tool
        )
        aiw_note = "yes"
        if not has_aiw:
            aiw_note = "no"
        elif aiw_info.get("clamped_garagespots"):
            aiw_note = "yes (garagespots->%d)" % aiw_info["garage_spots"]
        notes = []
        if trk_stats["skipped_missing"]:
            notes.append("%d skipped missing" % trk_stats["skipped_missing"])
        notes.append(
            "shadows=%s (%d casters, %d receivers, range=%.0fm)"
            % (track_shadows, trk_stats["shadow_casters"],
               trk_stats["shadow_receivers"], trk_stats["max_shadow_range"])
        )
        note_str = (", " + ", ".join(notes)) if notes else ""
        print("   layout %-20s -> %s.trk (%d instances%s, AIW=%s, CAM=%s, UI=%d)"
              % (lname, lay_id, trk_stats["instances"], note_str,
                 aiw_note,
                 "yes" if has_cam else "no",
                 n_ui))
        manifest_layouts.append({
            "source_layout": lname,
            "layout_id": lay_id,
            "instances": trk_stats["instances"],
            "skipped_missing": trk_stats["skipped_missing"],
            "renamed_long": trk_stats["renamed_long"],
            "track_shadows": track_shadows,
            "shadow_casters": trk_stats["shadow_casters"],
            "shadow_receivers": trk_stats["shadow_receivers"],
            "has_aiw": has_aiw,
            "has_cam": has_cam,
        })

    n_mov_mat, n_var_mat = _disambiguate_rf1_materials(all_gmt_dirs, generated_trks)
    if n_mov_mat or n_var_mat:
        print("   disambiguated %d moveable + %d conflicting static GMT material name(s)"
              % (n_mov_mat, n_var_mat))

    manifest = {
        "source_type": src_type,
        "source_dir": track_dir,
        "asset_root": asset_root,
        "venue": work_name,
        "layouts": manifest_layouts,
        "fallback_textures": sorted(fallback_made),
    }
    with open(os.path.join(out_dir, "rf1_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    # 4. Stage & optionally install.
    print("\n[4/4] staging and install")
    print("   staged %s" % stage)
    dest = stage
    if install:
        try:
            dest = finalize_track.install_location(stage, install, work_name)
            print("   installed -> %s" % dest)
        except (OSError, RuntimeError) as exc:
            print("   could not install: %s" % exc)
            print("   staged copy: %s" % stage)
            raise SystemExit(1)

    print("\n%s ready" % work_name)
    return {
        "work_dir": out_dir,
        "stage_dir": stage,
        "installed_dir": dest,
        "layouts": manifest_layouts,
    }
