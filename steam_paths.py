"""Where Assetto Corsa, GTR2, and bundled tools live.

All runtime tools and template game data resolve strictly from the ``dist``
bundle layout — never from hardcoded machine-local paths:

  <dist>/
      ac2gtr2.exe
      ac2gtr2.ini
      tools/blender/blender.exe
      tools/blender_scripts/blender_stage.py
      tools/texconv/texconv.exe
      tools/rfactortools/masunpack.exe
      tools/rfactortools/rfactorcrypt.exe
      data/ac/system/data/surfaces.ini
      data/gtr2/GameData/Locations/specialfx.tec
      work/
      out/

- Running from ``dist`` (frozen ``ac2gtr2.exe`` or directly inside a ``dist``
  folder): always uses dist-local paths next to the executable.
- Running from Python directly in the source tree: requires ``build_dist.py``
  to be run first and loads all tools and template data from
  ``<script_path>/dist/``.
"""
import configparser
import math
import os
import re
import sys

INI_NAME = "ac2gtr2.ini"
DEFAULT_TEXTURE_SCALE = 0.5
DEFAULT_TEXTURE_SCALE_MIN_SIZE = 1024
DEFAULT_ROAD_SMOOTHNESS = 0.5
DEFAULT_MESH_COARSENING = 0.0
DEFAULT_STRIP_UNUSED_GMT_DATA = True
DEFAULT_TRACK_SHADOWS = "compact"
VALID_TRACK_SHADOWS = ("compact", "off", "full")
DEFAULT_TRACK_BRIGHTNESS = 0.5


def validate_track_brightness(brightness):
    """Validate normalized track_brightness in [0.0, 1.0] and return float."""
    value = float(brightness)
    if not math.isfinite(value) or not (0.0 <= value <= 1.0):
        raise ValueError(
            "track_brightness must be between 0.0 and 1.0, got %r"
            % (brightness,)
        )
    return value


def validate_track_shadows(mode):
    """Validate track_shadows mode ('compact', 'off', or 'full')."""
    val = str(mode).strip().lower()
    if val not in VALID_TRACK_SHADOWS:
        raise ValueError(
            "track_shadows must be one of %s, got %r"
            % (", ".join(VALID_TRACK_SHADOWS), mode)
        )
    return val


def parse_bool_option(raw, name):
    """Parse an INI boolean (true/false, 1/0, yes/no, on/off)."""
    low = str(raw).strip().lower()
    if low in ("1", "true", "yes", "on"):
        return True
    if low in ("0", "false", "no", "off"):
        return False
    raise ValueError("%s must be a boolean (true/false), got %r" % (name, raw))


def validate_mesh_coarsening(coarsening):
    """Validate normalized mesh_coarsening in [0.0, 1.0] and return float."""
    value = float(coarsening)
    if not math.isfinite(value) or not (0.0 <= value <= 1.0):
        raise ValueError(
            "mesh_coarsening must be between 0.0 and 1.0, got %r" % (coarsening,)
        )
    return value


def road_smoothness_to_sigma(smoothness):
    """Map normalized road_smoothness in [0.0, 1.0] uniformly to sigma in [0.0, 8.0] m.

      0.0 -> 0.0m (disabled)
      0.5 -> 4.0m (default)
      1.0 -> 8.0m (max)
    """
    value = float(smoothness)
    if not math.isfinite(value) or not (0.0 <= value <= 1.0):
        raise ValueError(
            "road_smoothness must be between 0.0 and 1.0, got %r" % (smoothness,)
        )
    if value == 0.0:
        return 0.0
    return round(value * 8.0, 6)


