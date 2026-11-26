"""Check an installed GTR2 track against what the pipeline meant to produce.

Reads the actual GTR2 files the game will load (.trk, .gdb, .AIW, GMT v2.10
meshes, and DX9 DDS textures) and compares them against the material map.

    python verify_install.py "<GTR2>\\GameData\\Locations\\<track>" out/<track>
"""
import os
import re
import sys
import json
import struct
import argparse

import gmt_gtr2
from fix_textures import expected_dds_bytes

ALPHA_REF_OFFSET = 100
TEX_FLAGS_OFFSET = 64
COLOR_KEY = 0x80

SLOT_NAMES = {"sBase", "sBase1", "sSpec", "sBump", "sTex0", "sCube", "sTex1"}
PSEUDO_TEXTURES = {"MATH"}

KNOWN_SHADERS = (
    "L2BUMPSPECMAPT0XT1",
    "L2BUMPSPECMAPFACESTAMPT0",
    "L2BUMPSPECMAPT0",
    "L2BUMPT0XT1",
    "L2SPECULARMAPT0XT1",
    "L2SPECULARMAPT0",
    "L2BUMPT0",
    "L2DIFFUSET0XT1",
    "L2DIFFUSET0",
)


def shader_of(data):
    """The shader a gmt was written with, matched whole."""
    for name in KNOWN_SHADERS:
        raw = name.encode()
        i = data.find(raw)
        while i >= 0:
            end = i + len(raw)
            if end >= len(data) or not (65 <= data[end] <= 90
                                        or 48 <= data[end] <= 57):
                return name
            i = data.find(raw, i + 1)
    return None


def material_colours(data, shader_at):
    """Ambient and diffuse sit at fixed offsets before the shader name."""
    f = lambda o: struct.unpack_from("<f", data, shader_at + o)[0]
    return f(-80), f(-64)


def _rf2_gmt_texture_names(b):
    """Exact texture names from a v2.52 rF2 GMT material table, or None."""
    if not gmt_gtr2.is_rf2_gmt(b) or len(b) < gmt_gtr2.RF2_HEADER_SIZE:
        return None
    mat_off = struct.unpack_from("<I", b, 0)[0] + 4
    if mat_off + 4 > len(b):
        return None
    nmat = struct.unpack_from("<I", b, mat_off)[0]
    pos = mat_off + 4
    names = set()
    for _ in range(nmat):
        if pos + gmt_gtr2.RF2_MAT_HEADER_SIZE > len(b):
            return None
        ntex = struct.unpack_from("<I", b, pos + 256)[0]
        pos += gmt_gtr2.RF2_MAT_HEADER_SIZE
        for _ in range(ntex):
            if pos + gmt_gtr2.RF2_TEXTURE_SIZE > len(b):
                return None
            raw = b[pos:pos + 64].split(b"\x00", 1)[0]
            if raw:
                names.add(raw)
            pos += gmt_gtr2.RF2_TEXTURE_SIZE
    return names


