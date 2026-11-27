"""Build a standalone Windows ``dist/`` bundle for AC-to-GTR2 conversion.

Recreates ``dist/`` from scratch with:
  - ``ac2gtr2.exe`` (bundled Python runtime + NumPy + converter exposing
    ``build_track``, ``convert_multiple_tracks``, and drag-and-drop support)
  - ``ac2gtr2.ini`` (default configuration, e.g. ``[track] texture_scale``)
  - ``README.md`` (usage instructions and list of bundled tools)
  - ``tools/blender/`` (portable Blender 3.6 LTS with ``io_rf2_gmt_release``
    pre-installed inside ``3.6/scripts/addons/``)
  - ``tools/blender_scripts/`` (``blender_stage.py``, ``kn5.py``, ``backdrop.py``, ``mesh_coarsen.py``)
  - ``tools/texconv/texconv.exe``
  - ``tools/rfactortools/`` (``masunpack.exe``, ``rfactorcrypt.exe``, and required runtime DLLs/libraries)
  - ``data/ac/system/data/surfaces.ini``
  - ``data/gtr2/GameData/Locations/specialfx.tec``
  - ``out/`` (default automatic install destination for converted tracks, with ``readme.txt``)
  - ``work/`` (intermediate build workspace, with ``readme.txt``)

Usage:
    python build_dist.py [--dist <output dir>]
    python build_dist.py --release
"""
import argparse
import datetime
import os
import shutil
import subprocess
import sys
import tempfile
import time

import sha256sum
import steam_paths


def _rmtree_retry(path, attempts=5, delay=0.5):
    """Remove a directory tree, retrying briefly on transient Windows locks."""
    for i in range(attempts):
        try:
            shutil.rmtree(path)
            return
        except OSError:
            if not os.path.exists(path):
                return
            if i + 1 == attempts:
                raise
            time.sleep(delay * (i + 1))

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON_NAME = "io_rf2_gmt_release"
BLENDER_SCRIPTS = ("blender_stage.py", "kn5.py", "backdrop.py", "mesh_coarsen.py")
HIDDEN_IMPORTS = (
    "backdrop",
    "bake_layers",
    "build_track",
    "check_setup",
    "convert_multiple_tracks",
    "convert_track",
    "finalize_track",
    "fix_textures",
    "gmt_gtr2",
    "kn5",
    "make_aiw",
    "make_cam",
    "mesh_coarsen",
    "numpy",
    "parallel",
    "rf1_track",
    "road_smooth",
    "steam_paths",
    "track_zip",
    "verify_install",
)


OUT_README = (
    "Converted GTR2 tracks are installed here automatically.\n"
    "Copy any track folder from this directory into:\n"
    "  <GTR2>\\GameData\\Locations\\\n"
)

WORK_README = (
    "Intermediate conversion files (extracted textures, baked layers, raw GMTs,\n"
    "AIW/CAM files, and extracted track zips) are stored here during builds.\n"
    "Subfolders here can be deleted safely after conversion, or kept to reuse\n"
    "exported geometry with --skip-geometry.\n"
)


def _program_files_dirs():
    out = []
    for var in ("ProgramFiles", "ProgramFiles(x86)"):
        val = os.environ.get(var)
        if val and os.path.isdir(val):
            out.append(val)
    return out


def find_blender_dir(explicit, fallback_dist=None):
    candidates = []
    if explicit:
        path = os.path.abspath(explicit)
        if os.path.isfile(path):
            path = os.path.dirname(path)
        return path
    env_b = os.environ.get("AC2GTR2_BLENDER")
    if env_b:
        path = os.path.abspath(env_b)
        return os.path.dirname(path) if os.path.isfile(path) else path
    which_b = shutil.which("blender")
    if which_b:
        candidates.append(os.path.dirname(os.path.abspath(which_b)))
    for pf in _program_files_dirs():
        candidates.append(os.path.join(pf, "Blender Foundation", "Blender 3.6"))
    if fallback_dist:
        candidates.append(os.path.join(fallback_dist, "tools", "blender"))
    for path in candidates:
        if os.path.isfile(os.path.join(path, "blender.exe")):
            return path
    return candidates[0] if candidates else ""


def find_texconv(explicit, fallback_dist=None):
    if explicit:
        return os.path.abspath(explicit)
    env_t = os.environ.get("AC2GTR2_TEXCONV")
    if env_t:
        return os.path.abspath(env_t)
    which_t = shutil.which("texconv")
    if which_t:
        return os.path.abspath(which_t)
    rf2_dir = os.environ.get("AC2GTR2_RF2_DIR") or steam_paths.game_dir("rFactor 2")
    if rf2_dir:
        cand = os.path.join(rf2_dir, "Support", "Tools", "MapConverter", "texconv.exe")
        if os.path.isfile(cand):
            return cand
    if fallback_dist:
        cand = os.path.join(fallback_dist, "tools", "texconv", "texconv.exe")
        if os.path.isfile(cand):
            return cand
    return ""


