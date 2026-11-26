"""Convert one Assetto Corsa track into GTR2, end to end.

    python build_track.py "<AC track folder>" [--install "<GTR2>\\GameData\\Locations"]

Stages, in order:
  1. read the model, map surfaces, extract textures      convert_track.py
  2. re-encode textures DX9 cannot load                  fix_textures.py
  3. flatten AC multilayer materials into one texture    bake_layers.py
  4. waypoints, grid, pit lane, timing strips            make_aiw.py
  5. trackside cameras                                   make_cam.py
  6. geometry to GMT meshes                              blender_stage.py (Blender)
  7. GTR2 scene, database, GMT v2.10 convert, install    finalize_track.py
"""
import os
import sys
import json
import shutil
import struct
import argparse
import subprocess

from parallel import default_jobs

HERE = (
    os.path.dirname(os.path.abspath(sys.executable))
    if getattr(sys, "frozen", False)
    else os.path.dirname(os.path.abspath(__file__))
)
BLENDER = (
    os.path.join(HERE, "tools", "blender", "blender.exe")
    if os.path.isfile(os.path.join(HERE, "tools", "blender", "blender.exe"))
    else r"D:\Apps\Blender\blender.exe"
)


def stage_cmd(script_name, *stage_args):
    """Build a command line for one pipeline stage, frozen or unfrozen."""
    if getattr(sys, "frozen", False):
        stage = os.path.splitext(os.path.basename(script_name))[0]
        return [sys.executable, "--stage", stage, *stage_args]
    return [sys.executable, os.path.join(HERE, script_name), *stage_args]


def step(n, title):
    print("\n[%d/7] %s" % (n, title))
    sys.stdout.flush()


WARNINGS = []


def collect_warnings(out):
    WARNINGS.extend(l.strip() for l in out.splitlines()
                    if l.strip().startswith(("WARNING", "!! no file produced")))


def run(args, quiet=False):
    r = subprocess.run(args, capture_output=True, text=True, errors="replace", cwd=HERE)
    out = (r.stdout or "") + (r.stderr or "")
    collect_warnings(out)
    if r.returncode != 0:
        print(out[-2000:])
        raise SystemExit("failed: %s" % " ".join(str(a) for a in args[:3]))
    if not quiet:
        for line in out.splitlines():
            if line.strip() and not line.startswith(("Blender", "TBBmalloc", "Read prefs")):
                print("   " + line[:160])
    return out


def write_solid_texture(path, rgb):
    """A 4x4 block of one colour, for a shader slot AC gives us nothing for."""
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


def write_clear_texture(path):
    """A 4x4 fully transparent block, used by the timing strips."""
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


GEOMETRY_LINES = ("EXPORTED", "backdrop", "coarsen", "materials prepared", "!!")


def blender_stage(pre, post, gmt_dir, jobs):
    """Run the geometry stage, split across `jobs` Blenders when above 1."""
    if jobs <= 1:
        log = run(pre + [gmt_dir] + post, quiet=True)
        for line in log.splitlines():
            if line.startswith(GEOMETRY_LINES):
                print("   " + line[:160])
        return log

    shard_dirs = [os.path.join(gmt_dir, "_shard%d" % i) for i in range(jobs)]
    procs = []
    for i, d in enumerate(shard_dirs):
        os.makedirs(d)
        logf = open(os.path.join(d, "blender.log"), "w")
        procs.append((subprocess.Popen(
            pre + [d] + post + ["--shard", "%d/%d" % (i, jobs)],
            stdout=logf, stderr=subprocess.STDOUT, text=True, cwd=HERE), logf))
    print("   %d Blenders side by side" % jobs)
    for p, logf in procs:
        p.wait()
        logf.close()

    exported, logs, printed = [], [], set()
    for i, d in enumerate(shard_dirs):
        log = open(os.path.join(d, "blender.log"), errors="replace").read()
        logs.append(log)
        collect_warnings(log)
        record = os.path.join(d, "exported.json")
        if procs[i][0].returncode != 0 or not os.path.exists(record):
            print(log[-2500:])
            raise SystemExit("geometry stage: Blender %d of %d failed (log above, "
                             "and in %s)" % (i + 1, jobs, d))
        for line in log.splitlines():
            if line.startswith(GEOMETRY_LINES) and not line.startswith("EXPORTED") \
                    and line not in printed:
                printed.add(line)
                print("   " + line[:160])
        for e in json.load(open(record)):
            e["_dir"] = d
            exported.append(e)

    exported.sort(key=lambda e: e["_seq"])
    merged, names = [], set()
    for e in exported:
        src = os.path.join(e.pop("_dir"), e["gmt"])
        del e["_seq"]
        if e["gmt"].lower() in names:
            msg = "WARNING two meshes exported as %s; kept the first" % e["gmt"]
            print("   " + msg)
            WARNINGS.append(msg)
            continue
        names.add(e["gmt"].lower())
        os.replace(src, os.path.join(gmt_dir, e["gmt"]))
        merged.append(e)
    with open(os.path.join(gmt_dir, "exported.json"), "w") as f:
        json.dump(merged, f, indent=1)
    for d in shard_dirs:
        shutil.rmtree(d, ignore_errors=True)
    print("   EXPORTED %d (from %d Blenders)" % (len(merged), jobs))
    return "\n".join(logs)


