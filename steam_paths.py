"""Where Assetto Corsa, GTR2, and bundled/external tools live.

In a standalone ``dist`` layout (next to ``ac2gtr2.exe``), all tools and template
game data resolve relative to the executable directory so the converter runs on
a clean Windows machine with no AC, GTR2, rF2, Python, or Blender installed:

  <dist>/
      ac2gtr2.exe
      ac2gtr2.ini
      tools/blender/blender.exe
      tools/blender_scripts/blender_stage.py
      tools/texconv/texconv.exe
      data/ac/system/data/surfaces.ini
      data/gtr2/GameData/Locations/specialfx.tec
      work/
      out/

Outside a standalone bundle, Steam libraries, registry paths, and common GTR2
install locations are used, and environment variables (AC2GTR2_GAME_DIR,
AC2GTR2_AC_DIR, AC2GTR2_TEXCONV, AC2GTR2_BLENDER) can override any path.
"""
import configparser
import math
import os
import re
import shutil
import sys

STEAM_DEFAULT = r"C:\Program Files (x86)\Steam"
DEFAULT_BLENDER = r"D:\Apps\Blender\blender.exe"
DEFAULT_GTR2_CANDIDATES = (
    r"D:\Games\GTR2\GTR2_HQ_PnG3",
    r"C:\GTR2",
    r"D:\GTR2",
)
DEFAULT_MASUNPACK_CANDIDATES = (
    r"D:\Mods\Automobilista\Tools\rfactortools-0.3.1.win32\masunpack.exe",
)
INI_NAME = "ac2gtr2.ini"
DEFAULT_TEXTURE_SCALE = 0.5
DEFAULT_TEXTURE_SCALE_MIN_SIZE = 1024
DEFAULT_ROAD_SMOOTHNESS = 0.5
DEFAULT_MESH_COARSENING = 0.0
DEFAULT_STRIP_UNUSED_GMT_DATA = True
DEFAULT_TRACK_SHADOWS = "compact"
VALID_TRACK_SHADOWS = ("compact", "off", "full")
DEFAULT_TRACK_BRIGHTNESS = 0.0


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


def steam_root():
    """Steam's own folder: from the registry, else the default install path."""
    try:
        import winreg
    except ImportError:
        return STEAM_DEFAULT
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
    return STEAM_DEFAULT


def libraries():
    """Every Steam library folder, Steam's own first."""
    root = steam_root()
    out = [root]
    if os.path.normcase(STEAM_DEFAULT) != os.path.normcase(root):
        out.append(STEAM_DEFAULT)
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
    """steamapps/common/<folder> in whichever library has it, else Steam's own."""
    for lib in libraries():
        p = os.path.join(lib, "steamapps", "common", folder)
        if os.path.isdir(p):
            return p
    return os.path.join(steam_root(), "steamapps", "common", folder)


def find_gtr2_dir():
    """Locate a GTR2 installation in Steam libraries or common install folders."""
    for folder in ("GTR 2 - FIA GT Racing Game", "GTR2"):
        for lib in libraries():
            p = os.path.join(lib, "steamapps", "common", folder)
            if os.path.isdir(p):
                return p
    for cand in DEFAULT_GTR2_CANDIDATES:
        if os.path.isdir(cand):
            return cand
    return os.path.join(steam_root(), "steamapps", "common", "GTR 2 - FIA GT Racing Game")


APP_DIR = app_dir()
BUNDLED_BLENDER = os.path.join(APP_DIR, "tools", "blender", "blender.exe")
BUNDLED_BLENDER_STAGE = os.path.join(APP_DIR, "tools", "blender_scripts", "blender_stage.py")
BUNDLED_TEXCONV = os.path.join(APP_DIR, "tools", "texconv", "texconv.exe")
BUNDLED_MASUNPACK = os.path.join(APP_DIR, "tools", "rfactortools", "masunpack.exe")
BUNDLED_AC_DIR = os.path.join(APP_DIR, "data", "ac")
BUNDLED_GTR2_DIR = os.path.join(APP_DIR, "data", "gtr2")


def find_masunpack():
    """Locate masunpack.exe (bundled, Tools folder, or PATH)."""
    if os.path.isfile(BUNDLED_MASUNPACK):
        return BUNDLED_MASUNPACK
    for cand in DEFAULT_MASUNPACK_CANDIDATES:
        if os.path.isfile(cand):
            return cand
    found = shutil.which("masunpack")
    if found:
        return found
    return DEFAULT_MASUNPACK_CANDIDATES[0]

IS_STANDALONE = bool(
    getattr(sys, "frozen", False)
    or (os.path.isfile(BUNDLED_BLENDER) and os.path.isdir(os.path.join(APP_DIR, "data")))
)

_bundled_gtr2_ok = os.path.isfile(
    os.path.join(BUNDLED_GTR2_DIR, "GameData", "Locations", "specialfx.tec")
)
_bundled_ac_ok = os.path.isfile(
    os.path.join(BUNDLED_AC_DIR, "system", "data", "surfaces.ini")
)

GTR2_DIR = (
    os.environ.get("AC2GTR2_GAME_DIR")
    or (BUNDLED_GTR2_DIR if _bundled_gtr2_ok else find_gtr2_dir())
)
AC_DIR = (
    os.environ.get("AC2GTR2_AC_DIR")
    or (BUNDLED_AC_DIR if _bundled_ac_ok else game_dir("assettocorsa"))
)
RF2_DIR = os.environ.get("AC2GTR2_RF2_DIR") or game_dir("rFactor 2")
TEXCONV = (
    os.environ.get("AC2GTR2_TEXCONV")
    or (BUNDLED_TEXCONV if os.path.isfile(BUNDLED_TEXCONV)
        else os.path.join(RF2_DIR, "Support", "Tools", "MapConverter", "texconv.exe"))
)
MASUNPACK = os.environ.get("AC2GTR2_MASUNPACK") or find_masunpack()
BLENDER = (
    os.environ.get("AC2GTR2_BLENDER")
    or (BUNDLED_BLENDER if os.path.isfile(BUNDLED_BLENDER) else DEFAULT_BLENDER)
)
BLENDER_STAGE = (
    BUNDLED_BLENDER_STAGE
    if os.path.isfile(BUNDLED_BLENDER_STAGE)
    else os.path.join(APP_DIR, "blender_stage.py")
)

LOCATIONS = os.path.join(GTR2_DIR, "GameData", "Locations")
INSTALL_LOCATIONS = os.path.join(APP_DIR, "out") if IS_STANDALONE else LOCATIONS
WORK_ROOT = os.path.join(APP_DIR, "work") if IS_STANDALONE else os.path.join(APP_DIR, "out")


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