def _gtr2_gmt_info(b):
    """Parse textures, L2 shaders, alpha_refs, and tex_flags from a GTR2 GMT v2.10."""
    if not gmt_gtr2.is_gtr2_gmt(b) or len(b) < gmt_gtr2.GTR2_HEADER_SIZE:
        return None
    mat_off = struct.unpack_from("<I", b, 0)[0] + 4
    if mat_off + 4 > len(b):
        return None
    nmat = struct.unpack_from("<I", b, mat_off)[0]
    pos = mat_off + 4
    names = set()
    shaders = []
    alpha_refs = {}
    tex_flags = {}
    for _ in range(nmat):
        if pos + gmt_gtr2.GTR2_MAT_HEADER_SIZE + 3 * gmt_gtr2.GTR2_SHADER_INFO_SIZE + gmt_gtr2.GTR2_MAT_POST_SHADERS_PAD + 4 > len(b):
            return None
        pos += gmt_gtr2.GTR2_MAT_HEADER_SIZE
        l2_shader = ""
        for lv in range(3):
            sh = b[pos + 8:pos + 72].split(b"\x00", 1)[0].decode("ascii", errors="replace")
            if lv == 2:
                l2_shader = sh
            pos += gmt_gtr2.GTR2_SHADER_INFO_SIZE
        shaders.append(l2_shader)
        pos += gmt_gtr2.GTR2_MAT_POST_SHADERS_PAD
        ntex = struct.unpack_from("<I", b, pos)[0]
        pos += 4
        for _ in range(ntex):
            if pos + 80 > len(b):
                return None
            sz = gmt_gtr2.tex_record_size(b, pos)
            if pos + sz > len(b):
                return None
            raw = b[pos:pos + 64].split(b"\x00", 1)[0]
            tname = raw.decode("ascii", errors="replace")
            if tname and tname.upper() not in PSEUDO_TEXTURES:
                names.add(raw)
                tex_flags[tname] = struct.unpack_from("<I", b, pos + TEX_FLAGS_OFFSET)[0]
                if pos + ALPHA_REF_OFFSET < len(b):
                    alpha_refs[tname] = b[pos + ALPHA_REF_OFFSET]
            pos += sz
    return {
        "textures": names,
        "shaders": shaders,
        "alpha_refs": alpha_refs,
        "tex_flags": tex_flags,
        "trailing": len(b) - pos,
    }


def read_gmt(path):
    with open(path, "rb") as f:
        b = f.read()
    parsed_texs = _rf2_gmt_texture_names(b)
    out = {"shader": shader_of(b),
           "textures": (parsed_texs if parsed_texs is not None
                        else set(re.findall(rb"[A-Za-z0-9_\-]+\.dds", b))),
           "strings": set(x.group().decode()
                          for x in re.finditer(rb"[ -~]{3,}", b)),
           "materials": [], "alpha_refs": {}}
    for m in re.finditer(rb"L2[A-Z0-9]{6,40}", b):
        s = m.start()
        if s < 96:
            continue
        amb, dif = material_colours(b, s)
        if 0.0 <= amb <= 1.0 and 0.0 <= dif <= 1.0:
            out["materials"].append((m.group().decode(), amb, dif))
    out["tex_flags"] = {}
    for m in re.finditer(rb"[A-Za-z0-9_\-]+\.dds", b):
        at = m.start() + ALPHA_REF_OFFSET
        if at < len(b):
            out["alpha_refs"][m.group().decode()] = b[at]
        fl = m.start() + TEX_FLAGS_OFFSET
        if fl + 4 <= len(b):
            out["tex_flags"][m.group().decode()] = struct.unpack_from("<I", b, fl)[0]
    return out


def vertical_normal_share(path, limit=4000):
    """Share of vertex normals in a gmt that point straight up."""
    with open(path, "rb") as f:
        b = f.read()
    hits = []
    for off in range(0, len(b) - 24, 4):
        px, py, pz = struct.unpack_from("<3f", b, off)
        if not (4.0 < abs(px) + abs(py) + abs(pz) < 1e5):
            continue
        nx, ny, nz = struct.unpack_from("<3f", b, off + 12)
        if not abs(nx * nx + ny * ny + nz * nz - 1.0) <= 1e-3:
            continue
        hits.append((off, ny))
        if len(hits) >= limit:
            break
    if len(hits) < 8:
        return None
    gaps = {}
    for (a, _), (c, _) in zip(hits, hits[1:]):
        gaps[c - a] = gaps.get(c - a, 0) + 1
    stride = max(gaps, key=gaps.get)
    offs = {o for o, _ in hits}
    rec = [ny for o, ny in hits if o + stride in offs or o - stride in offs]
    if len(rec) < 8:
        return None
    return sum(ny > 0.99 for ny in rec) / float(len(rec))


def installed_scene_path(installed, venue):
    """Locate the installed GTR2 .trk file corresponding to the track's .gdb."""
    candidates = []
    for folder, _, names in os.walk(installed):
        for name in names:
            if name.lower().endswith(".gdb"):
                candidates.append(os.path.join(
                    folder, os.path.splitext(name)[0] + ".trk"))
    candidates.sort()
    if candidates:
        return next((path for path in candidates if os.path.isfile(path)),
                    candidates[0])
    return os.path.join(installed, venue + ".trk")


