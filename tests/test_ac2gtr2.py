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
import rf1_track
import steam_paths
import track_zip
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

    def test_drops_degenerate_and_oversized_collision_faces(self):
        # Build a 3-face rF2 GMT: face 0 valid small, face 1 zero-area degenerate,
        # face 2 oversized (>50m in X). Both numpy and pure-Python paths must match.
        base = bytearray(_make_rf2_gmt("1GRASS_Terrain"))
        # Easier: build 3 single-face GMTs or patch a 3-vertex GMT directly for each case.
        # Case A: degenerate zero-area face (collinear / collapsed vertices) is dropped.
        degen = bytearray(_make_rf2_gmt("1ROAD_degen"))
        (_vc, voff_r, _tc, _to, _nm, _mo, _nf, foff_r, _nn, noff_r, _bc, _bo) = struct.unpack_from("<12I", degen, 0x13C)
        voff, noff = voff_r + 4, noff_r + 4
        for vi, (x, y, z) in enumerate(((1.0, 2.0, 3.0), (5.0, 2.0, 3.0), (1.0, 2.0, 3.0))):
            struct.pack_into("<3f", degen, voff + vi * gmt_gtr2.RF2_VERTEX_SIZE, x, y, z)
        struct.pack_into("<3fI", degen, noff, 0.0, 0.0, 0.0, 0)

        fast_d = gmt_gtr2.convert_rf2_to_gtr2(bytes(degen))
        with mock.patch.object(gmt_gtr2, "_np", None):
            pure_d = gmt_gtr2.convert_rf2_to_gtr2(bytes(degen))
        self.assertEqual(fast_d, pure_d)
        _nm, _mo, nface_d, _fo, nnorm_d, _no = struct.unpack_from("<6I", fast_d, 0x16C)
        self.assertEqual((nface_d, nnorm_d), (0, 0))

        # Case B: oversized triangle (80m x 40m) kept when max_face_span=None, dropped when max_face_span=50.0.
        big = bytearray(_make_rf2_gmt("1GRASS_big"))
        for vi, (x, y, z) in enumerate(((0.0, 0.0, 0.0), (80.0, 0.0, 0.0), (0.0, 0.0, 40.0))):
            struct.pack_into("<3f", big, voff + vi * gmt_gtr2.RF2_VERTEX_SIZE, x, y, z)

        kept = gmt_gtr2.convert_rf2_to_gtr2(bytes(big), max_face_span=None)
        self.assertEqual(struct.unpack_from("<I", kept, 0x174)[0], 1)

        fast_b = gmt_gtr2.convert_rf2_to_gtr2(bytes(big), max_face_span=50.0)
        with mock.patch.object(gmt_gtr2, "_np", None):
            pure_b = gmt_gtr2.convert_rf2_to_gtr2(bytes(big), max_face_span=50.0)
        self.assertEqual(fast_b, pure_b)
        _nm, _mo, nface_b, _fo, nnorm_b, _no = struct.unpack_from("<6I", fast_b, 0x16C)
        self.assertEqual((nface_b, nnorm_b), (0, 0))
        self.assertEqual(verify_install._gtr2_gmt_info(fast_b)["trailing"], 0)

        # Case C: corrupted long needle sliver (>50m span, <10m altitude) is dropped
        # even when max_face_span=None (e.g. on 1ROAD_* / 1KERB_* meshes).
        sliver = bytearray(_make_rf2_gmt("1ROAD_sliver"))
        for vi, (x, y, z) in enumerate(((0.0, 1.0, 0.0), (200.0, 1.0, 200.0), (200.1, 1.0, 200.0))):
            struct.pack_into("<3f", sliver, voff + vi * gmt_gtr2.RF2_VERTEX_SIZE, x, y, z)
        fast_s = gmt_gtr2.convert_rf2_to_gtr2(bytes(sliver), max_face_span=None)
        with mock.patch.object(gmt_gtr2, "_np", None):
            pure_s = gmt_gtr2.convert_rf2_to_gtr2(bytes(sliver), max_face_span=None)
        self.assertEqual(fast_s, pure_s)
        self.assertEqual(struct.unpack_from("<I", fast_s, 0x174)[0], 0)


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

    def test_barrier_hat_target_offroad_trim_and_skyboxi_collision(self):
        # Surface classification helpers.
        wall_row = {"gmt": "1WALL_01.gmt", "mesh": "1WALL_01", "physical": True,
                    "_meta": {"surface": "WALL", "rf2_material": "cwal_collision"}}
        grass_row = {"gmt": "1GRASS_01.gmt", "mesh": "1GRASS_01", "physical": True,
                     "_meta": {"surface": "GRASS", "rf2_material": "gras_terrain"}}
        road_row = {"gmt": "1ROAD_01.gmt", "mesh": "1ROAD_01", "physical": True,
                    "_meta": {"surface": "ROAD", "rf2_material": "road_asphalt"}}
        self.assertTrue(finalize_track.is_barrier_row(wall_row))
        self.assertFalse(finalize_track.is_offroad_terrain_row(wall_row))
        self.assertFalse(finalize_track.is_barrier_row(grass_row))
        self.assertTrue(finalize_track.is_offroad_terrain_row(grass_row))
        self.assertFalse(finalize_track.is_barrier_row(road_row))
        self.assertFalse(finalize_track.is_offroad_terrain_row(road_row))

        def _rf2_with_tri(name, pts):
            buf = bytearray(_make_rf2_gmt(name))
            voff = struct.unpack_from("<I", buf, 0x140)[0] + 4
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            zs = [p[2] for p in pts]
            struct.pack_into("<3f", buf, 0x0C8, min(xs), min(ys), min(zs))
            struct.pack_into("<3f", buf, 0x0D4, max(xs), max(ys), max(zs))
            for vi, (x, y, z) in enumerate(pts):
                struct.pack_into("<3f", buf, voff + vi * gmt_gtr2.RF2_VERTEX_SIZE, x, y, z)
            return bytes(buf)

        with tempfile.TemporaryDirectory() as root:
            venue = "hat_guard_test"
            layout_id = finalize_track.layout_identifier(venue)
            out_dir = os.path.join(root, venue)
            gmt_dir = os.path.join(out_dir, "gmt")
            tex_dir = os.path.join(out_dir, "textures")
            os.makedirs(gmt_dir)
            os.makedirs(tex_dir)
            with open(os.path.join(out_dir, venue + ".AIW"), "w", encoding="utf-8") as f:
                f.write("[Waypoint]\nwp_pos=(0.0,0.0,0.0)\nwp_pos=(20.0,0.0,0.0)\n")

            small_tri = ((0.0, 0.0, 0.0), (4.0, 0.0, 0.0), (0.0, 0.0, 4.0))
            huge_tri = ((0.0, 0.0, 0.0), (120.0, 0.0, 0.0), (0.0, 0.0, 80.0))
            remote_tri = ((500.0, 0.0, 500.0), (504.0, 0.0, 500.0), (500.0, 0.0, 504.0))
            files_spec = [
                ("xfinish.gmt", "xfinish", True, True, small_tri),
                ("xsector1.gmt", "xsector1", True, True, small_tri),
                ("xsector2.gmt", "xsector2", True, True, small_tri),
                ("1ROAD_main.gmt", "1ROAD_main", True, False, huge_tri),
                ("1WALL_01.gmt", "1WALL_01", True, False, small_tri),
                ("1GRASS_near.gmt", "1GRASS_near", True, False, small_tri),
                ("1GRASS_far.gmt", "1GRASS_far", True, False, huge_tri),
                ("1TERRAIN_remote.gmt", "1TERRAIN_remote", True, False, remote_tri),
                ("skyboxi.gmt", "skyboxi", False, False, small_tri),
            ]
            rows = []
            for fname, mname, phys, timing, pts in files_spec:
                with open(os.path.join(gmt_dir, fname), "wb") as f:
                    f.write(_rf2_with_tri(mname, pts))
                rows.append({
                    "gmt": fname, "mesh": mname, "physical": phys,
                    "timing": timing, "blend": False, "radius": 10.0,
                })
            with open(os.path.join(gmt_dir, "exported.json"), "w") as f:
                json.dump(rows, f)

            with mock.patch.object(sys, "argv", ["finalize_track.py", out_dir]):
                finalize_track.main()

            stage = os.path.join(out_dir, "gtr2", venue)
            with open(os.path.join(stage, layout_id + ".trk"), encoding="utf-8") as f:
                trk = f.read()

            # 1WALL keeps CollTarget=True but gets HATTarget=False.
            self.assertIn("MeshFile=1WALL_01.gmt CollTarget=True HATTarget=False", trk)
            # 1ROAD keeps oversized wide faces untouched and remains HATTarget=True.
            self.assertIn("MeshFile=1ROAD_main.gmt CollTarget=True HATTarget=True", trk)
            self.assertEqual(gmt_gtr2.gtr2_gmt_face_count(os.path.join(stage, "gmt", "1ROAD_main.gmt")), 1)
            # Trackside 1GRASS_near stays HATTarget=True; far 1GRASS_far (>50m) gets emptied & disabled.
            self.assertIn("MeshFile=1GRASS_near.gmt CollTarget=True HATTarget=True", trk)
            self.assertEqual(gmt_gtr2.gtr2_gmt_face_count(os.path.join(stage, "gmt", "1GRASS_far.gmt")), 0)
            self.assertIn("MeshFile=1GRASS_far.gmt CollTarget=False HATTarget=False", trk)
            # Remote 1TERRAIN_remote (>200m from AIW) gets CollTarget/HATTarget disabled and faces stripped.
            self.assertIn("MeshFile=1TERRAIN_remote.gmt CollTarget=False HATTarget=False", trk)
            self.assertEqual(gmt_gtr2.gtr2_gmt_face_count(os.path.join(stage, "gmt", "1TERRAIN_remote.gmt")), 0)
            # AC skyboxi.gmt is renamed to track_skyboxi.gmt without overwriting GTR2's skyboxi.gmt.
            self.assertTrue(os.path.isfile(os.path.join(stage, "gmt", "track_skyboxi.gmt")))
            self.assertIn("MeshFile=track_skyboxi.gmt", trk)
            with open(os.path.join(stage, "gmt", "skyboxi.gmt"), "rb") as f:
                self.assertEqual(f.read(), gmt_gtr2.make_minimal_gtr2_gmt("skyboxi", "_invisible.dds"))


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
            self.assertEqual(cfg["track_shadows"], "compact")
            self.assertEqual(cfg["track_shadows_source"], "default")

            ini = os.path.join(root, "ac2gtr2.ini")
            with open(ini, "w", encoding="utf-8") as f:
                f.write(
                    "[track]\n"
                    "texture_scale=0.25\n"
                    "road_smoothness=0\n"
                    "mesh_coarsening=0.5\n"
                    "strip_unused_gmt_data=false\n"
                    "track_shadows=off\n"
                )
            cfg = steam_paths.load_config(root)
            self.assertTrue(cfg["exists"])
            self.assertEqual(cfg["texture_scale"], 0.25)
            self.assertEqual(cfg["road_smoothness"], 0.0)
            self.assertEqual(cfg["road_smooth_sigma"], 0.0)
            self.assertEqual(cfg["mesh_coarsening"], 0.5)
            self.assertFalse(cfg["strip_unused_gmt_data"])
            self.assertEqual(cfg["track_shadows"], "off")
            self.assertEqual(cfg["track_shadows_source"], "ini")

    def test_gtr2_shadow_modes_and_receivers(self):
        road_meta = {
            "render": True, "cast_shadows": False, "decal": False, "backdrop": False,
            "alpha_test": False, "needs_blend": False, "shader": "ksPerPixel",
            "rf2_material": "road_asphalt", "ground_like": True, "upward": 1.0,
        }
        tree_meta = {
            "render": True, "cast_shadows": True, "decal": False, "backdrop": False,
            "alpha_test": True, "needs_blend": False, "shader": "ksTree",
            "rf2_material": "tree_01", "ground_like": False, "upward": 0.0,
        }
        hut_meta = {
            "render": True, "cast_shadows": True, "decal": False, "backdrop": False,
            "alpha_test": False, "needs_blend": False, "shader": "ksPerPixel",
            "rf2_material": "hut", "ground_like": False, "upward": 0.2,
        }
        cutouts = {"tree_01": 191}

        # Road receives shadows in all modes; trees/huts do not in compact/off.
        self.assertTrue(finalize_track.is_shadow_receiver(road_meta, {"render": True, "physical": True}, "compact"))
        self.assertTrue(finalize_track.is_shadow_receiver(road_meta, {"render": True, "physical": True}, "off"))
        self.assertFalse(finalize_track.is_shadow_receiver(tree_meta, {"render": True, "physical": False}, "compact"))
        self.assertFalse(finalize_track.is_shadow_receiver(hut_meta, {"render": True, "physical": False}, "compact"))
        self.assertTrue(finalize_track.is_shadow_receiver(hut_meta, {"render": True, "physical": False}, "full"))

        # Compact casts only on compact opaque structures (<= 60 m, <= 8000 tris), never foliage.
        self.assertIsNone(finalize_track.shadow_caster(tree_meta, cutouts, mode="compact", radius=15.0, tris=200))
        self.assertEqual(finalize_track.shadow_caster(hut_meta, cutouts, mode="compact", radius=15.0, tris=200), ("Solid", 32))
        self.assertEqual(finalize_track.shadow_caster(hut_meta, cutouts, mode="compact", radius=40.0, tris=200), ("Solid", 64))
        self.assertIsNone(finalize_track.shadow_caster(hut_meta, cutouts, mode="compact", radius=80.0, tris=200))
        self.assertIsNone(finalize_track.shadow_caster(hut_meta, cutouts, mode="compact", radius=15.0, tris=12000))

        # Off disables all track casters.
        self.assertIsNone(finalize_track.shadow_caster(hut_meta, cutouts, mode="off", radius=15.0, tris=200))

        # Full enables Texture foliage casters up to 150 m at 64x64.
        self.assertEqual(finalize_track.shadow_caster(tree_meta, cutouts, mode="full", radius=40.0, tris=500), ("Texture", 64))
        self.assertIsNone(finalize_track.shadow_caster(tree_meta, cutouts, mode="full", radius=200.0, tris=500))

        # Distance LOD culling: grass=250m, tree=800m, small prop=600m, road=0 (full range).
        self.assertEqual(finalize_track.gtr2_lod_range(road_meta, {"physical": True, "radius": 20.0}), (0.0, 0.0))
        self.assertEqual(finalize_track.gtr2_lod_range(tree_meta, {"physical": False, "radius": 100.0}), (0.0, 800.0))
        self.assertEqual(finalize_track.gtr2_lod_range(hut_meta, {"physical": False, "radius": 15.0}), (0.0, 600.0))
        grass_meta = dict(tree_meta, shader="ksGrass")
        self.assertEqual(finalize_track.gtr2_lod_range(grass_meta, {"physical": False, "radius": 120.0}), (0.0, 250.0))

    def test_track_brightness_config_materials_gdb_and_dds(self):
        # Config validation & INI loading.
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(steam_paths.load_config(tmp)["track_brightness"], 0.5)
            with open(os.path.join(tmp, "ac2gtr2.ini"), "w", encoding="utf-8") as f:
                f.write("[track]\ntrack_brightness=0.65\n")
            cfg = steam_paths.load_config(tmp)
            self.assertAlmostEqual(cfg["track_brightness"], 0.65)
            self.assertEqual(cfg["track_brightness_source"], "ini")
            with open(os.path.join(tmp, "ac2gtr2.ini"), "w", encoding="utf-8") as f:
                f.write("[track]\ntrack_brightness=1.5\n")
            with self.assertRaises(ValueError):
                steam_paths.load_config(tmp)
        with self.assertRaises(ValueError):
            steam_paths.validate_track_brightness(-0.1)
        with self.assertRaises(ValueError):
            steam_paths.validate_track_brightness(float("nan"))

        # .gdb lighting interpolation: 0.0 unchanged, 0.5 -> 188, 1.0 -> 210 (bright sunny look).
        base_light = finalize_track.gdb_lighting_colors(165, 255, 0.0)
        mid_light = finalize_track.gdb_lighting_colors(165, 255, 0.5)
        max_light = finalize_track.gdb_lighting_colors(165, 255, 1.0)
        self.assertEqual(base_light["day_amb"], (165, 165, 165))
        self.assertEqual(mid_light["day_amb"], (188, 188, 188))
        self.assertEqual(max_light["day_amb"], (210, 210, 210))
        self.assertGreater(max_light["sunrise_amb"][0], base_light["sunrise_amb"][0])
        self.assertGreater(max_light["rain_day_amb"][0], base_light["rain_day_amb"][0])

        # GMT material colors: 0.0 byte-exact, 0.5 lifts 25% toward 1.0, 1.0 lifts 50% toward 1.0,
        # preserving alpha, specular, and emissive.
        raw_cols = struct.pack(
            "<16f",
            0.40, 0.50, 0.60, 0.85,   # diffuse
            0.20, 0.30, 0.40, 0.75,   # ambient
            0.10, 0.10, 0.10, 1.00,   # specular
            0.00, 0.00, 0.00, 1.00,   # emissive
        )
        self.assertEqual(gmt_gtr2._brighten_material_colors(raw_cols, 0.0), raw_cols)
        mid_cols = struct.unpack("<16f", gmt_gtr2._brighten_material_colors(raw_cols, 0.5))
        self.assertAlmostEqual(mid_cols[0], 0.55, places=5)
        self.assertAlmostEqual(mid_cols[4], 0.40, places=5)
        self.assertAlmostEqual(mid_cols[3], 0.85, places=5)
        self.assertAlmostEqual(mid_cols[8], 0.10, places=5)
        self.assertAlmostEqual(mid_cols[12], 0.00, places=5)
        max_cols = struct.unpack("<16f", gmt_gtr2._brighten_material_colors(raw_cols, 1.0))
        self.assertEqual(tuple(round(x, 5) for x in max_cols[:3]), (0.70, 0.75, 0.80))
        self.assertEqual(tuple(round(x, 5) for x in max_cols[4:7]), (0.60, 0.65, 0.70))
        self.assertAlmostEqual(max_cols[12], 0.00, places=5)

        # DXT1 & DXT5 RGB565 endpoint brightening: preserves headers, alpha blocks,
        # and DXT1 opaque vs 1-bit transparent ordering across edge cases.
        with tempfile.TemporaryDirectory() as tmp:
            def _make_dds(fourcc, blocks_bytes):
                hdr = bytearray(128)
                hdr[0:4] = b"DDS "
                struct.pack_into("<I", hdr, 4, 124)
                struct.pack_into("<III", hdr, 12, 4, 8, len(blocks_bytes))
                struct.pack_into("<I", hdr, 28, 1)
                hdr[84:88] = fourcc
                return bytes(hdr) + blocks_bytes

            # DXT1: block 0 opaque normal (c0 > c1), block 1 opaque crossing/collapsing
            # near white (c0 > c1), block 2 transparent (c0 <= c1).
            dxt1_blocks = (
                struct.pack("<HHI", (12 << 11) | (20 << 5) | 10, (6 << 11) | (10 << 5) | 5, 0x12345678)
                + struct.pack("<HHI", (30 << 11) | (58 << 5) | 28, (29 << 11) | (62 << 5) | 30, 0xAAAAAAAA)
                + struct.pack("<HHI", (8 << 11) | (16 << 5) | 8, (14 << 11) | (28 << 5) | 14, 0xE4E4E4E4)
            )
            p1 = os.path.join(tmp, "test_dxt1.dds")
            orig_dxt1 = _make_dds(b"DXT1", dxt1_blocks)
            with open(p1, "wb") as f:
                f.write(orig_dxt1)
            self.assertFalse(finalize_track.brighten_dds_rgb565(p1, 0.0))
            with open(p1, "rb") as f:
                self.assertEqual(f.read(), orig_dxt1)

            self.assertTrue(finalize_track.brighten_dds_rgb565(p1, 1.0))
            with open(p1, "rb") as f:
                out_dxt1 = f.read()
            self.assertEqual(len(out_dxt1), len(orig_dxt1))
            self.assertEqual(out_dxt1[:128], orig_dxt1[:128])
            b0_c0, b0_c1, _ = struct.unpack_from("<HHI", out_dxt1, 128)
            b1_c0, b1_c1, _ = struct.unpack_from("<HHI", out_dxt1, 136)
            b2_c0, b2_c1, _ = struct.unpack_from("<HHI", out_dxt1, 144)
            self.assertGreater(b0_c0, (12 << 11) | (20 << 5) | 10)
            self.assertGreater(b0_c0, b0_c1)   # opaque stayed opaque
            self.assertGreater(b1_c0, b1_c1)   # saturated opaque never flipped to transparent
            self.assertLessEqual(b2_c0, b2_c1) # 1-bit transparent stayed transparent

            # DXT5: alpha block bytes 0..8 must be untouched.
            alpha_block = bytes([0xAA, 0x55, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66])
            color_block = struct.pack("<HHI", (10 << 11) | (20 << 5) | 10, (4 << 11) | (8 << 5) | 4, 0x0F0F0F0F)
            p5 = os.path.join(tmp, "test_dxt5.dds")
            orig_dxt5 = _make_dds(b"DXT5", alpha_block + color_block)
            with open(p5, "wb") as f:
                f.write(orig_dxt5)
            self.assertTrue(finalize_track.brighten_dds_rgb565(p5, 0.7))
            with open(p5, "rb") as f:
                out_dxt5 = f.read()
            self.assertEqual(out_dxt5[128:136], alpha_block)
            c0_new, c1_new, idx_new = struct.unpack_from("<HHI", out_dxt5, 136)
            self.assertGreater(c0_new, (10 << 11) | (20 << 5) | 10)
            self.assertEqual(idx_new, 0x0F0F0F0F)

            # collect_diffuse_textures picks stage-0 diffuse and excludes bump/spec.
            rf2_bytes = _make_rf2_gmt(
                shader="L2BUMPSPECMAPT0",
                textures=[
                    ("road_diff.dds", 0x18, 1, 3, 0, 0),
                    ("road_spec.dds", 0x18, 1, 3, 0, 0),
                    ("road_nm.dds", 0x18, 1, 3, 0, 0),
                ],
            )
            gmt_dir = os.path.join(tmp, "gmt")
            os.makedirs(gmt_dir)
            with open(os.path.join(gmt_dir, "road.gmt"), "wb") as f:
                f.write(gmt_gtr2.convert_rf2_to_gtr2(rf2_bytes, track_brightness=0.5))
            diff_texs = gmt_gtr2.collect_diffuse_textures(gmt_dir)
            self.assertEqual(diff_texs, {"road_diff.dds"})

    def test_detects_ac_vs_rf1_and_rf1_zip_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            ac_dir = os.path.join(tmp, "ac_track")
            os.makedirs(ac_dir)
            with open(os.path.join(ac_dir, "track.kn5"), "wb") as f:
                f.write(b"sc6969")
            self.assertEqual(rf1_track.detect_track_type(ac_dir), "ac")

            rf1_dir = os.path.join(tmp, "rf1_venue")
            lay_a = os.path.join(rf1_dir, "LayoutA")
            lay_b = os.path.join(rf1_dir, "LayoutB")
            os.makedirs(lay_a)
            os.makedirs(lay_b)
            with open(os.path.join(rf1_dir, "venue.mas"), "wb") as f:
                f.write(b"MAS")
            for lay_dir, stem in ((lay_a, "LayoutA"), (lay_b, "LayoutB")):
                with open(os.path.join(lay_dir, stem + ".scn"), "w", encoding="utf-8") as f:
                    f.write("CUBEASF\n")
                with open(os.path.join(lay_dir, stem + ".gdb"), "w", encoding="utf-8") as f:
                    f.write("%s\n{\n}\n" % stem)

            self.assertEqual(rf1_track.detect_track_type(rf1_dir), "rf1")
            self.assertEqual(rf1_track.rf1_layout_names(rf1_dir), ["LayoutA", "LayoutB"])
            # Direct layout folder resolves parent as asset_root.
            root, lays = rf1_track.find_rf1_layouts(lay_a)
            self.assertEqual(os.path.normcase(root), os.path.normcase(rf1_dir))
            self.assertEqual([l["name"] for l in lays], ["LayoutA"])

            # Stock Car Extreme detection via ReizaCommon / AdPack / StockV8 markers.
            sce_dir = os.path.join(tmp, "sce_venue")
            sce_lay = os.path.join(sce_dir, "CMP")
            os.makedirs(sce_lay)
            with open(os.path.join(sce_lay, "CMP.scn"), "w", encoding="utf-8") as f:
                f.write("CUBEASF\nSearchPath=ReizaCommon\nMASFile=AdPackStockcar.MAS\n")
            with open(os.path.join(sce_lay, "CMP.gdb"), "w", encoding="utf-8") as f:
                f.write("CMP\n{\n  Filter Properties = StockV8 *\n}\n")
            self.assertEqual(rf1_track.detect_track_type(sce_dir), "sce")

        # track_zip detects rF1 zip folders when no .kn5 exists.
        names = [
            "Miami22/miami22.mas",
            "Miami22/HRS/HRS.scn",
            "Miami22/HRS/HRS.gdb",
        ]
        self.assertEqual(track_zip.track_folders(names), ["Miami22"])

    def test_rf1_gmt_v200_conversion_scn_to_trk_and_gdb(self):
        # Build a synthetic rF1 GMT v2.00 with 2 meshes / 2 materials:
        #   mesh 0 -> mat 0 (rdax_main, L2USEVERTEXONLY + animated texture)
        #   mesh 1 -> mat 1 at +0x38 (L2BUMPSPECMAPT0 with 3 rF1 stages -> 5 GTR2 stages w/ 2 MATH)
        hdr = bytearray(gmt_gtr2.RF1_HEADER_MIN_SIZE)
        struct.pack_into("<I", hdr, 4, 0x04000020)
        struct.pack_into("<f", hdr, 0x008, 12.5)
        struct.pack_into("<2f", hdr, 0x0E4, 0.0, 2500.0)
        hdr[0x194:0x1D4] = gmt_gtr2._pad_ascii("rf1_road", 64)

        moff = len(hdr)
        mesh_desc0 = bytearray(gmt_gtr2.RF1_MESH_SIZE)
        mesh_desc1 = bytearray(gmt_gtr2.RF1_MESH_SIZE)
        foff = moff + 2 * gmt_gtr2.RF1_MESH_SIZE
        face = struct.pack("<4H2I4f", 0, 1, 2, 2, 0, 0, 0.0, 1.0, 0.0, 2.0)
        noff = foff + len(face)
        norm = struct.pack("<3fI", 0.0, 1.0, 0.0, 0)
        voff = noff + len(norm)
        verts = (
            struct.pack("<6f2I", 0.0, 2.0, 0.0, 0.0, 1.0, 0.0, 0xFFFFFFFF, 0)
            + struct.pack("<6f2I", 4.0, 2.0, 0.0, 0.0, 1.0, 0.0, 0xFFFFFFFF, 0)
            + struct.pack("<6f2I", 0.0, 2.0, 4.0, 0.0, 1.0, 0.0, 0xFFFFFFFF, 0)
        )
        uvoff = voff + len(verts)
        uvs = struct.pack("<8f", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0) * 3
        tanoff = uvoff + len(uvs)
        tans = struct.pack("<6f", 1.0, 0.0, 0.0, 0.0, 0.0, 1.0) * 3
        trioff = tanoff + len(tans)
        tris = struct.pack("<3H", 0, 1, 2)
        mat_off = trioff + len(tris)

        struct.pack_into(
            "<10I", mesh_desc0, 0,
            0x20004011, voff - 4, 0, uvoff - 4, 0, 0, 3, 0, 3, trioff - 4,
        )
        struct.pack_into("<I", mesh_desc0, 0x38, 0)
        struct.pack_into("<I", mesh_desc0, 0x4C, 0xFFFFFFFF)
        struct.pack_into(
            "<10I", mesh_desc1, 0,
            0x20004015, voff - 4, 0, uvoff - 4, 0, tanoff - 4, 3, 0, 3, trioff - 4,
        )
        struct.pack_into("<I", mesh_desc1, 0x38, 1)
        struct.pack_into("<I", mesh_desc1, 0x4C, 0xFFFFFFFF)
        struct.pack_into(
            "<8I", hdr, 0x174,
            2, moff - 4, 1, foff - 4, 1, noff - 4, 0, voff - 4,
        )
        struct.pack_into("<I", hdr, 0, mat_off - 4)

        mat = bytearray()
        mat.extend(struct.pack("<I", 2))
        # Material 0: rdax_main, L2USEVERTEXONLY, static + 3-frame animated tex.
        mat.extend(gmt_gtr2._pad_ascii("rdax_main", 64))
        mat.extend(struct.pack("<I", 0x68900003))
        mat.extend(struct.pack("<16f", *([1.0, 1.0, 1.0, 1.0] * 3 + [0.0, 0.0, 0.0, 1.0])))
        mat.extend(struct.pack("<2If", 2, 1, 1.0))
        for si, (sname, tc) in enumerate((("L0DIFFUSET0", 1), ("L1DIFFUSET0", 1), ("L2USEVERTEXONLY", 0))):
            sb = bytearray(gmt_gtr2.GTR2_SHADER_INFO_SIZE)
            struct.pack_into("<2I", sb, 0, si, tc)
            sb[8:72] = gmt_gtr2._pad_ascii(sname, 64)
            mat.extend(sb)
        mat.extend(bytes(gmt_gtr2.GTR2_MAT_POST_SHADERS_PAD))
        mat.extend(struct.pack("<I", 2))
        tb1 = bytearray(gmt_gtr2.GTR2_TEXTURE_SIZE)
        tb1[:64] = gmt_gtr2._pad_ascii("road.tga", 64)
        struct.pack_into("<IiII", tb1, 64, 0x18, -1, 1, 1)
        mat.extend(tb1)
        tb2 = bytearray(gmt_gtr2.GTR2_TEXTURE_SIZE + 4 * 3 + 12)
        tb2[:64] = gmt_gtr2._pad_ascii("flag.dds", 64)
        struct.pack_into("<IiII", tb2, 64, 0x18, -1, 3, 3)
        struct.pack_into("<3I", tb2, 80, 0, 1, 2)
        mat.extend(tb2)

        # Material 1: L2BUMPSPECMAPT0 with 3 rF1 textures (must expand to 5 with 2 MATH)
        # and GSCE/rF1 local/unique material/texture flags that must be stripped for GTR2.
        mat.extend(gmt_gtr2._pad_ascii("road_bump", 64))
        mat.extend(struct.pack("<I", 0x68940203))
        mat.extend(struct.pack("<16f", *([1.0, 1.0, 1.0, 1.0] * 3 + [0.0, 0.0, 0.0, 1.0])))
        mat.extend(struct.pack("<2If", 2, 1, 1.0))
        for first_t, tc, sname in ((0, 1, "L0DIFFUSET0"), (1, 1, "L1DIFFUSET0"), (2, 3, "L2BUMPSPECMAPT0")):
            sb = bytearray(gmt_gtr2.GTR2_SHADER_INFO_SIZE)
            struct.pack_into("<2I", sb, 0, first_t, tc)
            sb[8:72] = gmt_gtr2._pad_ascii(sname, 64)
            mat.extend(sb)
        mat.extend(bytes(gmt_gtr2.GTR2_MAT_POST_SHADERS_PAD))
        mat.extend(struct.pack("<I", 5))
        for tname in ("road.tga", "road.tga", "road.tga", "road_s.dds", "road_b.dds"):
            tb = bytearray(gmt_gtr2.GTR2_TEXTURE_SIZE)
            tb[:64] = gmt_gtr2._pad_ascii(tname, 64)
            struct.pack_into("<IiII", tb, 64, 0x118, -1, 1, 1)
            mat.extend(tb)

        rf1_raw = bytes(hdr + mesh_desc0 + mesh_desc1 + face + norm + verts + uvs + tans + tris + mat)
        self.assertTrue(gmt_gtr2.is_rf1_gmt(rf1_raw))
        gtr2_raw = gmt_gtr2.convert_rf1_to_gtr2(
            rf1_raw,
            tex_resolver=lambda t: "road.dds" if t.lower() == "road.tga" else t,
        )
        self.assertTrue(gmt_gtr2.is_gtr2_gmt(gtr2_raw))
        info = verify_install._gtr2_gmt_info(gtr2_raw)
        self.assertIsNotNone(info)
        self.assertEqual(info["trailing"], 0)
        self.assertEqual(info["shaders"], ["L2USEVERTEXPIXEL", "L2BUMPSPECMAPT0"])
        self.assertIn(b"road.dds", info["textures"])
        self.assertIn(b"flag.dds", info["textures"])
        self.assertEqual(gtr2_raw.count(b"MATH\x00"), 2)
        self.assertIn(b"road_rdax_main", gtr2_raw)
        # Verify unsupported GSCE/rF1 local material/texture flags were cleared.
        m1_off = gtr2_raw.index(b"road_bump\x00")
        self.assertEqual(struct.unpack_from("<I", gtr2_raw, m1_off + 64)[0], 0x68900003)
        m1_t0 = (m1_off + gmt_gtr2.GTR2_MAT_HEADER_SIZE
                 + 3 * gmt_gtr2.GTR2_SHADER_INFO_SIZE
                 + gmt_gtr2.GTR2_MAT_POST_SHADERS_PAD + 4)
        self.assertEqual(struct.unpack_from("<I", gtr2_raw, m1_t0 + 64)[0], 0x18)
        # Verify mesh 0 -> mat 0 and mesh 1 -> mat 1 in GTR2 108-byte mesh descriptors.
        out_moff = struct.unpack_from("<I", gtr2_raw, 0x170)[0] + 4
        self.assertEqual(struct.unpack_from("<I", gtr2_raw, out_moff + 4)[0], 0)
        self.assertEqual(struct.unpack_from("<I", gtr2_raw, out_moff + gmt_gtr2.GTR2_MESH_SIZE + 4)[0], 1)
        # plane_d = dot((0,1,0), (0,2,0)) = 2.0
        out_foff = struct.unpack_from("<I", gtr2_raw, 0x178)[0] + 4
        plane_d = struct.unpack_from("<f", gtr2_raw, out_foff + 16)[0]
        self.assertAlmostEqual(plane_d, 2.0, places=4)

        # rF1-only shaders absent from GTR2 GFX.GTR map to stage-compatible stock shaders.
        self.assertEqual(gmt_gtr2._remap_rf1_shader_name("L2USEVERTEXONLY"), "L2USEVERTEXPIXEL")
        self.assertEqual(gmt_gtr2._remap_rf1_shader_name("L0CMAPADDT0"), "L0CMAPT0")
        self.assertEqual(gmt_gtr2._remap_rf1_shader_name("L1CMAPADDT0"), "L1CMAPT0")
        self.assertEqual(gmt_gtr2._remap_rf1_shader_name("L2CMAPADDT0"), "L2CMAPT0")
        self.assertEqual(
            gmt_gtr2._remap_rf1_shader_name("L2DIFFUSET0LERPT1XT2T3VERTEXALPHA"),
            "L2DIFFUSET0T1XT2T3",
        )
        self.assertEqual(gmt_gtr2._remap_rf1_shader_name("L2DIFFUSET0"), "L2DIFFUSET0")
        # Stock Car Extreme / Reiza custom shaders map to stock GTR2 GFX.GTR shaders.
        self.assertEqual(gmt_gtr2._remap_rf1_shader_name("SIMPLEROADSHADERV2"), "L2BUMPT0XT1")
        self.assertEqual(gmt_gtr2._remap_rf1_shader_name("ADVANCEDNONLINEDROADSHADERV2"), "L2BUMPT0XT1")
        self.assertEqual(gmt_gtr2._remap_rf1_shader_name("TREESHADERBB"), "L2DIFFUSET0")
        self.assertEqual(gmt_gtr2._remap_rf1_shader_name("TERRAINV2MAPPEDVC"), "L2DIFFUSET0")
        self.assertEqual(gmt_gtr2._remap_rf1_shader_name("RZRUMBLESTRIP"), "L2BUMPT0")
        self.assertEqual(gmt_gtr2._remap_rf1_shader_name("DIFFUSEBUMPSPECR"), "L2BUMPSPECMAPT0")
        self.assertEqual(
            gmt_gtr2._remap_rf1_shader_name("L2BUMPCMAPSPECMAPADDSPECULARREFLECTT0"),
            "L2BUMPCMAPSPECMAPADDALPHAREFLECTT0",
        )

        # Test .scn -> .trk and .gdb conversion across track_shadows modes.
        with tempfile.TemporaryDirectory() as tmp:
            gmt_dir = os.path.join(tmp, "gmt")
            os.makedirs(gmt_dir)
            with open(os.path.join(gmt_dir, "road01.gmt"), "wb") as f:
                f.write(gtr2_raw)
            metrics = rf1_track._collect_gmt_metrics([gmt_dir])
            self.assertIn("road01.gmt", metrics)
            self.assertAlmostEqual(metrics["road01.gmt"][0], 12.5, places=3)
            self.assertEqual(metrics["road01.gmt"][1], 2)

            scn = os.path.join(tmp, "Test.scn")
            with open(scn, "w", encoding="utf-8") as f:
                f.write(
                    "CUBEASF\n"
                    "SearchPath=Test\n"
                    "MASFile=test.mas\n"
                    "MaxShadowRange(350.00)\n"
                    "Light=Direct00\n{\n  Type=Directional Color=(255,255,255)\n}\n"
                    "Instance=skyboxi\n{\n  MeshFile=horizon.gmt\n}\n"
                    "Instance=very_long_rf1_instance_name_exceeding_thirty_one_chars\n"
                    "{\n  MeshFile=road01.gmt CollTarget=True HATTarget=True ShadowReceiver=True "
                    "ShadowCaster=(Dynamic, Texture, 256, 256)\n}\n"
                    "Instance=small_solid\n"
                    "{\n  MeshFile=small_hut.gmt CollTarget=True HATTarget=False ShadowReceiver=True\n"
                    "  ShadowCaster=(Static, Solid, 128, 128)\n"
                    "  ShadowCaster=(Dynamic,Texture,32,32)\n}\n"
                    "Instance=med_solid\n"
                    "{\n  MeshFile=med_stand.gmt CollTarget=False HATTarget=False ShadowReceiver=True\n"
                    "  ShadowObject=(Static, Solid, 256, 256)\n}\n"
                    "Instance=huge_solid\n"
                    "{\n  MeshFile=huge_stand.gmt CollTarget=False HATTarget=False\n"
                    "  ShadowCaster=(Static, Solid, 128, 128)\n}\n"
                    "Instance=static_tex\n"
                    "{\n  MeshFile=tree01.gmt CollTarget=False HATTarget=False\n"
                    "  ShadowCaster=(Static, Texture, 256, 256)\n}\n"
                    "Instance=missing_obj\n{\n  MeshFile=does_not_exist.gmt\n}\n"
                )
            avail = {"road01.gmt", "small_hut.gmt", "med_stand.gmt", "huge_stand.gmt", "tree01.gmt"}
            fake_metrics = {
                "road01.gmt": (12.5, 100),
                "small_hut.gmt": (15.0, 200),
                "med_stand.gmt": (45.0, 1500),
                "huge_stand.gmt": (90.0, 1500),
                "tree01.gmt": (10.0, 50),
            }

            # full mode: keeps static solid/texture casters, all receivers, and authored range,
            # while still stripping Dynamic, Texture casters.
            trk_full, stats_full = rf1_track.convert_scn_to_trk(
                scn, "TestVenue", "Test", avail, "Instance=skyboxi\n{\n}\n",
                track_shadows="full", gmt_metrics=fake_metrics,
            )
            self.assertEqual(stats_full["instances"], 5)
            self.assertEqual(stats_full["skipped_missing"], 1)
            self.assertEqual(stats_full["renamed_long"], 1)
            self.assertEqual(stats_full["stripped_dynamic_casters"], 2)
            self.assertEqual(stats_full["shadow_casters"], 4)
            self.assertEqual(stats_full["shadow_receivers"], 3)
            self.assertIn("MaxShadowRange=(350.0)", trk_full)
            self.assertIn("ShadowCaster=(Static, Solid, 128, 128)", trk_full)
            self.assertIn("ShadowCaster=(Static, Texture, 256, 256)", trk_full)
            self.assertIn("ShadowObject=(Static, Solid, 256, 256)", trk_full)
            self.assertNotIn("ShadowCaster=(Dynamic", trk_full)
            self.assertNotIn("does_not_exist.gmt", trk_full)
            self.assertNotIn("Type=Directional", trk_full)
            for inst in __import__("re").findall(r"(?i)\bInstance=([^\s\{\}]+)", trk_full):
                self.assertLessEqual(len(inst), 31)

            # compact mode (default): caps range to 200m, keeps ShadowReceiver=True only on
            # HATTarget=True, strips Texture & >60m casters, downsizes compact solid casters.
            trk_cmp, stats_cmp = rf1_track.convert_scn_to_trk(
                scn, "TestVenue", "Test", avail, "Instance=skyboxi\n{\n}\n",
                track_shadows="compact", gmt_metrics=fake_metrics,
            )
            self.assertIn("MaxShadowRange=(200.0)", trk_cmp)
            self.assertEqual(stats_cmp["shadow_casters"], 2)
            self.assertEqual(stats_cmp["shadow_receivers"], 1)
            self.assertEqual(stats_cmp["stripped_receivers"], 2)
            self.assertIn("ShadowCaster=(Static, Solid, 32, 32)", trk_cmp)
            self.assertIn("ShadowObject=(Static, Solid, 64, 64)", trk_cmp)
            self.assertNotIn("Texture", trk_cmp)
            self.assertNotIn("128, 128", trk_cmp)
            self.assertNotIn("256, 256", trk_cmp)

            # off mode: strips all casters, caps range to 200m, keeps HATTarget=True receivers.
            trk_off, stats_off = rf1_track.convert_scn_to_trk(
                scn, "TestVenue", "Test", avail, "Instance=skyboxi\n{\n}\n",
                track_shadows="off", gmt_metrics=fake_metrics,
            )
            self.assertIn("MaxShadowRange=(200.0)", trk_off)
            self.assertEqual(stats_off["shadow_casters"], 0)
            self.assertEqual(stats_off["shadow_receivers"], 1)
            self.assertNotIn("ShadowCaster=", trk_off)
            self.assertNotIn("ShadowObject=", trk_off)

            # Texture filename space sanitizing & resolver.
            self.assertEqual(rf1_track.sanitize_texture_filename("ASFALTO 4.DDS"), "ASFALTO_4.DDS")
            self.assertEqual(rf1_track.sanitize_texture_filename("ABU DHABI  DOS.DDS"), "ABU_DHABI_DOS.DDS")
            res = rf1_track._make_tex_resolver(
                {"asfalto_4.dds", "my_flag00.dds"},
                {"asfalto_4": "ASFALTO_4.DDS"},
                renamed_maps={"asfalto 4.dds": "ASFALTO_4.DDS"},
            )
            self.assertEqual(res("ASFALTO 4.DDS"), "ASFALTO_4.DDS")
            self.assertEqual(res("ASFALTO 4.TGA"), "ASFALTO_4.DDS")
            self.assertEqual(res("MY FLAG.DDS"), "MY_FLAG.DDS")

            tdf = os.path.join(tmp, "Test.tdf")
            with open(tdf, "w", encoding="utf-8") as f:
                f.write("[FEEDBACK]\nDry=1.05\nWet=0.88\nMaterials=roada,road\n")
            gdb = os.path.join(tmp, "Test.gdb")
            with open(gdb, "w", encoding="utf-8") as f:
                f.write(
                    "Test\n{\n  TrackName = My Test\n  NorthDirection = 123\n"
                    "  DayAmbientRGB = (110,120,130)\n  Qualify Laptime = 72.5\n}\n"
                )
            used_venues = set()
            out_gdb = rf1_track.build_gtr2_gdb(
                gdb, "TestVenue", "Test", tdf_path=tdf,
                used_venue_names=used_venues, max_vehicles=24,
            )
            self.assertIn("DayAmbientColour = (110,120,130)", out_gdb)
            self.assertIn("NorthDir = 123", out_gdb)
            self.assertIn("RoadDryGrip = 1.050", out_gdb)
            self.assertIn("VenueName = My Test", out_gdb)
            self.assertIn("Max Vehicles = 24", out_gdb)
            # Second layout sharing the same TrackName/VenueName gets a distinct VenueName.
            out_gdb2 = rf1_track.build_gtr2_gdb(
                gdb, "TestVenue", "TestWet", tdf_path=tdf,
                used_venue_names=used_venues,
            )
            self.assertNotIn("VenueName = My Test\n", out_gdb2)

            # Multi-layout IDs & VenueNames avoid shared 4-char prefixes and prefix collisions.
            g1 = os.path.join(tmp, "MFinthen_DTM.gdb")
            g2 = os.path.join(tmp, "MFinthen_DTMwet.gdb")
            with open(g1, "w", encoding="utf-8") as f:
                f.write("MFinthen_DTM\n{\n  TrackName = Mainz-Finthen DTM\n  VenueName = Mainz-Finthen\n}\n")
            with open(g2, "w", encoding="utf-8") as f:
                f.write("MFinthen_DTMwet\n{\n  TrackName = Mainz-Finthen DTM wet\n  VenueName = Mainz-Finthen\n}\n")
            lays = [
                {"name": "MFinthen_DTM", "gdb": g1},
                {"name": "MFinthen_DTMwet", "gdb": g2},
            ]
            lids = rf1_track.assign_rf1_layout_ids(lays)
            self.assertNotEqual(lids["MFinthen_DTM"][:4].lower(), lids["MFinthen_DTMwet"][:4].lower())
            self.assertFalse(lids["MFinthen_DTMwet"].lower().startswith(lids["MFinthen_DTM"].lower()))
            vnames = rf1_track.assign_rf1_display_names(lays, "Mainz-Finthen")
            self.assertFalse(vnames["MFinthen_DTMwet"].lower().startswith(vnames["MFinthen_DTM"].lower()))

            # AIW garagespots > 3 is clamped to actual per-team GarPos count (<= 3).
            aiw_in = os.path.join(tmp, "in.AIW")
            aiw_out = os.path.join(tmp, "out.AIW")
            with open(aiw_in, "w", encoding="utf-8") as f:
                f.write(
                    "[Features]\nstartinggrid=104\npitspots=52\ngaragespots=30\n"
                    "[GRID]\nGridIndex=0\nGridIndex=1\n"
                    "[PITS]\nTeamIndex=0\nGarPos=(0,0,0,0)\nGarPos=(1,0,0,0)\nGarPos=(2,0,0,0)\n"
                )
            aiw_stats = rf1_track.sanitize_rf1_aiw(aiw_in, aiw_out)
            self.assertTrue(aiw_stats["clamped_garagespots"])
            self.assertTrue(aiw_stats["clamped_startinggrid"])
            self.assertTrue(aiw_stats["clamped_pitspots"])
            self.assertEqual(aiw_stats["garage_spots"], 3)
            self.assertEqual(aiw_stats["grid_spots"], 2)
            self.assertEqual(aiw_stats["team_spots"], 1)
            with open(aiw_out, encoding="utf-8") as f:
                aiw_txt = f.read()
                self.assertIn("garagespots=3", aiw_txt)
                self.assertIn("startinggrid=2", aiw_txt)
                self.assertIn("pitspots=1", aiw_txt)
            # Plain TGA header detection rejects encrypted GSCE TGA bytes.
            self.assertTrue(rf1_track._is_plain_tga(bytes([0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 64, 0, 64, 0, 32, 0])))
            self.assertFalse(rf1_track._is_plain_tga(bytes.fromhex("e83aab945f46b13e001eae02c08cac757575")))

            # Moveable=True and Change=False vs static material global/local disambiguation.
            dis_dir = os.path.join(tmp, "dis_gmt")
            os.makedirs(dis_dir)
            with open(os.path.join(dis_dir, "static_pole.gmt"), "wb") as f:
                f.write(gtr2_raw)
            with open(os.path.join(dis_dir, "balloon.gmt"), "wb") as f:
                f.write(gtr2_raw)
            with open(os.path.join(dis_dir, "xpitin.gmt"), "wb") as f:
                f.write(gtr2_raw)
            fake_trk = (
                "Instance=pole01\n{\n  MeshFile=static_pole.gmt CollTarget=True HATTarget=False\n}\n"
                "Instance=balloon01\n{\n  Moveable=True\n  MeshFile=balloon.gmt CollTarget=False HATTarget=False\n}\n"
                "Instance=xpitin\n{\n  Render=False Change=False\n  MeshFile=xpitin.gmt CollTarget=True HATTarget=False\n}\n"
            )
            n_mov, n_var = rf1_track._disambiguate_rf1_materials([dis_dir], [fake_trk])
            self.assertEqual(n_mov, 4)
            self.assertEqual(n_var, 0)
            with open(os.path.join(dis_dir, "balloon.gmt"), "rb") as f:
                b_raw = f.read()
            self.assertIn(b"road_rdax_main_m\x00", b_raw)
            self.assertIn(b"road_bump_m\x00", b_raw)
            with open(os.path.join(dis_dir, "xpitin.gmt"), "rb") as f:
                x_raw = f.read()
            self.assertIn(b"road_rdax_main_nc\x00", x_raw)

    def test_build_dist_release_archive_and_sha256(self):
        import datetime
        import build_dist
        import sha256sum

        self.assertEqual(
            os.path.basename(build_dist.release_dist_dir(r"C:\work", datetime.date(2026, 11, 26))),
            "ac2gtr2_20261126",
        )
        seven_zip = build_dist.find_7z()
        with tempfile.TemporaryDirectory() as tmp:
            rel_dir = build_dist.release_dist_dir(tmp, datetime.date(2026, 11, 26))
            os.makedirs(rel_dir)
            with open(os.path.join(rel_dir, "hello.txt"), "w", encoding="utf-8") as f:
                f.write("ac2gtr2 release test\n")
            archive_path, checksum_path, digest = build_dist.create_release_archive(rel_dir, seven_zip)
            self.assertTrue(os.path.isfile(archive_path))
            self.assertTrue(os.path.isfile(checksum_path))
            self.assertEqual(os.path.basename(archive_path), "ac2gtr2_20261126.7z")
            self.assertEqual(os.path.basename(checksum_path), "ac2gtr2_20261126.7z.sha256.txt")
            self.assertEqual(digest, sha256sum.sha256_file(archive_path))
            with open(checksum_path, encoding="utf-8") as f:
                self.assertEqual(f.read(), "%s  ac2gtr2_20261126.7z\n" % digest)

    def test_no_hardcoded_local_tool_paths_and_dist_tool_resolution(self):
        import ast
        import io
        import pathlib
        import re
        import check_setup

        # 1. Verify no production Python file encodes a hardcoded drive-letter local path.
        repo_root = pathlib.Path(steam_paths.__file__).resolve().parent
        drive_pat = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/]")
        offenders = []
        for py in sorted(repo_root.glob("*.py")):
            tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"), filename=py.name)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if drive_pat.search(node.value):
                        offenders.append("%s:%d: %r" % (py.name, node.lineno, node.value))
        self.assertEqual(offenders, [], "Hardcoded local paths found:\n" + "\n".join(offenders))

        # 2. Running from dist (frozen) ALWAYS uses dist-local paths even if env vars are set.
        with tempfile.TemporaryDirectory() as tmp:
            dist_root = os.path.join(tmp, "my_dist")
            os.makedirs(dist_root)
            fake_env = {
                "AC2GTR2_BLENDER": os.path.join(tmp, "external", "blender.exe"),
                "AC2GTR2_TEXCONV": os.path.join(tmp, "external", "texconv.exe"),
                "AC2GTR2_MASUNPACK": os.path.join(tmp, "external", "masunpack.exe"),
                "AC2GTR2_RFACTORCRYPT": os.path.join(tmp, "external", "rfactorcrypt.exe"),
                "AC2GTR2_AC_DIR": os.path.join(tmp, "external", "ac"),
                "AC2GTR2_GAME_DIR": os.path.join(tmp, "external", "gtr2"),
            }
            dist_paths = steam_paths.resolve_paths(app_root=dist_root, frozen=True, env=fake_env)
            self.assertTrue(dist_paths["IS_STANDALONE"])
            self.assertEqual(dist_paths["TOOLS_ROOT"], os.path.abspath(dist_root))
            self.assertEqual(dist_paths["BLENDER"], os.path.join(dist_root, "tools", "blender", "blender.exe"))
            self.assertEqual(dist_paths["BLENDER_STAGE"], os.path.join(dist_root, "tools", "blender_scripts", "blender_stage.py"))
            self.assertEqual(dist_paths["TEXCONV"], os.path.join(dist_root, "tools", "texconv", "texconv.exe"))
            self.assertEqual(dist_paths["MASUNPACK"], os.path.join(dist_root, "tools", "rfactortools", "masunpack.exe"))
            self.assertEqual(dist_paths["RFACTORCRYPT"], os.path.join(dist_root, "tools", "rfactortools", "rfactorcrypt.exe"))
            self.assertEqual(dist_paths["AC_DIR"], os.path.join(dist_root, "data", "ac"))
            self.assertEqual(dist_paths["GTR2_DIR"], os.path.join(dist_root, "data", "gtr2"))

            # 3. Running from Python directly gets all tools from <script_path>/dist/.
            src_root = os.path.join(tmp, "repo")
            os.makedirs(src_root)
            with open(os.path.join(src_root, "blender_stage.py"), "w", encoding="utf-8") as f:
                f.write("# stub\n")
            py_paths = steam_paths.resolve_paths(app_root=src_root, frozen=False, env={})
            expected_dist = os.path.join(src_root, "dist")
            self.assertFalse(py_paths["IS_STANDALONE"])
            self.assertEqual(py_paths["TOOLS_ROOT"], expected_dist)
            self.assertFalse(steam_paths.dist_ready(py_paths["TOOLS_ROOT"]))
            self.assertEqual(py_paths["BLENDER"], os.path.join(expected_dist, "tools", "blender", "blender.exe"))
            self.assertEqual(py_paths["TEXCONV"], os.path.join(expected_dist, "tools", "texconv", "texconv.exe"))
            self.assertEqual(py_paths["MASUNPACK"], os.path.join(expected_dist, "tools", "rfactortools", "masunpack.exe"))
            self.assertEqual(py_paths["RFACTORCRYPT"], os.path.join(expected_dist, "tools", "rfactortools", "rfactorcrypt.exe"))
            self.assertEqual(py_paths["AC_DIR"], os.path.join(expected_dist, "data", "ac"))
            self.assertEqual(py_paths["GTR2_DIR"], os.path.join(expected_dist, "data", "gtr2"))
            self.assertEqual(py_paths["BLENDER_STAGE"], os.path.join(src_root, "blender_stage.py"))

            # 4. check_setup requires build_dist.py to be run first when dist/ is missing.
            buf = io.StringIO()
            with mock.patch.object(steam_paths, "IS_STANDALONE", False), \
                    mock.patch.object(steam_paths, "TOOLS_ROOT", expected_dist), \
                    mock.patch.object(steam_paths, "AC_DIR", py_paths["AC_DIR"]), \
                    mock.patch.object(steam_paths, "GTR2_DIR", py_paths["GTR2_DIR"]), \
                    mock.patch.object(steam_paths, "TEXCONV", py_paths["TEXCONV"]), \
                    mock.patch.object(steam_paths, "APP_DIR", src_root), \
                    mock.patch.object(sys, "stdout", buf):
                ok = check_setup.check(need_blender=False)
            self.assertFalse(ok)
            self.assertIn("build_dist.py", buf.getvalue())

            # Once dist/tools and dist/data exist, dist_ready() is True.
            os.makedirs(os.path.join(expected_dist, "tools"))
            os.makedirs(os.path.join(expected_dist, "data"))
            self.assertTrue(steam_paths.dist_ready(expected_dist))


if __name__ == "__main__":
    unittest.main()