def find_masunpack(explicit, rfactortools_dir=None, fallback_dist=None):
    if explicit:
        path = os.path.abspath(explicit)
        if os.path.isdir(path):
            return os.path.join(path, "masunpack.exe")
        return path
    if rfactortools_dir:
        path = os.path.abspath(rfactortools_dir)
        return path if os.path.isfile(path) else os.path.join(path, "masunpack.exe")
    env_rt = os.environ.get("AC2GTR2_RFACTORTOOLS")
    if env_rt:
        cand = os.path.join(os.path.abspath(env_rt), "masunpack.exe")
        if os.path.isfile(cand):
            return cand
    env_m = os.environ.get("AC2GTR2_MASUNPACK")
    if env_m:
        return os.path.abspath(env_m)
    which_m = shutil.which("masunpack")
    if which_m:
        return os.path.abspath(which_m)
    if fallback_dist:
        cand = os.path.join(fallback_dist, "tools", "rfactortools", "masunpack.exe")
        if os.path.isfile(cand):
            return cand
    return ""


def find_rfactorcrypt(explicit, masunpack_path=None, rfactortools_dir=None, fallback_dist=None):
    if explicit:
        path = os.path.abspath(explicit)
        if os.path.isdir(path):
            return os.path.join(path, "rfactorcrypt.exe")
        return path
    if rfactortools_dir:
        path = os.path.abspath(rfactortools_dir)
        cand = os.path.join(os.path.dirname(path) if os.path.isfile(path) else path, "rfactorcrypt.exe")
        if os.path.isfile(cand):
            return cand
    env_r = os.environ.get("AC2GTR2_RFACTORCRYPT")
    if env_r:
        return os.path.abspath(env_r)
    if masunpack_path:
        sibling = os.path.join(os.path.dirname(os.path.abspath(masunpack_path)), "rfactorcrypt.exe")
        if os.path.isfile(sibling):
            return sibling
    which_r = shutil.which("rfactorcrypt")
    if which_r:
        return os.path.abspath(which_r)
    if fallback_dist:
        cand = os.path.join(fallback_dist, "tools", "rfactortools", "rfactorcrypt.exe")
        if os.path.isfile(cand):
            return cand
    return ""


def find_ac_dir(explicit, fallback_dist=None):
    if explicit:
        return os.path.abspath(explicit)
    env_ac = os.environ.get("AC2GTR2_AC_DIR")
    if env_ac:
        return os.path.abspath(env_ac)
    cand = steam_paths.game_dir("assettocorsa")
    if cand and os.path.isfile(os.path.join(cand, "system", "data", "surfaces.ini")):
        return cand
    if fallback_dist:
        fb = os.path.join(fallback_dist, "data", "ac")
        if os.path.isfile(os.path.join(fb, "system", "data", "surfaces.ini")):
            return fb
    return cand


def find_gtr2_dir(explicit, fallback_dist=None):
    if explicit:
        return os.path.abspath(explicit)
    env_g = os.environ.get("AC2GTR2_GAME_DIR")
    if env_g:
        return os.path.abspath(env_g)
    cand = steam_paths.find_gtr2_dir()
    if cand and os.path.isfile(os.path.join(cand, "GameData", "Locations", "specialfx.tec")):
        return cand
    if fallback_dist:
        fb = os.path.join(fallback_dist, "data", "gtr2")
        if os.path.isfile(os.path.join(fb, "GameData", "Locations", "specialfx.tec")):
            return fb
    return cand


def find_addon_dir(blender_dir, explicit):
    candidates = []
    if explicit:
        candidates.append(os.path.abspath(explicit))
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(
            os.path.join(appdata, "Blender Foundation", "Blender", "3.6",
                         "scripts", "addons", ADDON_NAME)
        )
    candidates.append(
        os.path.join(blender_dir, "3.6", "scripts", "addons", ADDON_NAME)
    )
    for path in candidates:
        if os.path.isdir(path):
            return path
    raise FileNotFoundError(
        "Blender add-on %s not found; checked: %s" % (ADDON_NAME, ", ".join(candidates))
    )


def _ignore_blender(_dirpath, names):
    skip = set()
    for name in names:
        low = name.lower()
        if low == "blender.pdb" or low == "__pycache__":
            skip.add(name)
    return skip