def verify_rf1_install(installed, out_dir):
    """Verify an rFactor 1 -> GTR2 installed track folder."""
    import rf1_track
    import steam_paths

    problems = []
    gdbs = sorted(
        f for f in os.listdir(installed)
        if f.lower().endswith(".gdb") and os.path.isfile(os.path.join(installed, f))
    ) if os.path.isdir(installed) else []
    if not gdbs:
        problems.append("no .gdb layout files found in %s" % installed)

    # Gather all installed GMTs (gmt/ and gmt_*/) and textures (maps/ and maps_*/).
    gmt_paths = {}
    map_paths = {}
    for folder, _, names in os.walk(installed):
        rel = os.path.relpath(folder, installed)
        top = rel.split(os.sep)[0].lower() if rel != "." else ""
        for name in names:
            low = name.lower()
            full = os.path.join(folder, name)
            if low.endswith(".gmt") and (top == "gmt" or top.startswith("gmt_")):
                gmt_paths.setdefault(low, []).append(full)
            elif os.path.splitext(low)[1] in rf1_track.TEXTURE_EXTS and (
                top == "maps" or top.startswith("maps_") or top == ""
            ):
                map_paths.setdefault(low, []).append(full)

    # Check GTR2 SHARED textures as valid external texture sources.
    shared_tex = rf1_track._gtr2_shared_textures(steam_paths.GTR2_DIR, steam_paths.MASUNPACK)
    on_disk_lower = set(map_paths.keys()) | shared_tex

    # 1. Verify every GMT is valid GTR2 GMT v2.10 with 0 trailing bytes,
    #    no rF1 skybox GMTs shadow GTR2 SHARED, and every texture (including
    #    skyboxi.gmt's _invisible.dds) is installed.
    bad_gmt = []
    shadowed_sky = []
    missing_tex = set()
    total_gmts = 0
    for low, paths in sorted(gmt_paths.items()):
        if low in rf1_track.SKYBOX_GMTS and low != "skyboxi.gmt":
            shadowed_sky.append(low)
        for path in paths:
            total_gmts += 1
            with open(path, "rb") as fh:
                b = fh.read()
            g2 = _gtr2_gmt_info(b)
            if g2 is None or g2["trailing"] != 0:
                bad_gmt.append(os.path.basename(path))
                continue
            for t in g2["textures"]:
                tname = t.decode("ascii", errors="replace").strip()
                tl = tname.lower()
                if not tl or tl in on_disk_lower:
                    continue
                stem = os.path.splitext(tl)[0]
                if not any(
                    k == stem + ".dds" or k.startswith(stem) and k.endswith((".dds", ".tga"))
                    for k in on_disk_lower
                ):
                    missing_tex.add(tname)

    if bad_gmt:
        problems.append("installed GMTs not valid GTR2 GMT v2.10: %s" % bad_gmt[:5])
    if shadowed_sky:
        problems.append("rF1 skybox GMTs shadow GTR2 SHARED skybox: %s" % shadowed_sky[:5])
    if missing_tex:
        problems.append("textures referenced but not installed: %s" % sorted(missing_tex)[:5])

    # 2. Verify every installed DDS is valid DX9 pow2 DXT1/3/5 with exact byte size.
    unloadable = []
    total_dds = 0
    for low, paths in sorted(map_paths.items()):
        if not low.endswith(".dds"):
            continue
        for inst_dds in paths:
            total_dds += 1
            with open(inst_dds, "rb") as fh:
                head = fh.read(128)
            if len(head) < 128 or head[:4] != b"DDS ":
                unloadable.append(os.path.basename(inst_dds) + " (not DDS)")
                continue
            h, w = struct.unpack_from("<II", head, 12)
            mips = struct.unpack_from("<I", head, 28)[0]
            fourcc = head[84:88]
            caps2 = struct.unpack_from("<I", head, 112)[0]
            is_cube = bool(caps2 & 0x200)
            exp_bytes = expected_dds_bytes(w, h, fourcc, mips)
            if is_cube and exp_bytes is not None:
                exp_bytes = 128 + 6 * (exp_bytes - 128)
            actual_bytes = os.path.getsize(inst_dds)
            if min(w, h) < 4:
                unloadable.append(os.path.basename(inst_dds) + " (under 4x4)")
            elif (w & (w - 1)) != 0 or (h & (h - 1)) != 0:
                unloadable.append("%s (non-pow2 %dx%d)" % (os.path.basename(inst_dds), w, h))
            elif fourcc not in (b"DXT1", b"DXT3", b"DXT5"):
                unloadable.append("%s (format %r)" % (os.path.basename(inst_dds), fourcc))
            elif exp_bytes is not None and actual_bytes != exp_bytes:
                unloadable.append("%s (malformed DDS size %d != %d)"
                                  % (os.path.basename(inst_dds), actual_bytes, exp_bytes))
    if unloadable:
        problems.append("textures GTR2 cannot load: %s" % unloadable[:5])

    # 3. Verify every layout's .trk, .AIW, .grd, unique VenueName, MeshFile= refs,
    #    garagespots <= 3, and <=31-char Instance= names.
    avail_gmts = set(gmt_paths.keys()) | rf1_track.SKYBOX_GMTS
    missing_meshes = set()
    overlong_instances = []
    seen_venues = {}
    dup_venues = []
    bad_garages = []
    for gdb in gdbs:
        stem = os.path.splitext(gdb)[0]
        base = os.path.join(installed, stem)
        gdb_txt = open(os.path.join(installed, gdb), encoding="utf-8", errors="replace").read()
        m_vn = re.search(r"(?im)^\s*VenueName\s*=\s*(.+)", gdb_txt)
        if m_vn:
            vn = m_vn.group(1).strip()
            vkey = vn.casefold()
            if vkey in seen_venues:
                dup_venues.append("%r (%s, %s)" % (vn, seen_venues[vkey], gdb))
            else:
                seen_venues[vkey] = gdb
        for ext in (".trk", ".AIW", ".grd"):
            if not os.path.isfile(base + ext):
                problems.append("missing companion file: %s" % (stem + ext))
        aiw_path = base + ".AIW"
        if os.path.isfile(aiw_path):
            aiw_head = open(aiw_path, encoding="utf-8", errors="replace").read(4096)
            m_gs = re.search(r"(?im)^\s*garagespots\s*=\s*(\d+)", aiw_head)
            if m_gs and int(m_gs.group(1)) > 3:
                bad_garages.append("%s (garagespots=%s)" % (stem + ".AIW", m_gs.group(1)))
        trk_path = base + ".trk"
        if os.path.isfile(trk_path):
            scene = open(trk_path, encoding="utf-8", errors="replace").read()
            for mf in re.findall(r"(?i)\bMeshFile\s*=\s*(\S+)", scene):
                if mf.lower() not in avail_gmts:
                    missing_meshes.add(mf)
            for inst in re.findall(r"(?i)\bInstance=([^\s\{\}]+)", scene):
                if len(inst) > 31:
                    overlong_instances.append(inst)

    # Check multi-layout stem/prefix uniqueness (GTR2 requires distinct stem[:4]
    # and non-prefix VenueName / layout stems so all layouts are selectable).
    if len(gdbs) > 1:
        stems = [os.path.splitext(g)[0] for g in gdbs]
        lows = [s.lower() for s in stems]
        if len({s[:4] for s in lows}) < len(lows):
            problems.append("multi-layout .gdb stems share first 4 chars: %s" % stems)
        for i in range(len(lows)):
            for j in range(len(lows)):
                if i != j and lows[j].startswith(lows[i]):
                    problems.append("layout stem %r is a prefix of %r" % (stems[i], stems[j]))
        vlist = list(seen_venues.items())
        for i in range(len(vlist)):
            for j in range(len(vlist)):
                if i != j and vlist[j][0].startswith(vlist[i][0]):
                    problems.append(
                        "VenueName prefix collision: %s (%r) vs %s (%r)"
                        % (vlist[i][1], vlist[i][0], vlist[j][1], vlist[j][0])
                    )

    if dup_venues:
        problems.append("duplicate VenueName across .gdb layouts: %s" % dup_venues[:5])
    if bad_garages:
        problems.append("AIW garagespots exceeds GTR2 max (3): %s" % bad_garages[:5])
    if missing_meshes:
        problems.append("TRK references missing GMT meshes: %s" % sorted(missing_meshes)[:5])
    if overlong_instances:
        problems.append("TRK Instance= names exceed gMotor 31-char limit: %d, e.g. %s"
                        % (len(overlong_instances), overlong_instances[:4]))

    print("installed:   %s (rFactor 1 source)" % installed)
    print("layouts:     %d (%s)" % (len(gdbs), ", ".join(os.path.splitext(g)[0] for g in gdbs)))
    print("gmts:        %d GTR2 v2.10 verified (%d bad)" % (total_gmts - len(bad_gmt), len(bad_gmt)))
    print("textures:    %d media (%d DX9 DDS verified), %d missing"
          % (len(map_paths), total_dds, len(missing_tex)))
    print()
    if problems:
        print("PROBLEMS")
        for p in problems:
            print("  - %s" % p)
        return 1
    print("all checks passed")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("installed", help="GTR2 GameData\\Locations\\<track> folder")
    ap.add_argument("out_dir", help="out/<track> from the converter")
    args = ap.parse_args()
    installed, out_dir = args.installed, args.out_dir
    if os.path.isfile(os.path.join(out_dir, "rf1_manifest.json")) or not os.path.isfile(
        os.path.join(out_dir, "material_map.json")
    ):
        return verify_rf1_install(installed, out_dir)
    gmt_dir = os.path.join(installed, "gmt")
    map_dir = os.path.join(installed, "maps")
    source_gmt_dir = os.path.join(out_dir, "gmt")
    venue = os.path.basename(installed.rstrip("\\/"))
    trk = installed_scene_path(installed, venue)
    data = json.load(open(os.path.join(out_dir, "material_map.json")))

    problems = []
    rows = {m["mesh"]: m for m in data["meshes"]}
    rows_by_id = {m["id"]: m for m in data["meshes"] if "id" in m}

    def row_of(rec):
        if rec is None:
            return None
        if rec.get("id"):
            return rows_by_id.get(rec["id"])
        return rows.get(rec.get("mesh"))

    on_disk = set(os.listdir(map_dir)) if os.path.isdir(map_dir) else set()
    on_disk_lower = {n.lower() for n in on_disk}
    gmt_files = os.listdir(gmt_dir) if os.path.isdir(gmt_dir) else []
    if os.path.isfile(trk):
        scene = open(trk, encoding="utf-8", errors="replace").read()
    else:
        scene = ""
        problems.append("missing TRK scene file: %s" % trk)

    # Check companion .gdb / .AIW / .grd files next to .trk.
    trk_stem = os.path.splitext(trk)[0]
    for ext in (".gdb", ".AIW", ".grd"):
        if not os.path.isfile(trk_stem + ext):
            problems.append("missing companion file: %s" % (os.path.basename(trk_stem) + ext))

    # 1. Verify every installed GMT is valid unencrypted GTR2 GMT v2.10, every
    #    referenced texture (excluding MATH) is installed, and no slot names leaked.
    bad_gmt = []
    missing, leaked = set(), []
    refs = {}
    gtr2_refs = {}
    for folder in (gmt_dir, map_dir):
        if not os.path.isdir(folder):
            problems.append("missing asset folder: %s" % folder)
    for f in gmt_files:
        if not f.lower().endswith(".gmt"):
            continue
        inst_path = os.path.join(gmt_dir, f)
        with open(inst_path, "rb") as fh:
            inst_bytes = fh.read()
        g2 = _gtr2_gmt_info(inst_bytes)
        if g2 is None or g2["trailing"] != 0:
            bad_gmt.append(f)
            continue
        gtr2_refs[f] = g2
        for t in g2["textures"]:
            tname = t.decode("ascii", errors="replace")
            if tname.lower() not in on_disk_lower:
                missing.add(tname)
        if f.lower() in ("skyboxi.gmt", "xpitin.gmt", "xpitout.gmt"):
            continue
        source = os.path.join(source_gmt_dir, f)
        if not os.path.isfile(source):
            problems.append("installed GMT has no source file: %s" % f)
            continue
        g = read_gmt(source)
        leaked += [(f, s) for s in SLOT_NAMES & g["strings"]]
        refs[f] = g

    if bad_gmt:
        problems.append("installed GMTs not valid GTR2 GMT v2.10: %s" % bad_gmt[:5])
    if missing:
        problems.append("textures referenced but not installed: %s" % sorted(missing)[:5])
    if leaked:
        problems.append("slot names written where filenames belong: %s" % leaked[:5])

    # Every installed DDS must be unencrypted DX9 power-of-two DXT1/3/5 with exact byte size.
    unloadable = []
    for t in sorted(on_disk):
        if not t.lower().endswith(".dds"):
            continue
        inst_dds = os.path.join(map_dir, t)
        with open(inst_dds, "rb") as fh:
            head = fh.read(128)
        if len(head) < 128 or head[:4] != b"DDS ":
            unloadable.append(t + " (not DDS)")
        else:
            h, w = struct.unpack_from("<II", head, 12)
            mips = struct.unpack_from("<I", head, 28)[0]
            fourcc = head[84:88]
            caps2 = struct.unpack_from("<I", head, 112)[0]
            is_cube = bool(caps2 & 0x200)
            exp_bytes = expected_dds_bytes(w, h, fourcc, mips)
            if is_cube and exp_bytes is not None:
                exp_bytes = 128 + 6 * (exp_bytes - 128)
            actual_bytes = os.path.getsize(inst_dds)
            if min(w, h) < 4:
                unloadable.append(t + " (under 4x4)")
            elif (w & (w - 1)) != 0 or (h & (h - 1)) != 0:
                unloadable.append("%s (non-pow2 %dx%d)" % (t, w, h))
            elif fourcc not in (b"DXT1", b"DXT3", b"DXT5"):
                unloadable.append("%s (format %r)" % (t, fourcc))
            elif exp_bytes is not None and actual_bytes != exp_bytes:
                unloadable.append("%s (malformed DDS size %d != %d)"
                                  % (t, actual_bytes, exp_bytes))
    if unloadable:
        problems.append("textures GTR2 cannot load: %s" % unloadable[:5])

    # 2. Hidden meshes are really marked Render=False in .trk.
    hidden = {
        os.path.splitext(m.group(1))[0]
        for block in re.findall(r"Instance=\S+\s*\{([^}]*)\}", scene)
        if "Render=False" in block or "LODOut=(0.01)" in block
        for m in re.finditer(r"MeshFile=(\S+)", block)
    }
    rec_path = os.path.join(out_dir, "gmt", "exported.json")
    want_hidden = []
    for rec in (json.load(open(rec_path)) if os.path.exists(rec_path) else []):
        row = row_of(rec)
        if row is not None and (row.get("lod_of") or not row.get("render", True)):
            want_hidden.append(os.path.splitext(rec["gmt"])[0])
    still_drawn = [n for n in want_hidden if n not in hidden]
    if still_drawn:
        problems.append("meant to be hidden but still drawn: %d, e.g. %s"
                        % (len(still_drawn), still_drawn[:4]))

    # 3 & 4. Alpha thresholds and ColorKey (0x80) reached the GTR2 v2.10 GMTs.
    cutout = data.get("alpha_ref", {})
    want_ref = {}
    for m in data["meshes"]:
        name = m["rf2_material"]
        if name not in cutout:
            continue
        t = m.get("baked") or m["textures"].get("txDiffuse")
        if not t:
            continue
        disk = data.get("cutout_tex", {}).get(name) or (
            t if m.get("baked") else data.get("texture_files", {}).get(t, t))
        want_ref[disk] = cutout[name]

    exported = {}
    if os.path.exists(rec_path):
        exported = {r["gmt"]: r for r in json.load(open(rec_path))}
    else:
        problems.append("no export record at %s, so nothing could be checked "
                        "against the meshes -- run this after a build finishes"
                        % rec_path)

    checked = wrong = 0
    examples = []
    unkeyed = []
    for fname, g2 in gtr2_refs.items():
        row = row_of(exported.get(fname))
        if row is None or row["rf2_material"] not in cutout:
            continue
        t = row.get("baked") or row["textures"].get("txDiffuse")
        disk = data.get("cutout_tex", {}).get(row["rf2_material"]) or (
            t if row.get("baked") else data.get("texture_files", {}).get(t, t))
        if disk not in g2["alpha_refs"]:
            continue
        want = cutout[row["rf2_material"]]
        checked += 1
        got = g2["alpha_refs"][disk]
        if got != want:
            wrong += 1
            if len(examples) < 4:
                examples.append("%s %s got %d want %d" % (fname, disk, got, want))
        fl = g2["tex_flags"].get(disk)
        if fl is not None and not (fl & COLOR_KEY):
            unkeyed.append("%s %s 0x%08x" % (fname, disk, fl))

    if unkeyed:
        problems.append("cut-outs whose alpha test is not switched on "
                        "(no colour key, so the threshold is ignored): %d, e.g. %s"
                        % (len(unkeyed), unkeyed[:3]))
    if checked and wrong:
        problems.append("alpha thresholds wrong in %d of %d gmts: %s"
                        % (wrong, checked, examples))
    if want_ref and not checked:
        problems.append("no alpha threshold found in any gmt, expected %d" % len(want_ref))

    # 5. Upright surfaces carry their own normals, not "straight up".
    sky_lit, walls = [], 0
    for fname in refs:
        row = row_of(exported.get(fname))
        if (row is None or float(row.get("upward", 1.0)) > 0.1
                or float(row.get("ac_normals_up", 0.0)) > 0.5
                or row.get("shader") in ("ksTree", "ksGrass")
                or row["rf2_material"] in cutout):
            continue
        ups = vertical_normal_share(os.path.join(source_gmt_dir, fname))
        if ups is None:
            continue
        walls += 1
        if ups > 0.9:
            sky_lit.append("%s %.0f%% up" % (fname, 100 * ups))
    if sky_lit:
        problems.append("upright meshes whose normals all point up: %d of %d, e.g. %s"
                        % (len(sky_lit), walls, sky_lit[:3]))

    # 6. Visible range in GTR2 GMT v2.10 (LOD_Out at offset 0xF8).
    short_range = []
    for fname in refs:
        try:
            with open(os.path.join(gmt_dir, fname), "rb") as fh:
                head = fh.read(0xFC)
            if len(head) == 0xFC and struct.unpack_from("<f", head, 0xF8)[0] <= 1000.0:
                short_range.append(fname)
        except OSError:
            pass
    if short_range:
        problems.append("%d gmts keep a <= 1000 m visible range, e.g. %s"
                        % (len(short_range), short_range[:3]))

    # 7. Ground/road shadow receivers exist (for car & track shadows).
    casters = scene.count("ShadowCaster=")
    receivers = scene.count("ShadowReceiver=True")
    if not receivers:
        problems.append("no instance receives shadows (ShadowReceiver=True)")

    # 8. Instance= names must fit inside gMotor 2.0's 32-byte buffer (<= 31 chars + NUL).
    overlong_instances = [
        inst for inst in re.findall(r"(?i)\bInstance=([^\s\{\}]+)", scene)
        if len(inst) > 31
    ]
    if overlong_instances:
        problems.append("TRK Instance= names exceed gMotor 31-char limit: %d, e.g. %s"
                        % (len(overlong_instances), overlong_instances[:4]))

    print("installed:   %s" % installed)
    print("gmts:        %d (%d GTR2 v2.10 verified)" % (len(refs), len(gtr2_refs)))
    print("textures:    %d installed, %d missing" % (len(on_disk), len(missing)))
    print("hidden:      %d instances, %d wanted" % (len(hidden), len(want_hidden)))
    print("alpha tests: %d checked, %d wrong, %d not colour keyed"
          % (checked, wrong, len(unkeyed)))
    print("normals:     %d upright meshes checked, %d lit as if facing up"
          % (walls, len(sky_lit)))
    print("ranges:      %d gmts at <= 1000 m" % len(short_range))
    print("shadows:     %d casters, %d receivers" % (casters, receivers))
    print()
    if problems:
        print("PROBLEMS")
        for p in problems:
            print("  - %s" % p)
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
