"""Use an Assetto Corsa track straight from its download zip.

    python track_zip.py "<track>.zip"      prints the extracted track folder

The track is extracted into out\\_zips\\<zip name>\\, not into Assetto Corsa,
and reused while the zip is unchanged. A track folder is the shallowest folder
in the zip that holds .kn5 models: CSP extras keep more of them deeper down,
in extension\\, and a zip may wrap the track in a version folder. Messages go
to stderr, so convert.bat can read the folder from stdout alone.
"""
import os
import shutil
import sys
import zipfile

import steam_paths

HERE = steam_paths.APP_DIR
ZIPS = os.path.join(steam_paths.WORK_ROOT, "_zips")


def say(text):
    sys.stderr.write(text + "\n")
    sys.stderr.flush()


def track_folders(names):
    """The shallowest AC (.kn5) or rFactor 1 (.scn + .gdb) track folders in a zip."""
    kn5_found = set()
    scn_dirs = set()
    gdb_dirs = set()
    mas_dirs = set()
    for name in names:
        parts = name.split("/")
        if "__MACOSX" in parts or parts[-1].startswith("._"):
            continue                    # macOS metadata, not models
        leaf = parts[-1].lower()
        parent = "/".join(parts[:-1])
        if leaf.endswith(".kn5"):
            kn5_found.add(parent)
        elif leaf.endswith(".scn"):
            scn_dirs.add(parent)
        elif leaf.endswith(".gdb"):
            gdb_dirs.add(parent)
        elif leaf.endswith(".mas"):
            mas_dirs.add(parent)

    if kn5_found:
        depth = min(f.count("/") + (1 if f else 0) for f in kn5_found)
        return sorted(f for f in kn5_found if f.count("/") + (1 if f else 0) == depth)

    # rFactor 1 track: layouts are folders holding both .scn and .gdb; if their
    # parent holds .mas files (standard venue layout), return that venue folder.
    rf1_layouts = scn_dirs & gdb_dirs
    if not rf1_layouts:
        return []
    rf1_roots = set()
    for lay in rf1_layouts:
        parent = lay.rsplit("/", 1)[0] if "/" in lay else ""
        if lay and (parent in mas_dirs or len([l for l in rf1_layouts if (l.rsplit("/", 1)[0] if "/" in l else "") == parent]) > 1):
            rf1_roots.add(parent)
        else:
            rf1_roots.add(lay)
    depth = min(f.count("/") + (1 if f else 0) for f in rf1_roots)
    return sorted(f for f in rf1_roots if f.count("/") + (1 if f else 0) == depth)


def extract(zip_path):
    """Extract the zip's one track and return its folder."""
    zip_path = os.path.abspath(zip_path)
    stem = os.path.splitext(os.path.basename(zip_path))[0]
    try:
        z = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise SystemExit("cannot open %s: %s" % (zip_path, exc))
    with z:
        names = [i.filename for i in z.infolist()]
        folders = track_folders(names)
        if not folders:
            raise SystemExit("%s holds no Assetto Corsa (.kn5) or rFactor 1 (.scn/.gdb) track"
                             % os.path.basename(zip_path))
        if len(folders) > 1:
            raise SystemExit("%s holds %d tracks (%s). Extract it and give the "
                             "converter the one you want."
                             % (os.path.basename(zip_path), len(folders),
                                ", ".join(f.split("/")[-1] for f in folders)))
        dest = os.path.join(ZIPS, stem)
        track = os.path.normpath(os.path.join(dest, *folders[0].split("/"))) \
            if folders[0] else dest
        stamp = "%d %d" % (os.path.getsize(zip_path), int(os.path.getmtime(zip_path)))
        marker = os.path.join(dest, ".from_zip")
        try:
            same = open(marker).read() == stamp and os.path.isdir(track)
        except OSError:
            same = False
        if same:
            say("using %s, already extracted" % os.path.basename(zip_path))
            return track
        if os.path.isdir(dest):
            shutil.rmtree(dest)
        say("extracting %s ..." % os.path.basename(zip_path))
        # extractall drops absolute paths and "..", so nothing lands outside dest.
        z.extractall(dest)
    with open(marker, "w") as f:
        f.write(stamp)
    return track


def is_zip(path):
    return path.lower().endswith(".zip") and os.path.isfile(path)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__.splitlines()[2].strip())
    print(extract(sys.argv[1]))