def _ignore_pycache(_dirpath, names):
    return {n for n in names if n == "__pycache__"}


_RFACTORTOOLS_SKIP_DIRS = frozenset({"checksums", "data", "tcl", "tk", "__pycache__"})
_RFACTORTOOLS_SKIP_FILES = frozenset({
    "maspack.exe", "minised-gui.exe", "rfactortools-gui.exe",
    "logo.png", "tcl85.dll", "tk85.dll", "_tkinter.pyd",
})


def _ignore_rfactortools(_dirpath, names):
    """Skip unused GUI binaries/Tcl/Tk/checksums from rfactortools while keeping CLI runtime."""
    return {
        n for n in names
        if n.lower() in _RFACTORTOOLS_SKIP_DIRS or n.lower() in _RFACTORTOOLS_SKIP_FILES
    }


def dir_size_mb(path):
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total / (1024.0 * 1024.0)


def build_exe(dist_dir):
    try:
        import PyInstaller  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "PyInstaller is required to build ac2gtr2.exe. "
            "Install it with: python -m pip install pyinstaller"
        ) from exc

    cli_script = os.path.join(HERE, "ac2gtr2.py")
    with tempfile.TemporaryDirectory(prefix="ac2gtr2-pyi-") as tmp:
        cmd = [
            sys.executable, "-m", "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onefile",
            "--console",
            "--name", "ac2gtr2",
            "--distpath", dist_dir,
            "--workpath", os.path.join(tmp, "build"),
            "--specpath", tmp,
            "--paths", HERE,
        ]
        for mod in HIDDEN_IMPORTS:
            cmd.extend(["--hidden-import", mod])
        cmd.append(cli_script)
        print("Building ac2gtr2.exe with PyInstaller...", flush=True)
        result = subprocess.run(cmd, cwd=HERE)
        if result.returncode != 0:
            raise SystemExit("PyInstaller failed with exit code %d" % result.returncode)

    exe_path = os.path.join(dist_dir, "ac2gtr2.exe")
    if not os.path.isfile(exe_path):
        raise SystemExit("PyInstaller did not produce %s" % exe_path)
    return exe_path