def _usable_image(path, min_dim=32):
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "rb") as f:
            head = f.read(24)
    except OSError:
        return False
    if len(head) >= 24 and head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
        w, h = struct.unpack_from(">II", head, 16)
        if min(w, h) < min_dim:
            return False
    return True


def track_preview(track_dir, layout):
    """Find the best AC track map or preview image for GTR2 menu/loading screens."""
    candidates = []
    if layout:
        candidates.extend((
            os.path.join(track_dir, layout, "map.png"),
            os.path.join(track_dir, "ui", layout, "outline.png"),
        ))
    candidates.extend((
        os.path.join(track_dir, "map.png"),
        os.path.join(track_dir, "ui", "outline.png"),
    ))
    if layout:
        candidates.extend((
            os.path.join(track_dir, "ui", layout, "preview.png"),
            os.path.join(track_dir, layout, "ui", "preview.png"),
        ))
    candidates.append(os.path.join(track_dir, "ui", "preview.png"))
    return next((path for path in candidates if _usable_image(path)), None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("track_dir")
    ap.add_argument("--install", default=None,
                    help='a GTR2 GameData\\Locations folder, or "auto" to find it')
    ap.add_argument("--hour", default="12:00")
    ap.add_argument("--bake-size", type=int, default=2048)
    ap.add_argument("--texture-scale", type=float, default=None,
                    help="scale factor per texture dimension (overrides ac2gtr2.ini [track] texture_scale; default 0.5)")
    ap.add_argument("--texture-scale-min-size", type=int, default=None,
                    help="minimum max(w, h) (inclusive) to scale textures and destination floor bound (overrides ac2gtr2.ini [track] texture_scale_min_size; default 1024)")
    ap.add_argument("--road-smoothness", type=float, default=None,
                    help="road surface smoothing in [0.0, 1.0] mapped uniformly to sigma in [0m, 8m] (0 disables, 0.5 -> 4m default, 1.0 -> 8m max; overrides ac2gtr2.ini [track] road_smoothness)")
    ap.add_argument("--mesh-coarsening", type=float, default=None,
                    help="deterministic Blender mesh coarsening in [0.0, 1.0] (0.0 disables, 1.0 strongest safe coarsening; overrides ac2gtr2.ini [track] mesh_coarsening)")
    ap.add_argument("--strip-unused-gmt-data",
                    action=argparse.BooleanOptionalAction, default=None,
                    help="strip unused collision FaceData/NormalData on CollTarget=False GMTs and dummy UVTangents on flat-normal cutouts (default enabled; overrides ac2gtr2.ini [track] strip_unused_gmt_data)")
    ap.add_argument("--track-shadows", choices=("compact", "off", "full"), default=None,
                    help="GTR2 track shadow casting mode: compact (default), off, or full (overrides ac2gtr2.ini [track] track_shadows)")
    ap.add_argument("--track-brightness", type=float, default=None,
                    help="track brightness boost in [0.0, 1.0] (0.0 disabled, 0.5 default subtle lift, 1.0 bright sunny look; overrides ac2gtr2.ini [track] track_brightness)")
    ap.add_argument("--grain", default="2")
    ap.add_argument("--detail-metres", default="2.0",
                    help="how often the game repeats a second stage detail texture")
    ap.add_argument("--gain", default="1.0",
                    help="adjusts converted material lighting coefficients")
    ap.add_argument("--diffuse", default="0.42")
    ap.add_argument("--ambient", default="0.30")
    ap.add_argument("--scene-ambient", default="165")
    ap.add_argument("--blender", default=None)
    ap.add_argument("--layout", default=None,
                    help="for a multi-layout track: the part after models_ in models_<layout>.ini")
    ap.add_argument("--skip-geometry", action="store_true",
                    help="reuse the meshes already exported")
    ap.add_argument("--jobs", type=int, default=default_jobs(),
                    help="Blenders and texture conversions run side by side "
                         "(default %d here; 1 runs everything one at a time)" % default_jobs())
    ap.add_argument("--gtr2", default=None,
                    help="the GTR2 folder, if not auto-detected")
    ap.add_argument("--ac", default=None,
                    help="the assettocorsa folder, if Steam does not list it")
    ap.add_argument("--texconv", default=None,
                    help="path to texconv.exe (defaults to bundled copy or rFactor 2)")
    ap.add_argument("--masunpack", default=None,
                    help="path to masunpack.exe for rFactor 1 .mas archives")
    args = ap.parse_args()

    if args.gtr2:
        os.environ["AC2GTR2_GAME_DIR"] = os.path.abspath(args.gtr2)
    if args.ac:
        os.environ["AC2GTR2_AC_DIR"] = os.path.abspath(args.ac)
    if args.texconv:
        os.environ["AC2GTR2_TEXCONV"] = os.path.abspath(args.texconv)
    if args.masunpack:
        os.environ["AC2GTR2_MASUNPACK"] = os.path.abspath(args.masunpack)
    if args.blender:
        os.environ["AC2GTR2_BLENDER"] = os.path.abspath(args.blender)

    import steam_paths
    import rf1_track
    import track_zip

    raw_track_dir = os.path.abspath(args.track_dir)
    if track_zip.is_zip(raw_track_dir):
        raw_track_dir = track_zip.extract(raw_track_dir)
    args.track_dir = raw_track_dir

    if args.install is None and steam_paths.IS_STANDALONE:
        args.install = "auto"
    if args.install == "auto":
        args.install = steam_paths.INSTALL_LOCATIONS
        if steam_paths.IS_STANDALONE:
            os.makedirs(args.install, exist_ok=True)
    elif args.install:
        args.install = os.path.abspath(args.install)

    track_type = rf1_track.detect_track_type(args.track_dir)
    if track_type == "rf1":
        try:
            config = steam_paths.load_config()
        except ValueError as exc:
            raise SystemExit("config error: %s" % exc)
        if args.track_shadows is None:
            args.track_shadows = config["track_shadows"]
        else:
            try:
                args.track_shadows = steam_paths.validate_track_shadows(args.track_shadows)
            except ValueError as exc:
                raise SystemExit("--track-shadows: %s" % exc)
        import check_setup
        if not check_setup.check(args.track_dir, args.layout, None,
                                 args.install, need_blender=False,
                                 track_shadows=args.track_shadows,
                                 track_type="rf1"):
            raise SystemExit(1)
        rf1_track.convert_rf1_track(
            args.track_dir,
            layout=args.layout,
            install=args.install,
            masunpack=steam_paths.MASUNPACK,
            texconv=steam_paths.TEXCONV,
            gtr2_dir=steam_paths.GTR2_DIR,
            jobs=args.jobs,
            track_shadows=args.track_shadows,
        )
        return

    try:
        config = steam_paths.load_config()
    except ValueError as exc:
        raise SystemExit("config error: %s" % exc)
    if args.texture_scale is None:
        args.texture_scale = config["texture_scale"]
    elif not (0.0 < args.texture_scale <= 1.0):
        raise SystemExit("--texture-scale must be between 0 and 1, got %r" % (args.texture_scale,))
    if args.texture_scale_min_size is None:
        args.texture_scale_min_size = config["texture_scale_min_size"]
    elif args.texture_scale_min_size < 0:
        raise SystemExit("--texture-scale-min-size must be >= 0, got %r" % (args.texture_scale_min_size,))
    if args.road_smoothness is None:
        args.road_smoothness = config["road_smoothness"]
    else:
        try:
            steam_paths.road_smoothness_to_sigma(args.road_smoothness)
        except ValueError as exc:
            raise SystemExit("--road-smoothness: %s" % exc)
    if args.mesh_coarsening is None:
        args.mesh_coarsening = config["mesh_coarsening"]
    else:
        try:
            args.mesh_coarsening = steam_paths.validate_mesh_coarsening(args.mesh_coarsening)
        except ValueError as exc:
            raise SystemExit("--mesh-coarsening: %s" % exc)
    if args.strip_unused_gmt_data is None:
        args.strip_unused_gmt_data = config["strip_unused_gmt_data"]
    if args.track_shadows is None:
        args.track_shadows = config["track_shadows"]
    else:
        args.track_shadows = steam_paths.validate_track_shadows(args.track_shadows)
    if args.track_brightness is None:
        args.track_brightness = config["track_brightness"]
    else:
        try:
            args.track_brightness = steam_paths.validate_track_brightness(args.track_brightness)
        except ValueError as exc:
            raise SystemExit("--track-brightness: %s" % exc)

    if not args.blender:
        args.blender = steam_paths.BLENDER

    track = os.path.basename(os.path.abspath(args.track_dir).rstrip("\\/"))
    if args.layout:
        track = "%s_%s" % (track, args.layout)
    out = os.path.join(steam_paths.WORK_ROOT, track)
    lay = ["--layout", args.layout] if args.layout else []

    import check_setup
    reuse = args.skip_geometry and os.path.isdir(os.path.join(out, "gmt"))
    if not check_setup.check(args.track_dir, args.layout, args.blender,
                             args.install, need_blender=not reuse,
                             texture_scale=args.texture_scale,
                             texture_scale_min_size=args.texture_scale_min_size,
                             road_smoothness=args.road_smoothness,
                             mesh_coarsening=args.mesh_coarsening,
                             strip_unused_gmt_data=args.strip_unused_gmt_data,
                             track_shadows=args.track_shadows,
                             track_brightness=args.track_brightness):
        raise SystemExit(1)
    print("\nconverting %s" % track)

    step(1, "model, surfaces and textures")
    run(stage_cmd("convert_track.py", args.track_dir, "-o", out, "--textures", *lay))

    step(2, "texture formats")
    run(stage_cmd("fix_textures.py", os.path.join(out, "textures"),
                  "--texconv", steam_paths.TEXCONV,
                  "--scale", str(args.texture_scale),
                  "--scale-min-size", str(args.texture_scale_min_size),
                  "--jobs", str(args.jobs)))
    tex = os.path.join(out, "textures")
    write_clear_texture(os.path.join(tex, "_invisible.dds"))
    write_solid_texture(os.path.join(tex, "_flat_spec.dds"), (0, 0, 0))
    write_solid_texture(os.path.join(tex, "_flat_nm.dds"), (128, 128, 255))
    write_solid_texture(os.path.join(tex, "_white.dds"), (255, 255, 255))

    step(3, "composing multilayer materials")
    run(stage_cmd("bake_layers.py", out, "--size", str(args.bake_size),
                  "--grain", args.grain, "--detail-metres", args.detail_metres,
                  "--texconv", steam_paths.TEXCONV, "--jobs", str(args.jobs)))

    step(4, "waypoints, grid and pit lane")
    run(stage_cmd("make_aiw.py", args.track_dir, os.path.join(out, track + ".AIW"), *lay))

    step(5, "trackside cameras")
    run(stage_cmd("make_cam.py", args.track_dir, os.path.join(out, track + ".cam"),
                  "--aiw", os.path.join(out, track + ".AIW"), *lay))

    step(6, "geometry (this is the slow one)")
    gmt_dir = os.path.join(out, "gmt")
    record = os.path.join(gmt_dir, "exported.json")
    if args.skip_geometry and os.path.isdir(gmt_dir):
        print("   reusing existing meshes")
    else:
        if os.path.isdir(gmt_dir):
            shutil.rmtree(gmt_dir)
        log = blender_stage(
            [args.blender, "--background", "--factory-startup", "--python",
             steam_paths.BLENDER_STAGE, "--", os.path.join(out, "material_map.json")],
            ["--diffuse", args.diffuse, "--ambient", args.ambient, "--gain", args.gain,
             "--coarsening", repr(args.mesh_coarsening)],
            gmt_dir, args.jobs)

    try:
        exported = json.load(open(record))
    except Exception:
        exported = []
    if not exported:
        print(log[-2500:] if not args.skip_geometry else "")
        raise SystemExit("geometry stage produced nothing; see the output above")
    print("   %d meshes exported" % len(exported))

    step(7, "scene, database and install")
    cmd = stage_cmd("finalize_track.py", out, "--hour", args.hour,
                    "--ambient", args.scene_ambient,
                    "--road-smoothness", repr(args.road_smoothness),
                    "--mesh-coarsening", repr(args.mesh_coarsening),
                    ("--strip-unused-gmt-data"
                     if args.strip_unused_gmt_data
                     else "--no-strip-unused-gmt-data"),
                    "--track-shadows", args.track_shadows,
                    "--track-brightness", repr(args.track_brightness))
    preview = track_preview(args.track_dir, args.layout)
    if preview:
        cmd += ["--preview", preview, "--texconv", steam_paths.TEXCONV]
    else:
        print("   no AC track map or preview image found; menu images will be omitted")
    if args.install:
        cmd += ["--install", args.install]
    run(cmd)

    if WARNINGS:
        print("\n%d warning(s) -- read these before loading the track:" % len(set(WARNINGS)))
        for w in dict.fromkeys(WARNINGS):
            print("   " + w[:200])
    print("\n%s ready" % track)


if __name__ == "__main__":
    main()
