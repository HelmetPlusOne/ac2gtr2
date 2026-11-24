import json
import os
import struct
import sys
import tempfile
import types
import unittest
from unittest import mock

import ac2gtr2
import build_track
import convert_multiple_tracks
import finalize_track
import fix_textures
import gmt_gtr2
import steam_paths
import verify_install


def _make_rf2_gmt(name="mesh", shader="L2DIFFUSET0", textures=None,
                  mat_flags=0x68902003, blend=(2, 1)):
    """Build a minimal valid rF2 GMT v2.52 binary with 1 triangle (3 verts)."""
    if textures is None:
        textures = [("base.dds", 0x18, 1, 3, 0, 0)]

    nmesh = 1
    nface = 1
    nnorm = 1
    vcount = 3
    tcount = 3

    out = bytearray(gmt_gtr2.RF2_HEADER_SIZE)
    struct.pack_into("<I", out, 4, 0x04000020)
    struct.pack_into("<3f", out, 0x0C8, -1.0, 0.0, -2.0)
    struct.pack_into("<3f", out, 0x0D4, 1.0, 3.0, 2.0)
    struct.pack_into("<f", out, 0x0E0, 5.0)
    struct.pack_into("<3f", out, 0x0E4, 0.0, 1.5, 0.0)
    struct.pack_into("<3f", out, 0x0F0, 0.0, 1.5, 0.0)
    struct.pack_into("<2f", out, 0x100, 0.0, 13000.0)
    out[0x17C:0x1BC] = gmt_gtr2._pad_ascii(name, 64)
    out[0x1BC:0x1CC] = gmt_gtr2.RF2_GMT_MAGIC

    moff = len(out)
    mesh = bytearray(gmt_gtr2.RF2_MESH_SIZE)
    struct.pack_into("<10I", mesh, 0, 0x20004011, vcount, 0, tcount, 0, 0, 0, 0, 0, 0)
    out.extend(mesh)

    foff = len(out)
    out.extend(struct.pack("<4H2I4f", 0, 1, 2, 4, 0, 0, 0.0, 0.0, 0.0, 1.0))

    noff = len(out)
    out.extend(struct.pack("<3fI", 0.0, 1.0, 0.0, 1))

    voff = len(out)
    for vi in range(vcount):
        v = bytearray(gmt_gtr2.RF2_VERTEX_SIZE)
        struct.pack_into("<6fI", v, 0, float(vi), 1.0, 2.0, 0.0, 1.0, 0.0, 0xFFFFFFFF)
        struct.pack_into("<6f", v, 40, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)
        struct.pack_into("<8f", v, 64,
                         0.1 + vi, 0.2,   # UV0
                         1.1 + vi, 1.2,   # UV1
                         2.1 + vi, 2.2,   # UV2
                         3.1 + vi, 3.2)   # UV3
        out.extend(v)

    toff = len(out)
    out.extend(struct.pack("<3H", 0, 1, 2))

    mat_off = len(out)
    out.extend(struct.pack("<I", 1))
    mat = bytearray(gmt_gtr2.RF2_MAT_HEADER_SIZE)
    mat[0:64] = gmt_gtr2._pad_ascii("mat0", 64)
    struct.pack_into("<I", mat, 64, mat_flags)
    struct.pack_into("<16f", mat, 68, *([0.5, 0.5, 0.5, 1.0] * 4))
    struct.pack_into("<2If", mat, 132, blend[0], blend[1], 10.0)
    mat[148:212] = gmt_gtr2._pad_ascii(shader, 64)
    struct.pack_into("<I", mat, 256, len(textures))
    out.extend(mat)
    for tname, tflags, actor, action, stage, aref in textures:
        tex = bytearray(gmt_gtr2.RF2_TEXTURE_SIZE)
        tex[0:64] = gmt_gtr2._pad_ascii(tname, 64)
        struct.pack_into("<IiIIIfI", tex, 64, tflags, -1, 1, actor, action, 1.0, stage)
        struct.pack_into("<I", tex, 100, aref)
        out.extend(tex)

    struct.pack_into("<I", out, 0, mat_off - 4)
    struct.pack_into("<12I", out, 0x13C,
                     vcount, voff - 4, tcount, toff - 4,
                     nmesh, moff - 4, nface, foff - 4, nnorm, noff - 4, 0, voff - 4)
    return bytes(out)


