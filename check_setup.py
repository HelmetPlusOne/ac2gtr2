"""Check that everything a conversion needs is in place, before it starts.

    python check_setup.py ["<AC track folder>"] [--layout <name>]
                          [--blender <path>] [--gtr2 <folder>] [--ac <folder>]

build_track.py runs the same checks first, so a missing piece is reported in a
few seconds, in plain words, instead of failing minutes into a build.
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON = "io_rf2_gmt_release"
ADDON_PAGE = ("https://forum.studio-397.com/index.php?threads/"
              "blender-rfactor2-export-scripts.74821/")
MIN_FREE_GB = 10

PROBE = (
    "import sys, bpy, addon_utils\n"
    "print('AC2GTR2 VERSION', bpy.app.version_string)\n"
    "m = addon_utils.enable(%r, default_set=False)\n"
    "v = m.bl_info.get('version') if m else None\n"
    "print('AC2GTR2 ADDON', '.'.join(map(str, v)) if v else ('ok' if m else 'missing'))\n"
    "sys.stdout.flush()\n" % ADDON
)


class Report:
    def __init__(self):
        self.problems = 0

    def ok(self, what):
        print("   ok       %s" % what)

    def note(self, what):
        print("   note     %s" % what)

    def problem(self, what, fix=None):
        self.problems += 1
        print("   PROBLEM  %s" % what)
        if fix:
            for line in fix.splitlines():
                print("            %s" % line)


def layouts(track_dir):
    return sorted(f[len("models_"):-len(".ini")] for f in os.listdir(track_dir)
                  if f.lower().startswith("models_") and f.lower().endswith(".ini"))


def check_track(rep, track_dir, layout):
    if os.path.isfile(track_dir):
        ext = os.path.splitext(track_dir)[1].lower()
        if ext in (".rar", ".7z"):
            rep.problem("%s is a %s archive" % (os.path.basename(track_dir), ext),
                        "Extract it first (7-Zip or WinRAR) and give the converter "
                        "the track folder. Only .zip files are extracted automatically.")
        else:
            rep.problem("%s is a file, not a track folder" % track_dir)
        return
    if not os.path.isdir(track_dir):
        rep.problem("track folder not found: %s" % track_dir)
        return
    name = os.path.basename(os.path.normpath(track_dir))
    if not any(f.lower().endswith(".kn5") for f in os.listdir(track_dir)):
        rep.problem("no .kn5 model files in %s" % track_dir,
                    "Give the track's own folder, e.g. "
                    "...\\assettocorsa\\content\\tracks\\ks_silverstone")
        return
    have = layouts(track_dir)
    if layout:
        if layout not in have:
            rep.problem("track %s has no layout called %r" % (name, layout),
                        "Its layouts: %s" % (", ".join(have) or "none (leave --layout out)"))
            return
        rep.ok("track %s, layout %s" % (name, layout))
        return
    default = (os.path.exists(os.path.join(track_dir, "models.ini"))
               or os.path.isdir(os.path.join(track_dir, "ai")))
    if have and not default:
        rep.problem("track %s has no default layout: choose one" % name,
                    "Its layouts: %s\n(--layout <name>, or type it when convert.bat asks)"
                    % ", ".join(have))
        return
    rep.ok("track %s" % name)


def check_blender(rep, blender):
    if not os.path.isfile(blender):
        rep.problem("Blender not found at %s" % blender,
                    "Install Blender 3.6 LTS, or pass --blender \"<path to blender.exe>\"")
        return
    try:
        r = subprocess.run([blender, "--background", "--factory-startup",
                            "--python-expr", PROBE],
                           capture_output=True, text=True, timeout=180)
        out = (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        rep.problem("Blender at %s did not start: %s" % (blender, exc))
        return
    found = {}
    for line in out.splitlines():
        if line.startswith("AC2GTR2 "):
            key, _, value = line[len("AC2GTR2 "):].partition(" ")
            found[key] = value.strip()
    version = found.get("VERSION")
    if not version:
        rep.problem("Blender at %s did not run the check" % blender,
                    "Its output ended with:\n" + "\n".join(out.splitlines()[-5:]))
        return
    rep.ok("Blender %s" % version)
    if not version.startswith("3.6"):
        rep.note("the converter is tested with Blender 3.6 LTS only; "
                 "the GMT exporter supports 3.3 to 3.6")
    addon = found.get("ADDON", "missing")
    if addon == "missing":
        rep.problem("the GMT exporter add-on (%s) is not installed in this Blender" % ADDON,
                    "Download it from the Studio-397 forum, thread \"Blender rFactor2 "
                    "Export Scripts\":\n%s\nThen in Blender: Edit > Preferences > "
                    "Add-ons > Install..., and pick the downloaded zip." % ADDON_PAGE)
    else:
        rep.ok("GMT exporter %s" % addon)


def check(track_dir=None, layout=None, blender=None, install=None,
          need_blender=True, texture_scale=None, texture_scale_min_size=None,
          road_smoothness=None, mesh_coarsening=None,
          strip_unused_gmt_data=None):
    """Print one line per check; True when nothing is missing."""
    import steam_paths
    rep = Report()
    print("checking setup")

    try:
        cfg = steam_paths.load_config()
    except ValueError as exc:
        rep.problem("config error: %s" % exc)
        cfg = None

    if cfg is not None:
        def _fmt(key, cli_val):
            cfg_val = cfg[key]
            src = cfg.get(key + "_source", "default")
            if cli_val is not None and cli_val != cfg_val:
                return "%s=%s [from CLI, %s=%s]" % (key, cli_val, src, cfg_val)
            tag = "from ini" if src == "ini" else "default"
            return "%s=%s [%s]" % (key, cfg_val if cli_val is None else cli_val, tag)

        active_smoothness = (
            cfg["road_smoothness"] if road_smoothness is None else road_smoothness
        )
        eff_sigma = steam_paths.road_smoothness_to_sigma(active_smoothness)
        smooth_fmt = _fmt("road_smoothness", road_smoothness)
        if eff_sigma == 0.0:
            smooth_fmt += " (effective road_smooth_sigma=0.0, disabled)"
        else:
            smooth_fmt += " (effective road_smooth_sigma=%sm)" % eff_sigma
        active_coarsening = (
            cfg["mesh_coarsening"] if mesh_coarsening is None else mesh_coarsening
        )
        coarsen_fmt = _fmt("mesh_coarsening", mesh_coarsening)
        if active_coarsening == 0.0:
            coarsen_fmt += " (disabled)"
        parts = [
            _fmt("texture_scale", texture_scale),
            _fmt("texture_scale_min_size", texture_scale_min_size),
            smooth_fmt,
            coarsen_fmt,
            _fmt("strip_unused_gmt_data", strip_unused_gmt_data),
        ]
        summary = ", ".join(parts)
        if cfg["exists"]:
            rep.ok("config         %s (%s)" % (cfg["path"], summary))
        else:
            rep.note("config         %s not found (%s)" % (cfg["path"], summary))

    v = sys.version_info
    if v < (3, 10):
        rep.problem("Python %d.%d is too old" % v[:2],
                    "Install Python 3.10 or newer from python.org")
    else:
        rep.ok("Python %d.%d.%d" % v[:3])
    try:
        import numpy
        rep.ok("NumPy %s" % numpy.__version__)
    except ImportError:
        rep.problem("NumPy is not installed for this Python (%s)" % sys.executable,
                    "Run:  python -m pip install numpy")

    surfaces = os.path.join(steam_paths.AC_DIR, "system", "data", "surfaces.ini")
    if os.path.exists(surfaces):
        rep.ok("Assetto Corsa  %s" % steam_paths.AC_DIR)
    else:
        rep.problem("Assetto Corsa not found (no %s)" % surfaces,
                    "If it is installed somewhere Steam does not list, pass "
                    "--ac \"<assettocorsa folder>\"")

    if not os.path.isdir(steam_paths.GTR2_DIR):
        rep.problem("GTR2 not found (looked for %s)" % steam_paths.GTR2_DIR,
                    "If it is installed somewhere else, pass "
                    "--gtr2 \"<GTR2 folder>\"")
    else:
        rep.ok("GTR2           %s" % steam_paths.GTR2_DIR)
        sfx = os.path.join(steam_paths.LOCATIONS, "specialfx.tec")
        if not os.path.exists(sfx):
            rep.note("GameData\\Locations\\specialfx.tec not found at %s" % sfx)

    if os.path.isfile(steam_paths.TEXCONV):
        rep.ok("texconv.exe    %s" % steam_paths.TEXCONV)
    else:
        rep.problem("texconv.exe not found at %s" % steam_paths.TEXCONV,
                    "Install DirectXTex texconv.exe or pass --texconv \"<path>\"")

    if install:
        if os.path.isdir(install):
            rep.ok("install into   %s" % install)
        else:
            rep.problem("install folder not found: %s" % install)

    if track_dir:
        check_track(rep, os.path.abspath(track_dir), layout)

    if need_blender:
        check_blender(rep, blender)

    free = shutil.disk_usage(steam_paths.APP_DIR).free / 1e9
    if free < MIN_FREE_GB:
        rep.note("only %.0f GB free on this drive; a build writes several GB "
                 "into out\\" % free)

    if rep.problems:
        print("\n%d problem(s) found: fix them and run again." % rep.problems)
        return False
    return True


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("track_dir", nargs="?")
    ap.add_argument("--layout")
    ap.add_argument("--blender")
    ap.add_argument("--gtr2")
    ap.add_argument("--ac")
    ap.add_argument("--texconv")
    args = ap.parse_args()
    if args.gtr2:
        os.environ["AC2GTR2_GAME_DIR"] = os.path.abspath(args.gtr2)
    if args.ac:
        os.environ["AC2GTR2_AC_DIR"] = os.path.abspath(args.ac)
    if args.texconv:
        os.environ["AC2GTR2_TEXCONV"] = os.path.abspath(args.texconv)
    import steam_paths
    if not args.blender:
        args.blender = steam_paths.BLENDER
    import track_zip
    if args.track_dir and track_zip.is_zip(args.track_dir):
        args.track_dir = track_zip.extract(args.track_dir)
    ok = check(args.track_dir, args.layout, args.blender, steam_paths.INSTALL_LOCATIONS)
    if ok:
        print("\nall good")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
