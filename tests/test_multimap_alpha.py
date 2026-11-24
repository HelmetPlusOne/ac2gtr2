import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

import numpy as np

import bake_layers
import convert_track


def _rgba(w, h, rgb=(0.6, 0.6, 0.6), alpha=1.0):
    arr = np.empty((h, w, 4), dtype=np.float32)
    arr[:, :, 0], arr[:, :, 1], arr[:, :, 2] = rgb
    arr[:, :, 3] = alpha
    return arr


class MultiMapAlphaTests(unittest.TestCase):
    def test_needs_blend_uses_txNormal_for_multimap_shaders(self):
        # Union Island wooden house walls: ksPerPixelMultiMap_AT_NMDetail with
        # txDiffuse.a = 0 (detail mask, 3.8% opaque) and txNormal = null_nrm.dds.
        wall = types.SimpleNamespace(
            shader="ksPerPixelMultiMap_AT_NMDetail",
            alpha_tested=1,
            blend=0,
            textures={"txDiffuse": "buildings_ao_01.dds", "txNormal": "null_nrm.dds"},
        )
        alpha = {"buildings_ao_01.dds": 3.8, "null_nrm.dds": None,
                 "win_nrm_alpha.dds": 20.0, "opaque_nrm.dds": 100.0}
        self.assertFalse(convert_track.needs_blend(wall, alpha))

        # Opaque real normal map also needs no blending.
        wall_opaque_nrm = types.SimpleNamespace(
            shader="ksPerPixelMultiMap_AT_NMDetail",
            alpha_tested=1,
            blend=0,
            textures={"txDiffuse": "buildings_ao_01.dds", "txNormal": "opaque_nrm.dds"},
        )
        self.assertFalse(convert_track.needs_blend(wall_opaque_nrm, alpha))

        # Blended MultiMap_AT with transparent alpha in txNormal (e.g. windows) keeps blending.
        window = types.SimpleNamespace(
            shader="ksPerPixelMultiMap_AT",
            alpha_tested=0,
            blend=1,
            textures={"txDiffuse": "win_diff.dds", "txNormal": "win_nrm_alpha.dds"},
        )
        self.assertTrue(convert_track.needs_blend(window, alpha))

        # Standard ksPerPixelAT still inspects txDiffuse.
        tree = types.SimpleNamespace(
            shader="ksPerPixelAT",
            alpha_tested=1,
            blend=0,
            textures={"txDiffuse": "buildings_ao_01.dds"},
        )
        self.assertTrue(convert_track.needs_blend(tree, alpha))

    def test_blend_alpha_ignores_txDiffuse_detail_mask_on_multimap(self):
        # txDiffuse has 96% alpha=0 (detail mask) and 4% alpha=1 (doors): std > 0.01.
        base = _rgba(8, 8, alpha=0.0)
        base[0, :3, 3] = 1.0

        wall_null_nrm = {
            "shader": "ksPerPixelMultiMap_AT_NMDetail",
            "alpha_test": True,
            "transparent": False,
            "needs_blend": False,
            "textures": {"txDiffuse": "ao.dds", "txNormal": "null_nrm.dds"},
        }
        self.assertIsNone(
            bake_layers.blend_alpha(wall_null_nrm, base, ".", ".", {}, 8, 8)
        )

        # When txNormal actually carries a cut-out mask, blend_alpha returns txNormal.a.
        nrm_mask = _rgba(8, 8, rgb=(0.5, 0.5, 1.0), alpha=0.0)
        nrm_mask[:4, :, 3] = 1.0
        wall_masked_nrm = {
            "shader": "ksPerPixelMultiMap_AT_NMDetail",
            "alpha_test": True,
            "transparent": False,
            "needs_blend": False,
            "textures": {"txDiffuse": "ao.dds", "txNormal": "grille_nrm.dds"},
        }
        with mock.patch.object(bake_layers, "to_rgba", return_value=nrm_mask):
            got = bake_layers.blend_alpha(wall_masked_nrm, base, ".", ".", {}, 8, 8)
        self.assertIsNotNone(got)
        np.testing.assert_array_equal(got, nrm_mask[:, :, 3])

        # Non-MultiMap shader still takes alpha from base (txDiffuse).
        plain_at = {
            "shader": "ksPerPixelAT",
            "alpha_test": True,
            "transparent": False,
            "needs_blend": False,
            "textures": {"txDiffuse": "fence.dds"},
        }
        got_plain = bake_layers.blend_alpha(plain_at, base, ".", ".", {}, 8, 8)
        self.assertIsNotNone(got_plain)
        np.testing.assert_array_equal(got_plain, base[:, :, 3])

    def test_bake_layers_main_excludes_multimap_null_normal_from_alpha_ref(self):
        # End-to-end (mocked texconv/DDS decode, zero external tools) covering:
        # 1) BAKED_archi1_yellow (ksPerPixelMultiMap_AT_NMDetail + null_nrm.dds) -> opaque, NOT in alpha_ref
        # 2) BAKED_archi1_white (no txDetail, null_nrm.dds) -> opaque, NOT in alpha_ref
        # 3) grille_multimap (ksPerPixelMultiMap_AT + real cutout txNormal.a) -> IN alpha_ref using txNormal.a
        # 4) tree_at (ksPerPixelAT + real cutout txDiffuse.a) -> IN alpha_ref using txDiffuse.a
        ao = _rgba(16, 16, rgb=(0.6, 0.6, 0.6), alpha=0.0)
        ao[0, :2, 3] = 1.0  # tiny opaque region, 99% alpha=0 detail mask
        detail = _rgba(16, 16, rgb=(0.9, 0.8, 0.1), alpha=1.0)
        grille_nrm = _rgba(16, 16, rgb=(0.5, 0.5, 1.0), alpha=0.0)
        grille_nrm[:8, :, 3] = 1.0  # 50% solid / 50% cut in txNormal.a
        tree_tex = _rgba(16, 16, rgb=(0.2, 0.5, 0.2), alpha=0.0)
        tree_tex[:8, :, 3] = 1.0

        images = {
            "buildings_ao_01.dds": ao,
            "wall_color_yellow.dds": detail,
            "grille_nrm.dds": grille_nrm,
            "tree.dds": tree_tex,
        }
        written_alphas = {}

        def fake_dds_size(path):
            return (16, 16) if os.path.exists(path) else None

        def fake_to_rgba(path, _tmp, w, h):
            img = images.get(os.path.basename(path))
            if img is None:
                return None
            return img[:h, :w] if img.shape[:2] == (h, w) else np.resize(img, (h, w, 4))

        def fake_write_dds(stem, rgb, alpha, tex_dir, _tmp, _texconv, keep_coverage=None):
            name = stem + ".dds"
            out_path = os.path.join(tex_dir, name)
            with open(out_path, "wb") as f:
                f.write(b"DDS ")
            arr = np.empty((rgb.shape[0], rgb.shape[1], 4), dtype=np.float32)
            arr[:, :, :3] = rgb
            arr[:, :, 3] = 1.0 if alpha is None else alpha
            images[name] = arr
            written_alphas[name] = None if alpha is None else alpha.copy()
            return name

        def fake_run(cmd):
            # Handles write_base()'s direct texconv call on <stem>.tga -> <stem>.dds
            src = cmd[-1]
            out_dir = cmd[cmd.index("-o") + 1]
            stem = os.path.splitext(os.path.basename(src))[0]
            fmt = cmd[cmd.index("-f") + 1]
            tga = bake_layers.read_tga(src)
            name = stem + ".dds"
            with open(os.path.join(out_dir, name), "wb") as f:
                f.write(b"DDS ")
            images[name] = tga
            written_alphas[name] = None if fmt == "BC1_UNORM" else tga[:, :, 3].copy()
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as root:
            tex_dir = os.path.join(root, "textures")
            os.makedirs(tex_dir)
            for fn in ("buildings_ao_01.dds", "wall_color_yellow.dds",
                       "null_nrm.dds", "grille_nrm.dds", "tree.dds"):
                with open(os.path.join(tex_dir, fn), "wb") as f:
                    f.write(b"DDS ")

            mmap = {
                "texture_files": {
                    fn: fn for fn in ("buildings_ao_01.dds", "wall_color_yellow.dds",
                                      "null_nrm.dds", "grille_nrm.dds", "tree.dds")
                },
                "meshes": [
                    {
                        "mesh": "house_yellow",
                        "rf2_material": "BAKED_archi1_yellow",
                        "ac_material": "BAKED_archi1_yellow",
                        "material_key": "BAKED_archi1_yellow",
                        "shader": "ksPerPixelMultiMap_AT_NMDetail",
                        "alpha_test": True,
                        "transparent": False,
                        "needs_blend": False,
                        "render": True,
                        "upward": 0.0,
                        "flatness": 0.0,
                        "props": {"ksAlphaRef": 0.0, "useDetail": 1.0},
                        "textures": {
                            "txDiffuse": "buildings_ao_01.dds",
                            "txNormal": "null_nrm.dds",
                            "txDetail": "wall_color_yellow.dds",
                        },
                    },
                    {
                        "mesh": "house_white",
                        "rf2_material": "BAKED_archi1_white",
                        "ac_material": "BAKED_archi1_white",
                        "material_key": "BAKED_archi1_white",
                        "shader": "ksPerPixelMultiMap_AT_NMDetail",
                        "alpha_test": True,
                        "transparent": False,
                        "needs_blend": False,
                        "render": True,
                        "upward": 0.0,
                        "flatness": 0.0,
                        "props": {"ksAlphaRef": 0.0, "useDetail": 1.0},
                        "textures": {
                            "txDiffuse": "buildings_ao_01.dds",
                            "txNormal": "null_nrm.dds",
                            "txDetail": "null.dds",
                        },
                    },
                    {
                        "mesh": "grille",
                        "rf2_material": "grille_multimap",
                        "ac_material": "grille_multimap",
                        "material_key": "grille_multimap",
                        "shader": "ksPerPixelMultiMap_AT",
                        "alpha_test": True,
                        "transparent": False,
                        "needs_blend": True,
                        "render": True,
                        "upward": 0.0,
                        "flatness": 0.0,
                        "props": {"ksAlphaRef": 0.5},
                        "textures": {
                            "txDiffuse": "buildings_ao_01.dds",
                            "txNormal": "grille_nrm.dds",
                        },
                    },
                    {
                        "mesh": "tree",
                        "rf2_material": "tree_at",
                        "ac_material": "tree_at",
                        "material_key": "tree_at",
                        "shader": "ksPerPixelAT",
                        "alpha_test": True,
                        "transparent": False,
                        "needs_blend": True,
                        "render": True,
                        "upward": 0.0,
                        "flatness": 0.0,
                        "props": {"ksAlphaRef": 0.5},
                        "textures": {"txDiffuse": "tree.dds"},
                    },
                ],
            }
            map_path = os.path.join(root, "material_map.json")
            with open(map_path, "w") as f:
                json.dump(mmap, f)

            with mock.patch.object(bake_layers, "dds_size", side_effect=fake_dds_size), \
                    mock.patch.object(bake_layers, "to_rgba", side_effect=fake_to_rgba), \
                    mock.patch.object(bake_layers, "write_dds", side_effect=fake_write_dds), \
                    mock.patch.object(bake_layers, "run", side_effect=fake_run), \
                    mock.patch.object(sys, "argv", ["bake_layers.py", root, "--jobs", "1"]):
                bake_layers.main()

            with open(map_path) as f:
                out = json.load(f)
            # Wooden house walls must NOT be alpha-tested, and their flat-colour
            # txDetail tint must be baked directly into an opaque texture instead
            # of being cancelled to grey by two-stage write_base().
            self.assertNotIn("BAKED_archi1_yellow", out["alpha_ref"])
            self.assertNotIn("BAKED_archi1_white", out["alpha_ref"])
            self.assertIn("BAKED_archi1_yellow", out["no_alpha_test"])
            self.assertIn("BAKED_archi1_white", out["no_alpha_test"])
            self.assertEqual(out["baked"]["BAKED_archi1_yellow"], "BAKED_archi1_yellow_baked.dds")
            self.assertNotIn("BAKED_archi1_yellow", out["two_stage"])
            self.assertIsNone(written_alphas["BAKED_archi1_yellow_baked.dds"])
            baked_rgb = images["BAKED_archi1_yellow_baked.dds"][:, :, :3]
            # Wall pixels (txDiffuse.a == 0) get tinted: [0.6, 0.6, 0.6] * [0.9, 0.8, 0.1]
            np.testing.assert_allclose(baked_rgb[8, 8], [0.54, 0.48, 0.06], atol=1e-3)
            # Door pixels (txDiffuse.a == 1) stay untinted: [0.6, 0.6, 0.6]
            np.testing.assert_allclose(baked_rgb[0, 0], [0.6, 0.6, 0.6], atol=1e-3)
            # Real cutouts (both MultiMap with txNormal.a and ksPerPixelAT with txDiffuse.a) keep alpha_ref.
            self.assertEqual(out["alpha_ref"]["grille_multimap"], bake_layers.CUTOUT_REF)
            self.assertEqual(out["alpha_ref"]["tree_at"], bake_layers.CUTOUT_REF)
            # grille_multimap's cutout texture took its 50/50 mask from grille_nrm.dds, not buildings_ao_01.dds.
            grille_tex = out["cutout_tex"]["grille_multimap"]
            self.assertAlmostEqual(float((written_alphas[grille_tex] >= 0.74).mean()), 0.5, places=2)

    def test_wants_bake_includes_plain_multimap_only_when_useDetail_enabled(self):
        ashton_blue = {
            "shader": "ksPerPixelMultiMap",
            "props": {"useDetail": 1.0},
            "textures": {"txDiffuse": "ashton_gi.dds", "txMaps": "NULL.dds",
                         "txDetail": "wall_color_blue3.dds"},
        }
        self.assertTrue(bake_layers.wants_bake(ashton_blue))

        unused_detail = {
            "shader": "ksPerPixelMultiMap",
            "props": {"useDetail": 0.0},
            "textures": {"txDiffuse": "curb.dds", "txMaps": "curb_map.dds",
                         "txDetail": "wall_color_blue3.dds"},
        }
        self.assertFalse(bake_layers.wants_bake(unused_detail))

        maps_copy = {
            "shader": "ksPerPixelMultiMap",
            "props": {"useDetail": 1.0},
            "textures": {"txDiffuse": "curb.dds", "txMaps": "curb_map.dds",
                         "txDetail": "curb_map.dds"},
        }
        self.assertFalse(bake_layers.wants_bake(maps_copy))

        grainy_nmdetail = {
            "shader": "ksPerPixelMultiMap_NMDetail",
            "props": {"useDetail": 1.0},
            "textures": {"txDiffuse": "sand_trap.dds", "txMaps": "sand_map.dds",
                         "txDetail": "gravel.dds"},
        }
        self.assertTrue(bake_layers.wants_bake(grainy_nmdetail))
        # Non-flat grainy detail returns None from bake_multimap_tint so it stays two-stage.
        grainy = _rgba(16, 16, rgb=(0.5, 0.5, 0.5), alpha=1.0)
        grainy[:8, :, :3] = 0.7
        grainy[8:, :, :3] = 0.3
        with mock.patch.object(bake_layers, "dds_size", return_value=(16, 16)), \
                mock.patch.object(bake_layers, "to_rgba", return_value=grainy):
            self.assertIsNone(
                bake_layers.bake_multimap_tint(grainy_nmdetail, "gravel.dds", ".", ".", 2048, {}, "texconv")
            )


if __name__ == "__main__":
    unittest.main()