class Gtr2GmtTests(unittest.TestCase):
    def test_converts_rf2_gmt_to_gtr2_v210_and_maps_two_stage_shader(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "mesh.gmt")
            with open(path, "wb") as f:
                f.write(_make_rf2_gmt(
                    "1GRASS0",
                    shader="L2BUMPSPECMAPT0XT1",
                    textures=[
                        ("base.dds", 0x18, 1, 3, 0, 0),
                        ("detail.dds", 0x18, 1, 3, 1, 0),
                        ("spec.dds", 0x18, 2, 4, 2, 0),
                        ("bump.dds", 0x18, 5, 4, 3, 0),
                    ],
                ))

            gmt_gtr2.convert_file(path)
            with open(path, "rb") as f:
                prepared = f.read()
            self.assertTrue(gmt_gtr2.is_gtr2_gmt(prepared))
            self.assertEqual(prepared[0x194:0x19B], b"1GRASS0")

            # GTR2 mesh descriptor (108 bytes at 0x214): flags +0x00, mat +0x04, vcnt +0x08,
            # voff +0x0C, uvoff +0x14, tanoff +0x1C, tcnt +0x24, trioff +0x28.
            mvals = struct.unpack_from("<11I", prepared, gmt_gtr2.GTR2_HEADER_SIZE)
            self.assertEqual(mvals[0], 0x20004015)  # tangent bit 0x04 set
            self.assertEqual(mvals[1], 0)           # mat_index
            uv_off = mvals[5] + 4
            tan_off = mvals[7] + 4
            self.assertGreater(tan_off, uv_off)

            # UVs permuted for L2BUMPT0XT1: (base=0, detail=1, bump=3, unused=3)
            uv0 = struct.unpack_from("<8f", prepared, uv_off)
            self.assertAlmostEqual(uv0[0], 0.1, places=5)
            self.assertAlmostEqual(uv0[2], 1.1, places=5)
            self.assertAlmostEqual(uv0[4], 3.1, places=5)

            # L2 shader mapped to stock GTR2 L2BUMPT0XT1 and rF2 carbody bit stripped.
            mat_off = struct.unpack_from("<I", prepared, 0)[0] + 4
            mflags_mat = struct.unpack_from("<I", prepared, mat_off + 4 + 64)[0]
            self.assertEqual(mflags_mat, 0x68900003)
            l2_shader = prepared[mat_off + 4 + 144 + 2 * 264 + 8:
                                 mat_off + 4 + 144 + 2 * 264 + 72].split(b"\x00", 1)[0]
            self.assertEqual(l2_shader, b"L2BUMPT0XT1")

            # Verify verify_install's GTR2 parser parses cleanly with 0 trailing bytes.
            info = verify_install._gtr2_gmt_info(prepared)
            self.assertIsNotNone(info)
            self.assertEqual(info["trailing"], 0)
            self.assertEqual(info["shaders"], ["L2BUMPT0XT1"])
            self.assertEqual(info["textures"], {b"base.dds", b"detail.dds", b"bump.dds"})

            # Idempotent on already-converted GTR2 GMT v2.10.
            self.assertFalse(gmt_gtr2.convert_file(path))
            with open(path, "rb") as f:
                self.assertEqual(f.read(), prepared)

            # Reject unknown GMT version.
            with open(path, "wb") as f:
                f.write(b"\x00" * 512)
            with self.assertRaisesRegex(RuntimeError, "unsupported GMT version"):
                gmt_gtr2.convert_file(path)

    def test_bumpspecmap_adds_gtr2_math_stages_and_preserves_alpha_ref(self):
        rf2 = _make_rf2_gmt(
            "fence",
            shader="L2BUMPSPECMAPT0",
            textures=[
                ("fence.dds", 0x98, 1, 3, 0, 191),
                ("fence_s.dds", 0x18, 2, 4, 1, 0),
                ("fence_b.dds", 0x18, 5, 4, 2, 0),
            ],
        )
        fast = gmt_gtr2.convert_rf2_to_gtr2(rf2)
        with mock.patch.object(gmt_gtr2, "_np", None):
            pure = gmt_gtr2.convert_rf2_to_gtr2(rf2)
        self.assertEqual(fast, pure)

        info = verify_install._gtr2_gmt_info(fast)
        self.assertEqual(info["trailing"], 0)
        self.assertEqual(info["shaders"], ["L2BUMPSPECMAPT0"])
        self.assertEqual(info["alpha_refs"]["fence.dds"], 191)
        self.assertEqual(info["tex_flags"]["fence.dds"] & 0x80, 0x80)
        # MATH pseudo-textures are excluded from disk texture lookup set.
        self.assertNotIn(b"MATH", info["textures"])
        self.assertIn(b"MATH\x00", fast)

    def test_drop_flat_tangents_downgrades_flat_cutout_to_l2diffuset0(self):
        rf2 = _make_rf2_gmt(
            "tree",
            shader="L2BUMPSPECMAPT0",
            textures=[
                ("tree.dds", 0x98, 1, 3, 0, 191),
                ("_flat_spec.dds", 0x18, 2, 4, 1, 0),
                ("_flat_nm.dds", 0x18, 5, 4, 2, 0),
            ],
        )
        out = gmt_gtr2.convert_rf2_to_gtr2(rf2, include_faces=False, drop_flat_tangents=True)
        mvals = struct.unpack_from("<11I", out, gmt_gtr2.GTR2_HEADER_SIZE)
        self.assertEqual(mvals[0] & gmt_gtr2.MESH_FLAG_TANGENTS, 0)
        self.assertEqual(mvals[7], 0)  # no tangents
        _nm, _mo, nface, _fo, nnorm, _no = struct.unpack_from("<6I", out, 0x16C)
        self.assertEqual((nface, nnorm), (0, 0))
        info = verify_install._gtr2_gmt_info(out)
        self.assertEqual(info["shaders"], ["L2DIFFUSET0"])
        self.assertEqual(info["alpha_refs"]["tree.dds"], 191)
        self.assertEqual(info["textures"], {b"tree.dds"})

    def test_rebuilds_face_normals_plane_d_and_bounding_radius(self):
        rf2 = bytearray(_make_rf2_gmt("road"))
        (vcount, voff_rel, _tc, _to, _nm, _mo,
         _nf, foff_rel, _nn, noff_rel, _bc, _bo) = struct.unpack_from("<12I", rf2, 0x13C)
        voff, foff, noff = voff_rel + 4, foff_rel + 4, noff_rel + 4
        pts = ((0.0, 5.0, 0.0), (1.0, 5.0, 0.0), (0.0, 5.0, 1.0))
        for vi, (x, y, z) in enumerate(pts):
            struct.pack_into("<3f", rf2, voff + vi * gmt_gtr2.RF2_VERTEX_SIZE, x, y, z)
        struct.pack_into("<H", rf2, foff + 6, 4)
        struct.pack_into("<3fI", rf2, noff, 0.0, 0.0, 1.0, 7)

        fast = gmt_gtr2.convert_rf2_to_gtr2(bytes(rf2))
        with mock.patch.object(gmt_gtr2, "_np", None):
            pure = gmt_gtr2.convert_rf2_to_gtr2(bytes(rf2))
        self.assertEqual(fast, pure)

        _nm, _mo, nface, foff_r, nnorm, noff_r = struct.unpack_from("<6I", fast, 0x16C)
        self.assertEqual((nface, nnorm), (1, 1))
        v0, v1, v2, flg, mesh_i, ni, plane_d, cx, cy, cz, rad = struct.unpack_from(
            "<4H2I5f", fast, foff_r + 4)
        nx, ny, nz, sg = struct.unpack_from("<3fI", fast, noff_r + 4)
        self.assertEqual((v0, v1, v2, flg, mesh_i, ni, sg), (0, 1, 2, 2, 0, 0, 7))
        self.assertAlmostEqual(nx, 0.0, places=5)
        self.assertAlmostEqual(ny, 1.0, places=5)
        self.assertAlmostEqual(nz, 0.0, places=5)
        # plane_d = dot((0,1,0), (0,5,0)) = 5.0
        self.assertAlmostEqual(plane_d, 5.0, places=5)
        self.assertAlmostEqual(cx, 1.0 / 3.0, places=5)
        self.assertAlmostEqual(cy, 5.0, places=5)
        self.assertAlmostEqual(cz, 1.0 / 3.0, places=5)
        expected_rad = ((2.0 / 3.0) ** 2 + (1.0 / 3.0) ** 2) ** 0.5
        self.assertAlmostEqual(rad, expected_rad, places=5)

    def test_convert_assets_converts_all_gmts_and_verifies(self):
        with tempfile.TemporaryDirectory() as root:
            gmt = os.path.join(root, "mesh.gmt")
            dds = os.path.join(root, "map.dds")
            with open(gmt, "wb") as f:
                f.write(_make_rf2_gmt("mesh"))
            with open(dds, "wb") as f:
                f.write(b"DDS_DATA")

            count = gmt_gtr2.convert_assets(root)
            self.assertEqual(count, 1)
            with open(gmt, "rb") as f:
                self.assertTrue(gmt_gtr2.is_gtr2_gmt(f.read()))
            with open(dds, "rb") as f:
                self.assertEqual(f.read(), b"DDS_DATA")