def app_dir():
    """Directory containing the running EXE (frozen) or repository scripts."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _steam_default():
    pf = os.environ.get("ProgramFiles(x86)") or os.environ.get("ProgramFiles") or ""
    return os.path.join(pf, "Steam") if pf else ""


def steam_root():
    """Steam's own folder: from the Windows registry, else ProgramFiles\\Steam."""
    default = _steam_default()
    try:
        import winreg
    except ImportError:
        return default
    keys = (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    )
    for hive, key, value in keys:
        try:
            with winreg.OpenKey(hive, key) as k:
                path = winreg.QueryValueEx(k, value)[0]
        except OSError:
            continue
        if path and os.path.isdir(path):
            return os.path.realpath(path)
    return default


def libraries():
    """Every Steam library folder, Steam's own first."""
    root = steam_root()
    out = [root] if root else []
    default = _steam_default()
    if default and os.path.normcase(default) not in (os.path.normcase(p) for p in out):
        out.append(default)
    if not root:
        return out
    vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
    try:
        with open(vdf, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return out
    for path in re.findall(r'"path"\s+"([^"]+)"', text):
        path = os.path.normpath(path.replace("\\\\", "\\"))
        if os.path.normcase(path) not in (os.path.normcase(p) for p in out):
            out.append(path)
    return out


def game_dir(folder):
    """steamapps/common/<folder> in whichever Steam library has it, else empty."""
    for lib in libraries():
        p = os.path.join(lib, "steamapps", "common", folder)
        if os.path.isdir(p):
            return p
    root = steam_root()
    return os.path.join(root, "steamapps", "common", folder) if root else ""


def find_gtr2_dir():
    """Locate a GTR2 installation in Steam libraries (used by build_dist.py)."""
    for folder in ("GTR 2 - FIA GT Racing Game", "GTR2"):
        for lib in libraries():
            p = os.path.join(lib, "steamapps", "common", folder)
            if os.path.isdir(p):
                return p
    root = steam_root()
    return os.path.join(root, "steamapps", "common", "GTR 2 - FIA GT Racing Game") if root else ""


def _has_dist_layout(root):
    """Return True when ``root`` itself is a standalone dist directory."""
    return (
        os.path.isdir(os.path.join(root, "tools"))
        and os.path.isdir(os.path.join(root, "data"))
    )


def dist_ready(tools_root=None):
    """Return True when the dist tools/ and data/ directories exist."""
    root = TOOLS_ROOT if tools_root is None else tools_root
    return _has_dist_layout(root)


def find_sce_common_dirs():
    """Return existing Reiza shared asset directories (ReizaCommon / Locations) if installed.

    Searches ``AC2GTR2_SCE_DIR`` first, then Steam installations of Stock Car
    Extreme and Automobilista (which share ``ReizaCommon`` and ``commonmaps.MAS``).
    """
    roots = []
    env_dir = os.environ.get("AC2GTR2_SCE_DIR")
    if env_dir:
        roots.append(os.path.abspath(env_dir))
    for folder in ("Stock Car Extreme", "Game Stock Car Extreme", "GSC2013", "Automobilista"):
        for lib in libraries():
            p = os.path.join(lib, "steamapps", "common", folder)
            if os.path.isdir(p):
                roots.append(p)
    found = []
    seen = set()
    for r in roots:
        for cand in (
            os.path.join(r, "GameData", "Locations", "ReizaCommon"),
            os.path.join(r, "GameData", "Locations"),
            os.path.join(r, "ReizaCommon"),
            r,
        ):
            if os.path.isdir(cand):
                key = os.path.normcase(os.path.abspath(cand))
                if key not in seen:
                    seen.add(key)
                    found.append(os.path.abspath(cand))
    return found


def resolve_paths(app_root=None, frozen=None, env=None):
    """Resolve all tool, data, work, and install paths.

    - Running from ``dist`` (frozen EXE or directly inside a ``dist`` folder):
      always uses dist-local paths under ``app_root``.
    - Running from Python directly: requires ``build_dist.py`` to be run first
      and resolves all tools and template data from ``<app_root>/dist/``
      (honoring explicit ``AC2GTR2_*`` CLI overrides when set).
    """
    is_frozen = getattr(sys, "frozen", False) if frozen is None else bool(frozen)
    root = os.path.abspath(app_root) if app_root is not None else app_dir()
    environ = os.environ if env is None else env

    is_standalone = bool(is_frozen or _has_dist_layout(root))
    dist_dir = root if is_standalone else os.path.join(root, "dist")
    tools_root = dist_dir

    bundled_blender = os.path.join(tools_root, "tools", "blender", "blender.exe")
    bundled_blender_stage = os.path.join(tools_root, "tools", "blender_scripts", "blender_stage.py")
    bundled_texconv = os.path.join(tools_root, "tools", "texconv", "texconv.exe")
    bundled_masunpack = os.path.join(tools_root, "tools", "rfactortools", "masunpack.exe")
    bundled_rfactorcrypt = os.path.join(tools_root, "tools", "rfactortools", "rfactorcrypt.exe")
    bundled_ac_dir = os.path.join(tools_root, "data", "ac")
    bundled_gtr2_dir = os.path.join(tools_root, "data", "gtr2")

    if is_standalone:
        # Running from dist: always use dist-local tool and data paths.
        gtr2_dir = bundled_gtr2_dir
        ac_dir = bundled_ac_dir
        texconv = bundled_texconv
        masunpack = bundled_masunpack
        rfactorcrypt = bundled_rfactorcrypt
        blender = bundled_blender
        blender_stage = bundled_blender_stage
    else:
        # Running from Python directly: get all tools from <script_path>/dist/
        # (with explicit CLI overrides passed via AC2GTR2_* env vars).
        gtr2_dir = environ.get("AC2GTR2_GAME_DIR") or bundled_gtr2_dir
        ac_dir = environ.get("AC2GTR2_AC_DIR") or bundled_ac_dir
        texconv = environ.get("AC2GTR2_TEXCONV") or bundled_texconv
        masunpack = environ.get("AC2GTR2_MASUNPACK") or bundled_masunpack
        rfactorcrypt = environ.get("AC2GTR2_RFACTORCRYPT") or bundled_rfactorcrypt
        blender = environ.get("AC2GTR2_BLENDER") or bundled_blender
        local_stage = os.path.join(root, "blender_stage.py")
        blender_stage = local_stage if os.path.isfile(local_stage) else bundled_blender_stage

    locations = os.path.join(gtr2_dir, "GameData", "Locations")
    if is_standalone:
        install_locations = os.path.join(root, "out")
        work_root = os.path.join(root, "work")
    else:
        work_root = os.path.join(root, "out")
        install_locations = (
            locations
            if os.path.normcase(gtr2_dir) != os.path.normcase(bundled_gtr2_dir)
            else os.path.join(dist_dir, "out")
        )

    return {
        "APP_DIR": root,
        "DIST_DIR": dist_dir,
        "TOOLS_ROOT": tools_root,
        "IS_STANDALONE": is_standalone,
        "BUNDLED_BLENDER": bundled_blender,
        "BUNDLED_BLENDER_STAGE": bundled_blender_stage,
        "BUNDLED_TEXCONV": bundled_texconv,
        "BUNDLED_MASUNPACK": bundled_masunpack,
        "BUNDLED_RFACTORCRYPT": bundled_rfactorcrypt,
        "BUNDLED_AC_DIR": bundled_ac_dir,
        "BUNDLED_GTR2_DIR": bundled_gtr2_dir,
        "GTR2_DIR": gtr2_dir,
        "AC_DIR": ac_dir,
        "TEXCONV": texconv,
        "MASUNPACK": masunpack,
        "RFACTORCRYPT": rfactorcrypt,
        "BLENDER": blender,
        "BLENDER_STAGE": blender_stage,
        "LOCATIONS": locations,
        "INSTALL_LOCATIONS": install_locations,
        "WORK_ROOT": work_root,
    }


_RESOLVED = resolve_paths()
APP_DIR = _RESOLVED["APP_DIR"]
DIST_DIR = _RESOLVED["DIST_DIR"]
TOOLS_ROOT = _RESOLVED["TOOLS_ROOT"]
IS_STANDALONE = _RESOLVED["IS_STANDALONE"]
BUNDLED_BLENDER = _RESOLVED["BUNDLED_BLENDER"]
BUNDLED_BLENDER_STAGE = _RESOLVED["BUNDLED_BLENDER_STAGE"]
BUNDLED_TEXCONV = _RESOLVED["BUNDLED_TEXCONV"]
BUNDLED_MASUNPACK = _RESOLVED["BUNDLED_MASUNPACK"]
BUNDLED_RFACTORCRYPT = _RESOLVED["BUNDLED_RFACTORCRYPT"]
BUNDLED_AC_DIR = _RESOLVED["BUNDLED_AC_DIR"]
BUNDLED_GTR2_DIR = _RESOLVED["BUNDLED_GTR2_DIR"]
GTR2_DIR = _RESOLVED["GTR2_DIR"]
AC_DIR = _RESOLVED["AC_DIR"]
TEXCONV = _RESOLVED["TEXCONV"]
MASUNPACK = _RESOLVED["MASUNPACK"]
RFACTORCRYPT = _RESOLVED["RFACTORCRYPT"]
BLENDER = _RESOLVED["BLENDER"]
BLENDER_STAGE = _RESOLVED["BLENDER_STAGE"]
LOCATIONS = _RESOLVED["LOCATIONS"]
INSTALL_LOCATIONS = _RESOLVED["INSTALL_LOCATIONS"]
WORK_ROOT = _RESOLVED["WORK_ROOT"]


def load_config(base_dir=None):
    """Read optional ``ac2gtr2.ini`` from the script/EXE directory."""
    root = base_dir if base_dir is not None else APP_DIR
    ini_path = os.path.join(root, INI_NAME)
    result = {
        "path": ini_path,
        "exists": False,
        "texture_scale": DEFAULT_TEXTURE_SCALE,
        "texture_scale_source": "default",
        "texture_scale_min_size": DEFAULT_TEXTURE_SCALE_MIN_SIZE,
        "texture_scale_min_size_source": "default",
        "road_smoothness": DEFAULT_ROAD_SMOOTHNESS,
        "road_smoothness_source": "default",
        "road_smooth_sigma": road_smoothness_to_sigma(DEFAULT_ROAD_SMOOTHNESS),
        "mesh_coarsening": DEFAULT_MESH_COARSENING,
        "mesh_coarsening_source": "default",
        "strip_unused_gmt_data": DEFAULT_STRIP_UNUSED_GMT_DATA,
        "strip_unused_gmt_data_source": "default",
        "track_shadows": DEFAULT_TRACK_SHADOWS,
        "track_shadows_source": "default",
        "track_brightness": DEFAULT_TRACK_BRIGHTNESS,
        "track_brightness_source": "default",
    }
    if not os.path.isfile(ini_path):
        return result

    parser = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    try:
        with open(ini_path, encoding="utf-8-sig") as f:
            parser.read_file(f)
    except (OSError, configparser.Error) as exc:
        raise ValueError("could not parse %s: %s" % (ini_path, exc)) from exc

    result["exists"] = True
    if parser.has_option("track", "texture_scale"):
        raw = parser.get("track", "texture_scale").strip()
        try:
            scale = float(raw)
        except ValueError as exc:
            raise ValueError(
                "[track] texture_scale must be a number, got %r" % (raw,)
            ) from exc
        if not (0.0 < scale <= 1.0):
            raise ValueError(
                "[track] texture_scale must be between 0 and 1, got %r" % (scale,)
            )
        result["texture_scale"] = scale
        result["texture_scale_source"] = "ini"
    if parser.has_option("track", "texture_scale_min_size"):
        raw = parser.get("track", "texture_scale_min_size").strip()
        try:
            min_size = int(raw)
        except ValueError as exc:
            raise ValueError(
                "[track] texture_scale_min_size must be an integer, got %r" % (raw,)
            ) from exc
        if min_size < 0:
            raise ValueError(
                "[track] texture_scale_min_size must be >= 0, got %r" % (min_size,)
            )
        result["texture_scale_min_size"] = min_size
        result["texture_scale_min_size_source"] = "ini"
    if parser.has_option("track", "road_smoothness"):
        raw = parser.get("track", "road_smoothness").strip()
        try:
            smoothness = float(raw)
        except ValueError as exc:
            raise ValueError(
                "[track] road_smoothness must be a number, got %r" % (raw,)
            ) from exc
        if not math.isfinite(smoothness) or not (0.0 <= smoothness <= 1.0):
            raise ValueError(
                "[track] road_smoothness must be between 0.0 and 1.0, got %r"
                % (smoothness,)
            )
        result["road_smoothness"] = smoothness
        result["road_smoothness_source"] = "ini"
        result["road_smooth_sigma"] = road_smoothness_to_sigma(smoothness)
    if parser.has_option("track", "mesh_coarsening"):
        raw = parser.get("track", "mesh_coarsening").strip()
        try:
            coarsening = float(raw)
        except ValueError as exc:
            raise ValueError(
                "[track] mesh_coarsening must be a number, got %r" % (raw,)
            ) from exc
        if not math.isfinite(coarsening) or not (0.0 <= coarsening <= 1.0):
            raise ValueError(
                "[track] mesh_coarsening must be between 0.0 and 1.0, got %r"
                % (coarsening,)
            )
        result["mesh_coarsening"] = coarsening
        result["mesh_coarsening_source"] = "ini"
    if parser.has_option("track", "strip_unused_gmt_data"):
        raw = parser.get("track", "strip_unused_gmt_data").strip()
        result["strip_unused_gmt_data"] = parse_bool_option(
            raw, "[track] strip_unused_gmt_data"
        )
        result["strip_unused_gmt_data_source"] = "ini"
    elif parser.has_option("track", "strip_non_collidable_faces"):
        raw = parser.get("track", "strip_non_collidable_faces").strip()
        result["strip_unused_gmt_data"] = parse_bool_option(
            raw, "[track] strip_non_collidable_faces"
        )
        result["strip_unused_gmt_data_source"] = "ini"
    if parser.has_option("track", "track_shadows"):
        raw = parser.get("track", "track_shadows").strip()
        result["track_shadows"] = validate_track_shadows(raw)
        result["track_shadows_source"] = "ini"
    if parser.has_option("track", "track_brightness"):
        raw = parser.get("track", "track_brightness").strip()
        try:
            brightness = float(raw)
        except ValueError as exc:
            raise ValueError(
                "[track] track_brightness must be a number, got %r" % (raw,)
            ) from exc
        if not math.isfinite(brightness) or not (0.0 <= brightness <= 1.0):
            raise ValueError(
                "[track] track_brightness must be between 0.0 and 1.0, got %r"
                % (brightness,)
            )
        result["track_brightness"] = brightness
        result["track_brightness_source"] = "ini"
    return result


if __name__ == "__main__":
    if "--locations" in sys.argv:
        print(INSTALL_LOCATIONS)
        sys.exit(0)
    print("Standalone:    %s" % IS_STANDALONE)
    print("App dir:       %s" % APP_DIR)
    print("GTR2:          %s" % GTR2_DIR)
    print("Assetto Corsa: %s" % AC_DIR)
    print("Blender:       %s (%s)" % (BLENDER, "found" if os.path.exists(BLENDER) else "MISSING"))
    print("texconv:       %s (%s)" % (TEXCONV, "found" if os.path.exists(TEXCONV) else "MISSING"))
    print("work root:     %s" % WORK_ROOT)
    print("install into:  %s" % INSTALL_LOCATIONS)