def find_7z(explicit=None):
    """Locate 7z.exe (explicit flag, PATH, or %ProgramFiles%\\7-Zip)."""
    candidates = []
    if explicit:
        candidates.append(os.path.abspath(explicit))
    for name in ("7z", "7za"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    for pf in _program_files_dirs():
        candidates.append(os.path.join(pf, "7-Zip", "7z.exe"))
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    raise FileNotFoundError(
        "7-Zip (7z.exe) not found; install 7-Zip or pass --7z \"<path to 7z.exe>\""
    )


def release_dist_dir(base_dir=HERE, today=None):
    """Return the dated release directory path ``<base_dir>/ac2gtr2_YYYYMMDD``."""
    day = today if today is not None else datetime.date.today()
    return os.path.join(base_dir, "ac2gtr2_%s" % day.strftime("%Y%m%d"))


def create_release_archive(dist_dir, seven_zip):
    """Compress ``dist_dir`` into ``<dist_dir>.7z`` and write ``<dist_dir>.7z.sha256.txt``.

    Returns ``(archive_path, checksum_path, digest)``.
    """
    dist_dir = os.path.abspath(dist_dir)
    parent_dir = os.path.dirname(dist_dir)
    folder_name = os.path.basename(dist_dir)
    archive_name = folder_name + ".7z"
    archive_path = os.path.join(parent_dir, archive_name)
    checksum_path = archive_path + ".sha256.txt"

    if os.path.exists(archive_path):
        os.remove(archive_path)

    print("\nCompressing %s -> %s ..." % (folder_name, archive_path), flush=True)
    result = subprocess.run(
        [seven_zip, "a", archive_name, folder_name],
        cwd=parent_dir,
    )
    if result.returncode != 0 or not os.path.isfile(archive_path):
        raise SystemExit("7z failed with exit code %d" % result.returncode)

    digest = sha256sum.sha256_file(archive_path)
    with open(checksum_path, "w", encoding="utf-8") as f:
        f.write("%s  %s\n" % (digest, archive_name))
    return archive_path, checksum_path, digest


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dist", default=None,
                    help="output distribution directory (default: ./dist, or ./ac2gtr2_YYYYMMDD with --release)")
    ap.add_argument("--release", action="store_true",
                    help="build into ./ac2gtr2_YYYYMMDD, compress to .7z, and write .7z.sha256.txt")
    ap.add_argument("--7z", dest="seven_zip", default=None,
                    help="path to 7z.exe (used with --release)")
    ap.add_argument("--blender", default=None,
                    help="path to Blender folder or blender.exe")
    ap.add_argument("--addon", default=None,
                    help="path to io_rf2_gmt_release addon folder")
    ap.add_argument("--texconv", default=None,
                    help="path to texconv.exe")
    ap.add_argument("--rfactortools", default=None,
                    help="path to rfactortools directory containing masunpack.exe and rfactorcrypt.exe")
    ap.add_argument("--masunpack", default=None,
                    help="path to masunpack.exe for rFactor 1 / Stock Car Extreme .mas archives")
    ap.add_argument("--rfactorcrypt", default=None,
                    help="path to rfactorcrypt.exe for Stock Car Extreme encrypted .gmt files")
    ap.add_argument("--ac", default=None,
                    help="Assetto Corsa folder containing system\\data\\surfaces.ini")
    ap.add_argument("--gtr2", default=None,
                    help="GTR2 folder containing GameData\\Locations\\specialfx.tec")
    args = ap.parse_args(argv)

    if args.dist:
        dist_dir = os.path.abspath(args.dist)
    elif args.release:
        dist_dir = os.path.abspath(release_dist_dir(HERE))
    else:
        dist_dir = os.path.abspath(os.path.join(HERE, "dist"))

    # Reuse an existing ./dist bundle as fallback source only when building
    # into a different target directory (e.g. --release into ./ac2gtr2_YYYYMMDD).
    default_dist = os.path.abspath(os.path.join(HERE, "dist"))
    fallback_dist = (
        default_dist
        if os.path.isdir(default_dist)
        and os.path.normcase(dist_dir) != os.path.normcase(default_dist)
        else None
    )

    seven_zip = None
    if args.release:
        try:
            seven_zip = find_7z(args.seven_zip)
        except FileNotFoundError as exc:
            raise SystemExit(str(exc)) from exc
    blender_src = find_blender_dir(args.blender, fallback_dist=fallback_dist)
    addon_src = find_addon_dir(blender_src, args.addon) if blender_src else ""
    texconv_src = find_texconv(args.texconv, fallback_dist=fallback_dist)
    masunpack_src = find_masunpack(
        args.masunpack, rfactortools_dir=args.rfactortools, fallback_dist=fallback_dist
    )
    rfactorcrypt_src = find_rfactorcrypt(
        args.rfactorcrypt, masunpack_path=masunpack_src,
        rfactortools_dir=args.rfactortools, fallback_dist=fallback_dist,
    )
    ac_dir = find_ac_dir(args.ac, fallback_dist=fallback_dist)
    gtr2_dir = find_gtr2_dir(args.gtr2, fallback_dist=fallback_dist)

    surfaces_src = os.path.join(ac_dir, "system", "data", "surfaces.ini") if ac_dir else ""
    specialfx_src = os.path.join(gtr2_dir, "GameData", "Locations", "specialfx.tec") if gtr2_dir else ""

    for label, path, check, hint in (
        ("Blender", os.path.join(blender_src, "blender.exe") if blender_src else "", os.path.isfile, "--blender"),
        ("Blender addon", addon_src, os.path.isdir, "--addon"),
        ("texconv.exe", texconv_src, os.path.isfile, "--texconv"),
        ("masunpack.exe", masunpack_src, os.path.isfile, "--rfactortools / --masunpack"),
        ("rfactorcrypt.exe", rfactorcrypt_src, os.path.isfile, "--rfactortools / --rfactorcrypt"),
        ("AC surfaces.ini", surfaces_src, os.path.isfile, "--ac"),
        ("GTR2 specialfx.tec", specialfx_src, os.path.isfile, "--gtr2"),
    ):
        if not path or not check(path):
            raise SystemExit(
                "Required source missing (%s): %s (pass %s)"
                % (label, path or "<not found>", hint)
            )

    print("Recreating %s ..." % dist_dir, flush=True)
    if os.path.exists(dist_dir):
        _rmtree_retry(dist_dir)
    os.makedirs(dist_dir, exist_ok=True)

    # 1. Copy Blender + embed io_rf2_gmt_release directly in Blender's addons folder.
    blender_dst = os.path.join(dist_dir, "tools", "blender")
    print("Copying Blender -> %s ..." % blender_dst, flush=True)
    shutil.copytree(blender_src, blender_dst, ignore=_ignore_blender)
    addon_dst = os.path.join(blender_dst, "3.6", "scripts", "addons", ADDON_NAME)
    if os.path.exists(addon_dst):
        _rmtree_retry(addon_dst)
    print("Copying %s -> %s ..." % (ADDON_NAME, addon_dst), flush=True)
    shutil.copytree(addon_src, addon_dst, ignore=_ignore_pycache)

    # 2. Copy plain scripts executed inside Blender's own Python runtime.
    scripts_dst = os.path.join(dist_dir, "tools", "blender_scripts")
    os.makedirs(scripts_dst, exist_ok=True)
    for script in BLENDER_SCRIPTS:
        shutil.copy2(os.path.join(HERE, script), os.path.join(scripts_dst, script))

    # 3. Copy texconv.exe and rfactortools (masunpack.exe + rfactorcrypt.exe + runtime DLLs).
    texconv_dst = os.path.join(dist_dir, "tools", "texconv")
    os.makedirs(texconv_dst, exist_ok=True)
    shutil.copy2(texconv_src, os.path.join(texconv_dst, "texconv.exe"))

    rftools_src = os.path.dirname(os.path.abspath(masunpack_src))
    rftools_dst = os.path.join(dist_dir, "tools", "rfactortools")
    print("Copying rfactortools -> %s ..." % rftools_dst, flush=True)
    shutil.copytree(rftools_src, rftools_dst, ignore=_ignore_rfactortools)
    if os.path.normcase(os.path.dirname(os.path.abspath(rfactorcrypt_src))) != os.path.normcase(rftools_src):
        shutil.copy2(rfactorcrypt_src, os.path.join(rftools_dst, "rfactorcrypt.exe"))

    # 4. Copy standalone template data from AC and GTR2.
    ac_data_dst = os.path.join(dist_dir, "data", "ac", "system", "data")
    gtr2_loc_dst = os.path.join(dist_dir, "data", "gtr2", "GameData", "Locations")
    os.makedirs(ac_data_dst, exist_ok=True)
    os.makedirs(gtr2_loc_dst, exist_ok=True)
    shutil.copy2(surfaces_src, os.path.join(ac_data_dst, "surfaces.ini"))
    shutil.copy2(specialfx_src, os.path.join(gtr2_loc_dst, "specialfx.tec"))

    # 5. Copy standalone README.md, ac2gtr2.ini, and create out/ and work/ with readme.txt.
    dist_readme = os.path.join(HERE, "DIST_README.md")
    if not os.path.isfile(dist_readme):
        raise SystemExit("Required source missing (DIST_README.md): %s" % dist_readme)
    shutil.copy2(dist_readme, os.path.join(dist_dir, "README.md"))

    ini_src = os.path.join(HERE, steam_paths.INI_NAME)
    if not os.path.isfile(ini_src):
        raise SystemExit("Required source missing (%s): %s" % (steam_paths.INI_NAME, ini_src))
    shutil.copy2(ini_src, os.path.join(dist_dir, steam_paths.INI_NAME))

    out_dir = os.path.join(dist_dir, "out")
    work_dir = os.path.join(dist_dir, "work")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    with open(os.path.join(out_dir, "readme.txt"), "w", encoding="utf-8") as f:
        f.write(OUT_README)
    with open(os.path.join(work_dir, "readme.txt"), "w", encoding="utf-8") as f:
        f.write(WORK_README)

    # 6. Bundle Python runtime + converter CLI into dist/ac2gtr2.exe.
    exe_path = build_exe(dist_dir)

    print("\nStandalone distribution ready: %s" % dist_dir)
    print("  executable:    %s (%.1f MB)" % (
        os.path.relpath(exe_path, dist_dir),
        os.path.getsize(exe_path) / (1024.0 * 1024.0),
    ))
    print("  documentation: README.md")
    print("  tools/blender: %.1f MB (with %s)" % (dir_size_mb(blender_dst), ADDON_NAME))
    print("  tools/texconv: %.1f MB" % dir_size_mb(texconv_dst))
    print("  tools/rftools: %.1f MB" % dir_size_mb(rftools_dst))
    print("  total size:    %.1f MB" % dir_size_mb(dist_dir))

    if args.release:
        archive_path, checksum_path, digest = create_release_archive(dist_dir, seven_zip)
        print("\nRelease archive ready:")
        print("  archive:       %s (%.1f MB)" % (
            archive_path,
            os.path.getsize(archive_path) / (1024.0 * 1024.0),
        ))
        print("  sha256:        %s" % digest)
        print("  checksum file: %s" % checksum_path)

    print("\nUsage:")
    print("  Drag and drop an AC track folder onto %s" % exe_path)
    print("  %s build_track \"<AC track folder or zip>\"" % exe_path)
    print("  %s convert_multiple_tracks \"<tracks.txt>\"" % exe_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