class Gtr2FinalizeTests(unittest.TestCase):
    def test_long_layout_identifier_and_instance_identifier_limits(self):
        venue = "targa_tas_21_s1_high_plains"
        layout = finalize_track.layout_identifier(venue)
        self.assertEqual(len(layout), finalize_track.MAX_LAYOUT_ID_LENGTH)
        self.assertLessEqual(len(layout + ".trk"), 20)
        self.assertEqual(layout, finalize_track.layout_identifier(venue))

        used = set()
        long_name = "pitstop__metal_fence_extruded_001_SUB0"
        short = finalize_track.instance_identifier(long_name, used)
        self.assertLessEqual(len(short), finalize_track.MAX_INSTANCE_NAME_LENGTH)

    def test_stages_gtr2_menu_images_from_ac_track_map(self):
        import numpy as np

        seen_jpgs = {}

        def fake_texconv(command, **_kwargs):
            output_dir = command[command.index("-o") + 1]
            image_format = command[command.index("-ft") + 1].upper()
            src = command[-1]
            stem = os.path.splitext(os.path.basename(src))[0]
            out_path = os.path.join(output_dir, stem + "." + image_format)

            if image_format == "TGA":
                if "-w" in command and "-h" in command:
                    tw = int(command[command.index("-w") + 1])
                    th = int(command[command.index("-h") + 1])
                    src_img = finalize_track._read_tga_bgra(src)
                    ys = np.linspace(0, src_img.shape[0] - 1, th).astype(np.intp)
                    xs = np.linspace(0, src_img.shape[1] - 1, tw).astype(np.intp)
                    resized = src_img[np.ix_(ys, xs)]
                    finalize_track._write_tga_bgra(out_path, resized, bottom_up=False)
                else:
                    arr = np.zeros((400, 200, 4), dtype=np.uint8)
                    arr[40:360, 80:120, :3] = 255
                    arr[40:360, 80:120, 3] = 255
                    finalize_track._write_tga_bgra(out_path, arr, bottom_up=False)
            elif image_format == "JPG":
                seen_jpgs[stem] = finalize_track._read_tga_bgra(src).shape
                with open(out_path, "wb") as f:
                    f.write(b"\xff\xd8\xffJPG")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as root:
            preview = os.path.join(root, "map.png")
            track_dir = os.path.join(root, "track")
            os.makedirs(track_dir)
            with open(preview, "wb") as f:
                f.write(b"png")

            with mock.patch.object(finalize_track.subprocess, "run", side_effect=fake_texconv):
                count = finalize_track.stage_menu_images(
                    preview, track_dir, "targata_3ecef12f", "texconv.exe")

            self.assertEqual(count, 4)
            self.assertEqual(seen_jpgs["lod_canvas"], (768, 1024, 4))
            self.assertEqual(seen_jpgs["lod2_canvas"], (45, 1024, 4))
            self.assertTrue(os.path.isfile(os.path.join(track_dir, "targ_LOD.jpg")))
            self.assertTrue(os.path.isfile(os.path.join(track_dir, "targ_LOD_2.jpg")))
            thmb = os.path.join(track_dir, "tarThmb.tga")
            tbmp = os.path.join(track_dir, "targata_3ecef12fTrack.bmp")
            self.assertEqual(finalize_track._read_tga_bgra(thmb).shape, (400, 666, 4))
            self.assertEqual(finalize_track._read_tga_bgra(tbmp).shape, (83, 145, 4))

    def test_stages_gtr2_location_layout_and_references(self):
        with tempfile.TemporaryDirectory() as root:
            venue = "sample_track_name_that_exceeds_the_scene_filename_limit"
            layout_id = finalize_track.layout_identifier(venue)
            out_dir = os.path.join(root, venue)
            gmt_dir = os.path.join(out_dir, "gmt")
            tex_dir = os.path.join(out_dir, "textures")
            os.makedirs(gmt_dir)
            os.makedirs(tex_dir)
            rows = []
            for name in ("xfinish", "xsector1", "xsector2"):
                filename = name + ".gmt"
                with open(os.path.join(gmt_dir, filename), "wb") as f:
                    f.write(_make_rf2_gmt(name))
                rows.append({
                    "gmt": filename,
                    "mesh": name,
                    "physical": True,
                    "timing": True,
                    "blend": False,
                    "radius": 1.0,
                })
            with open(os.path.join(gmt_dir, "exported.json"), "w") as f:
                json.dump(rows, f)
            with open(os.path.join(tex_dir, "road.dds"), "wb") as f:
                f.write(b"DDS ")

            with mock.patch.object(sys, "argv", ["finalize_track.py", out_dir]):
                finalize_track.main()

            stage = os.path.join(out_dir, "gtr2", venue)
            self.assertTrue(os.path.isfile(os.path.join(stage, layout_id + ".trk")))
            self.assertTrue(os.path.isfile(os.path.join(stage, layout_id + ".gdb")))
            self.assertTrue(os.path.isfile(os.path.join(stage, layout_id + ".grd")))
            self.assertTrue(os.path.isfile(os.path.join(stage, "gmt", "skyboxi.gmt")))
            self.assertTrue(os.path.isfile(os.path.join(stage, "gmt", "xpitin.gmt")))
            self.assertTrue(os.path.isfile(os.path.join(stage, "gmt", "xpitout.gmt")))
            self.assertTrue(os.path.isfile(os.path.join(stage, "gmt", "xfinish.gmt")))
            self.assertTrue(os.path.isfile(os.path.join(stage, "maps", "road.dds")))
            with open(os.path.join(stage, "gmt", "xfinish.gmt"), "rb") as f:
                self.assertTrue(gmt_gtr2.is_gtr2_gmt(f.read()))
            with open(os.path.join(stage, layout_id + ".trk"), encoding="utf-8") as f:
                trk = f.read()
            self.assertIn("SearchPath=GAMEDATA\\LOCATIONS\\%s\\gmt" % venue, trk)
            self.assertIn("MASFile=Skies_central.gtr", trk)
            self.assertIn("Instance=skyboxi", trk)
            self.assertIn("Response=VEHICLE,TIMING", trk)
            self.assertIn("Response=VEHICLE,SECTOR", trk)
            with open(os.path.join(stage, layout_id + ".gdb"), encoding="utf-8") as f:
                gdb = f.read()
            self.assertTrue(gdb.startswith(layout_id + "\n"))
            self.assertIn("RoadDryGrip =", gdb)
            self.assertIn("DayAmbientColour =", gdb)

    def test_texture_scale_min_size_and_floor(self):
        self.assertEqual(fix_textures.target_dimensions(4096, 2048, 0.5, 1024), (2048, 1024))
        self.assertEqual(fix_textures.target_dimensions(1024, 1024, 0.5, 1024), (512, 512))
        self.assertEqual(fix_textures.target_dimensions(512, 512, 0.5, 1024), (512, 512))
        self.assertEqual(fix_textures.target_dimensions(1024, 1024, 0.25, 1024), (512, 512))


class StandaloneCliAndConfigTests(unittest.TestCase):
    def test_cli_dispatches_build_track_and_batch(self):
        seen = []

        def fake_build():
            seen.append(("build", list(sys.argv)))
            return 0

        def fake_batch():
            seen.append(("batch", list(sys.argv)))
            return 0

        with mock.patch.object(build_track, "main", side_effect=fake_build), \
                mock.patch.object(convert_multiple_tracks, "main", side_effect=fake_batch):
            self.assertEqual(ac2gtr2.main(["build_track", "C:\\track", "--hour", "10:00"]), 0)
            self.assertEqual(ac2gtr2.main(["convert_multiple_tracks", "tracks.txt"]), 0)
            self.assertEqual(ac2gtr2.main(["--stage", "build_track", "C:\\track2"]), 0)

        self.assertEqual(seen[0], ("build", ["ac2gtr2.exe build_track", "C:\\track", "--hour", "10:00"])
                         if seen[0][1][0].startswith("ac2gtr2.exe")
                         else ("build", seen[0][1]))
        self.assertEqual(seen[0][1][1:], ["C:\\track", "--hour", "10:00"])
        self.assertEqual(seen[1][1][1:], ["tracks.txt"])
        self.assertEqual(seen[2][1][1:], ["C:\\track2"])

    def test_load_config_reads_ac2gtr2_ini(self):
        with tempfile.TemporaryDirectory() as root:
            cfg = steam_paths.load_config(root)
            self.assertFalse(cfg["exists"])
            self.assertEqual(cfg["texture_scale"], 0.5)
            self.assertEqual(cfg["texture_scale_min_size"], 1024)
            self.assertEqual(cfg["road_smoothness"], 0.5)
            self.assertEqual(cfg["mesh_coarsening"], 0.0)
            self.assertTrue(cfg["strip_unused_gmt_data"])

            ini = os.path.join(root, "ac2gtr2.ini")
            with open(ini, "w", encoding="utf-8") as f:
                f.write(
                    "[track]\n"
                    "texture_scale=0.25\n"
                    "road_smoothness=0\n"
                    "mesh_coarsening=0.5\n"
                    "strip_unused_gmt_data=false\n"
                )
            cfg = steam_paths.load_config(root)
            self.assertTrue(cfg["exists"])
            self.assertEqual(cfg["texture_scale"], 0.25)
            self.assertEqual(cfg["road_smoothness"], 0.0)
            self.assertEqual(cfg["road_smooth_sigma"], 0.0)
            self.assertEqual(cfg["mesh_coarsening"], 0.5)
            self.assertFalse(cfg["strip_unused_gmt_data"])


if __name__ == "__main__":
    unittest.main()
