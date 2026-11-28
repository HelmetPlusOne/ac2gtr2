"""Convert rFactor 2 GMT v2.52 binaries to GTR2 GMT v2.10.

Blender's io_rf2_gmt_release addon exports only gMotor 2.5 (GMOTOR_GMT_2.52)
files: an 888-byte header (magic at 0x1BC), 156-byte mesh descriptors,
32-byte FaceData records with global vertex indices, interleaved 96-byte
TVertex2 records, and single-level materials with 180-byte texture records.

GTR2 (gMotor 2.0) requires unencrypted GMOTOR_GMT_2.10 files: a 532-byte
header (magic at 0x08), 108-byte mesh descriptors, 36-byte FaceData records
(including plane distance D and mesh-local vertex indices), split 32-byte
TVertex / 32-byte TexturesUV / optional 24-byte UVTangents arrays per mesh,
and 3-level (L0/L1/L2) materials with 172-byte texture records using shaders
defined in GTR2's stock GFX.GTR.
"""
import math
import os
import struct

try:
    import numpy as _np
except ImportError:
    _np = None

GTR2_GMT_MAGIC = bytes.fromhex("c8cfd2d8ced8e6c3d6ded8bebba7bf90")  # GMOTOR_GMT_2.10\0
RF2_GMT_MAGIC = bytes.fromhex("c8cfd2d8ced8e6c3d6ded8bebbabc190")   # GMOTOR_GMT_2.52\0

GTR2_MAGIC_OFFSET = 0x008
RF2_MAGIC_OFFSET = 0x1BC

GTR2_HEADER_SIZE = 0x214  # 532 bytes; MeshOffset = 0x210 (relative to byte 4)
RF1_HEADER_MIN_SIZE = 0x2E4  # 740 bytes minimum; rF1 GMT v2.00 has no header magic
RF2_HEADER_SIZE = 0x378   # 888 bytes; MeshOffset = 0x374 (relative to byte 4)

GTR2_GMT_FLAGS = 0x00000020
GTR2_MESH_SIZE = 108
RF1_MESH_SIZE = 132
RF2_MESH_SIZE = 156

RF1_FACE_SIZE = 32
RF2_FACE_SIZE = 32
GTR2_FACE_SIZE = 36
NORMAL_SIZE = 16
GTR2_VERTEX_SIZE = 32
GTR2_UV_SIZE = 32
GTR2_TANGENT_SIZE = 24
RF2_VERTEX_SIZE = 96

GTR2_MAT_HEADER_SIZE = 144
GTR2_SHADER_INFO_SIZE = 264
GTR2_MAT_POST_SHADERS_PAD = 32
GTR2_TEXTURE_SIZE = 172

RF2_MAT_HEADER_SIZE = 260
RF2_TEXTURE_SIZE = 180

MESH_FLAG_TANGENTS = 0x00000004
MAT_FLAG_RF2_CARBODY = 0x00002000
MAT_FLAG_UNIQUE_LOCAL = 0x00000200
MAT_FLAG_RF1_LOCAL = 0x00040000
MAT_FLAG_RF1_UNSUPPORTED = MAT_FLAG_RF1_LOCAL | MAT_FLAG_RF2_CARBODY | MAT_FLAG_UNIQUE_LOCAL
TEX_FLAG_COLOR_KEY = 0x00000080
TEX_FLAG_UNIQUE_MAT = 0x00000100
TEX_FLAG_BINK = 0x00040000

DEFAULT_NORMAL_QUANT = 500.0
MAX_COARSEN_NORMAL_DEG = 8.0

_MATH_STAGE_3 = "__GTR2_MATH_STAGE_3__"
_MATH_STAGE_4 = "__GTR2_MATH_STAGE_4__"


def normal_dedup_quant(mesh_coarsening=0.0):
    """Map mesh_coarsening in [0.0, 1.0] to the vertex normal dedup scale K.

    At 0.0 (disabled), returns DEFAULT_NORMAL_QUANT (500.0 ~= 0.11 deg) for
    exact legacy deduplication. For mesh_coarsening > 0.0, loosens smoothly up
    to MAX_COARSEN_NORMAL_DEG (8.0 deg, K ~= 7.16) at 1.0 so smooth surfaces
    deduplicate cleanly while sharp creases (> ~8 deg) stay split.
    """
    c = float(mesh_coarsening)
    if not math.isfinite(c) or not (0.0 <= c <= 1.0):
        raise ValueError(
            "mesh_coarsening must be between 0.0 and 1.0, got %r"
            % (mesh_coarsening,)
        )
    if c == 0.0:
        return DEFAULT_NORMAL_QUANT
    return min(DEFAULT_NORMAL_QUANT, 1.0 / math.radians(MAX_COARSEN_NORMAL_DEG * c))


def is_gtr2_gmt(data):
    """Return True when raw GMT bytes already carry the GMOTOR_GMT_2.10 magic."""
    return (len(data) >= GTR2_MAGIC_OFFSET + len(GTR2_GMT_MAGIC)
            and data[GTR2_MAGIC_OFFSET:GTR2_MAGIC_OFFSET + len(GTR2_GMT_MAGIC)]
            == GTR2_GMT_MAGIC)


def is_rf2_gmt(data):
    """Return True when raw GMT bytes carry the GMOTOR_GMT_2.52 magic."""
    return (len(data) >= RF2_MAGIC_OFFSET + len(RF2_GMT_MAGIC)
            and data[RF2_MAGIC_OFFSET:RF2_MAGIC_OFFSET + len(RF2_GMT_MAGIC)]
            == RF2_GMT_MAGIC)


def tex_record_size(data, pos):
    """Exact byte length of one GTR2 / rF1 GMT texture entry starting at ``pos``.

    Static textures occupy 172 bytes; Bink movie textures (flag 0x40000) occupy
    180 bytes; animated textures (anim_frames > 1) embed a uint32 frame sequence
    of length ``seq_len`` plus 12 bytes of animation metadata (172 + 4*seq_len + 12).
    """
    if pos + 80 > len(data):
        return GTR2_TEXTURE_SIZE
    flags = struct.unpack_from("<I", data, pos + 64)[0]
    anim_frames = struct.unpack_from("<I", data, pos + 72)[0]
    if anim_frames > 1:
        seq_len = struct.unpack_from("<I", data, pos + 76)[0]
        return GTR2_TEXTURE_SIZE + 4 * seq_len + 12
    if flags & TEX_FLAG_BINK:
        return GTR2_TEXTURE_SIZE + 8
    return GTR2_TEXTURE_SIZE


def is_rf1_gmt(data):
    """Return True when raw GMT bytes match rFactor 1 gMotor 2.0 (v2.00) layout."""
    if len(data) < RF1_HEADER_MIN_SIZE or is_gtr2_gmt(data) or is_rf2_gmt(data):
        return False
    mat_off = struct.unpack_from("<I", data, 0)[0] + 4
    if mat_off < RF1_HEADER_MIN_SIZE or mat_off + 4 > len(data):
        return False
    nmesh, moff_r, nface, foff_r, nnorm, noff_r, _bcnt, _boff_r = struct.unpack_from(
        "<8I", data, 0x174
    )
    moff = moff_r + 4
    foff = foff_r + 4
    noff = noff_r + 4
    if moff < RF1_HEADER_MIN_SIZE:
        return False
    if moff + nmesh * RF1_MESH_SIZE > mat_off:
        return False
    if nface and (foff < moff or foff + nface * RF1_FACE_SIZE > mat_off):
        return False
    if nnorm and (noff < foff or noff + nnorm * NORMAL_SIZE > mat_off):
        return False
    return True


def _pad_ascii(text, size):
    encoded = text.encode("ascii", errors="replace")[:size - 1]
    return encoded + b"\x00" * (size - len(encoded))


def _make_math_tex_record(stage, flags, unk68):
    """Build a 172-byte GTR2 MATH pseudo-texture entry (required by L2BumpSpecMapT0)."""
    out = bytearray(GTR2_TEXTURE_SIZE)
    out[:64] = _pad_ascii("MATH", 64)
    # flags, unk68, anim=1, tex_type=15 (Math), action=4, rate=1.0, stage
    struct.pack_into("<IiIIIfI", out, 64, flags, unk68, 1, 15, 4, 1.0, stage)
    struct.pack_into("<I", out, 100, 1)
    return bytes(out)


def _rf2_tex_to_gtr2(tex_raw, stage):
    """Shrink a 180-byte rF2 texture record to the 172-byte GTR2 layout."""
    if tex_raw == _MATH_STAGE_3:
        return _make_math_tex_record(3, 0x8000, 1)
    if tex_raw == _MATH_STAGE_4:
        return _make_math_tex_record(4, 0x18, -1)

    out = bytearray(GTR2_TEXTURE_SIZE)
    out[:108] = tex_raw[:108]
    tex_name = tex_raw[:64].split(b"\x00", 1)[0].decode("ascii", errors="replace")
    base, ext = os.path.splitext(tex_name)
    if base.upper() in {"NULL", "NONE", "NIL", "EMPTY"}:
        safe_name = "tex_" + base.lower() + (ext or ".dds")
        out[:64] = _pad_ascii(safe_name, 64)
    struct.pack_into("<I", out, 88, stage)
    flags = struct.unpack_from("<I", out, 64)[0]
    alpha_ref = struct.unpack_from("<I", out, 100)[0]
    if not (flags & TEX_FLAG_COLOR_KEY) and alpha_ref == 0:
        struct.pack_into("<I", out, 100, 1)
    return bytes(out)


def _tex_stage(tex_raw, fallback):
    stage = struct.unpack_from("<I", tex_raw, 88)[0]
    return stage if 0 <= stage < 4 else fallback


def _tex_filename_lower(tex_raw):
    return tex_raw[:64].split(b"\x00", 1)[0].decode("ascii", errors="replace").strip().lower()


def _map_material_for_gtr2(rf2_shader, textures, drop_flat_tangents=False):
    """Map an rF2 shader and its textures to GTR2's stock 3-level shader set.

    Returns (has_bump, uv_perm, shader_levels) where:
      - has_bump: whether GTR2 needs the 24-byte UVTangents block (mflags |= 4)
      - uv_perm: 4-tuple mapping GTR2 TEXCOORD[k] <- rF2 UV[uv_perm[k]]
      - shader_levels: list of 3 tuples (shader_name, [(tex_raw, stage), ...])

    GTR2's stock GFX.GTR provides:
      - L2BUMPT0XT1 (BUMPMAP.GFX): stage 0=sTex0 (base), stage 1=sTex1 (detail),
        stage 2=sBump (normal map). Used for rF2's L2BUMPSPECMAPT0XT1.
      - L2BUMPSPECMAPT0 (BUMPMAP.GFX): stage 0=sBase, stage 1=sSpec, stage 2=sBump,
        stage 3=MATH, stage 4=MATH.
      - L2DIFFUSET0 (DIFFUSE.GFX): stage 0=sTex0 (natively supports ColorKey 0x80).
    """
    shader = rf2_shader.strip().upper()

    if shader == "L2BUMPSPECMAPT0XT1" and len(textures) >= 4:
        # rF2 L2BUMPSPECMAPT0XT1 does not exist in GTR2's GFX.GTR.
        # GTR2's stock L2BumpT0xT1 (BUMPMAP.GFX) computes base*detail with
        # normal mapping using stage order (0=sTex0, 1=sTex1, 2=sBump).
        base, detail, _spec, bump = textures[0], textures[1], textures[2], textures[3]
        uv_perm = (
            _tex_stage(base, 0),
            _tex_stage(detail, 1),
            _tex_stage(bump, 3),
            3,
        )
        levels = [
            ("L0DIFFUSET0", [(base, 0)]),
            ("L1DIFFUSET0", [(base, 0)]),
            ("L2BUMPT0XT1", [
                (base, 0),
                (detail, 1),
                (bump, 2),
            ]),
        ]
        return True, uv_perm, levels

    if shader == "L2BUMPSPECMAPT0" and len(textures) >= 3:
        base, spec, bump = textures[0], textures[1], textures[2]
        if (drop_flat_tangents
                and _tex_filename_lower(spec) == "_flat_spec.dds"
                and _tex_filename_lower(bump) == "_flat_nm.dds"):
            uv_perm = (_tex_stage(base, 0), 1, 2, 3)
            levels = [
                ("L0DIFFUSET0", [(base, 0)]),
                ("L1DIFFUSET0", [(base, 0)]),
                ("L2DIFFUSET0", [(base, 0)]),
            ]
            return False, uv_perm, levels
        uv_perm = (
            _tex_stage(base, 0),
            _tex_stage(spec, 1),
            _tex_stage(bump, 2),
            3,
        )
        levels = [
            ("L0DIFFUSET0", [(base, 0)]),
            ("L1DIFFUSET0", [(base, 0)]),
            ("L2BUMPSPECMAPT0", [
                (base, 0),
                (spec, 1),
                (bump, 2),
                (_MATH_STAGE_3, 3),
                (_MATH_STAGE_4, 4),
            ]),
        ]
        return True, uv_perm, levels

    if shader == "L2DIFFUSET0" or len(textures) == 1:
        base = textures[0]
        uv_perm = (_tex_stage(base, 0), 1, 2, 3)
        levels = [
            ("L0DIFFUSET0", [(base, 0)]),
            ("L1DIFFUSET0", [(base, 0)]),
            ("L2DIFFUSET0", [(base, 0)]),
        ]
        return False, uv_perm, levels

    has_bump = "BUMP" in shader
    l01 = [(textures[0], 0)] if textures else []
    l2 = [(tex, idx) for idx, tex in enumerate(textures)]
    levels = [
        ("L0DIFFUSET0", l01),
        ("L1DIFFUSET0", l01),
        (shader or "L2DIFFUSET0", l2),
    ]
    return has_bump, (0, 1, 2, 3), levels


def _split_vertices(data, vert_offset_bytes, vert_count, uv_perm, need_tangents):
    """Split 96-byte rF2 TVertex2 records into GTR2 vertices, UVs, and tangents."""
    if vert_count == 0:
        return b"", b"", (b"" if need_tangents else None)

    span = vert_count * RF2_VERTEX_SIZE
    raw = memoryview(data)[vert_offset_bytes:vert_offset_bytes + span]

    if _np is not None:
        arr = _np.frombuffer(raw, dtype=_np.uint8).reshape(vert_count, RF2_VERTEX_SIZE)
        # GTR2 TVertex (32B): pos(12) + norm(12) + dcolor(4) + scolor=0(4)
        verts = _np.zeros((vert_count, GTR2_VERTEX_SIZE), dtype=_np.uint8)
        verts[:, :28] = arr[:, :28]

        # GTR2 TexturesUV (32B): 4 UV pairs (8B each), permuted from bytes 64..96
        uvs = _np.empty((vert_count, GTR2_UV_SIZE), dtype=_np.uint8)
        for dst_i, src_i in enumerate(uv_perm):
            src_off = 64 + src_i * 8
            dst_off = dst_i * 8
            uvs[:, dst_off:dst_off + 8] = arr[:, src_off:src_off + 8]

        if need_tangents:
            # GTR2 UVTangents (24B): tangent(12) + bitangent(12) from bytes 40..64
            tangents = _np.ascontiguousarray(arr[:, 40:64]).tobytes()
        else:
            tangents = None
        return verts.tobytes(), uvs.tobytes(), tangents

    verts = bytearray(vert_count * GTR2_VERTEX_SIZE)
    uvs = bytearray(vert_count * GTR2_UV_SIZE)
    tangents = bytearray(vert_count * GTR2_TANGENT_SIZE) if need_tangents else None
    p0, p1, p2, p3 = (64 + s * 8 for s in uv_perm)
    for i in range(vert_count):
        s = i * RF2_VERTEX_SIZE
        dv = i * GTR2_VERTEX_SIZE
        verts[dv:dv + 28] = raw[s:s + 28]
        du = i * GTR2_UV_SIZE
        uvs[du:du + 8] = raw[s + p0:s + p0 + 8]
        uvs[du + 8:du + 16] = raw[s + p1:s + p1 + 8]
        uvs[du + 16:du + 24] = raw[s + p2:s + p2 + 8]
        uvs[du + 24:du + 32] = raw[s + p3:s + p3 + 8]
        if need_tangents:
            dt = i * GTR2_TANGENT_SIZE
            tangents[dt:dt + 24] = raw[s + 40:s + 64]
    return bytes(verts), bytes(uvs), (bytes(tangents) if need_tangents else None)


def _dedup_mesh_vertices(verts_bytes, uvs_bytes, tans_bytes, vert_count,
                         normal_quant=DEFAULT_NORMAL_QUANT):
    """Deduplicate mesh vertices in first-appearance order.

    Returns (verts_bytes, uvs_bytes, tans_bytes, new_vcount, remap) where
    remap[old_local_idx] == new_local_idx.
    """
    has_tan = tans_bytes is not None
    q_scale = float(normal_quant)
    if vert_count <= 1:
        remap = (_np.arange(vert_count, dtype=_np.uint16)
                 if _np is not None else list(range(vert_count)))
        return verts_bytes, uvs_bytes, tans_bytes, vert_count, remap

    if _np is not None:
        va_u8 = _np.frombuffer(verts_bytes, dtype=_np.uint8).reshape(vert_count, GTR2_VERTEX_SIZE)
        ua_u8 = _np.frombuffer(uvs_bytes, dtype=_np.uint8).reshape(vert_count, GTR2_UV_SIZE)
        vf = _np.frombuffer(verts_bytes, dtype="<f4").reshape(vert_count, 8)
        vc = _np.frombuffer(verts_bytes, dtype="<u4").reshape(vert_count, 8)[:, 6:8]
        uf = _np.frombuffer(uvs_bytes, dtype="<f4").reshape(vert_count, 8)

        pos = _np.where(_np.isfinite(vf[:, :3]), vf[:, :3], 0.0).astype("<f4") + _np.float32(0.0)
        uvs = _np.where(_np.isfinite(uf), uf, 0.0).astype("<f4") + _np.float32(0.0)
        nf64 = _np.where(_np.isfinite(vf[:, 3:6]), vf[:, 3:6], 0.0).astype(_np.float64)
        nf64 = _np.clip(nf64, -1.0, 1.0)
        nrm = _np.rint(nf64 * q_scale).astype("<i2")

        if has_tan:
            ta_u8 = _np.frombuffer(tans_bytes, dtype=_np.uint8).reshape(vert_count, GTR2_TANGENT_SIZE)
            tf = _np.frombuffer(tans_bytes, dtype="<f4").reshape(vert_count, 6)
            tf64 = _np.where(_np.isfinite(tf), tf, 0.0).astype(_np.float64)
            cx = nf64[:, 1] * tf64[:, 2] - nf64[:, 2] * tf64[:, 1]
            cy = nf64[:, 2] * tf64[:, 0] - nf64[:, 0] * tf64[:, 2]
            cz = nf64[:, 0] * tf64[:, 1] - nf64[:, 1] * tf64[:, 0]
            handed = ((cx * tf64[:, 3] + cy * tf64[:, 4] + cz * tf64[:, 5]) >= 0.0).astype(_np.uint8)
            keys = _np.empty(vert_count, dtype=[
                ("p", "<f4", 3), ("n", "<i2", 3), ("c", "<u4", 2),
                ("u", "<f4", 8), ("h", "u1"),
            ])
            keys["h"] = handed
        else:
            keys = _np.empty(vert_count, dtype=[
                ("p", "<f4", 3), ("n", "<i2", 3), ("c", "<u4", 2),
                ("u", "<f4", 8),
            ])
        keys["p"] = pos
        keys["n"] = nrm
        keys["c"] = vc
        keys["u"] = uvs

        _, first_idx, inv = _np.unique(keys, return_index=True, return_inverse=True)
        new_vcount = int(len(first_idx))
        if new_vcount == vert_count:
            return verts_bytes, uvs_bytes, tans_bytes, vert_count, _np.arange(vert_count, dtype=_np.uint16)

        order = _np.argsort(first_idx)
        keep = first_idx[order]
        rank = _np.empty(new_vcount, dtype=_np.uint16)
        rank[order] = _np.arange(new_vcount, dtype=_np.uint16)
        remap = rank[inv]

        new_verts = _np.ascontiguousarray(va_u8[keep]).tobytes()
        new_uvs = _np.ascontiguousarray(ua_u8[keep]).tobytes()
        new_tans = _np.ascontiguousarray(ta_u8[keep]).tobytes() if has_tan else None
        return new_verts, new_uvs, new_tans, new_vcount, remap

    def _clean(x):
        return (x + 0.0) if math.isfinite(x) else 0.0

    def _qnorm(x):
        v = _clean(x)
        if v > 1.0:
            v = 1.0
        elif v < -1.0:
            v = -1.0
        return int(round(v * q_scale))

    seen = {}
    keep = []
    remap = [0] * vert_count
    for i in range(vert_count):
        px, py, pz, nx, ny, nz, dc, sc = struct.unpack_from("<6f2I", verts_bytes, i * GTR2_VERTEX_SIZE)
        uv = struct.unpack_from("<8f", uvs_bytes, i * GTR2_UV_SIZE)
        key_base = (
            _clean(px), _clean(py), _clean(pz),
            _qnorm(nx), _qnorm(ny), _qnorm(nz),
            dc, sc,
            tuple(_clean(c) for c in uv),
        )
        if has_tan:
            tx, ty, tz, bx, by, bz = struct.unpack_from("<6f", tans_bytes, i * GTR2_TANGENT_SIZE)
            fnx = max(-1.0, min(1.0, _clean(nx)))
            fny = max(-1.0, min(1.0, _clean(ny)))
            fnz = max(-1.0, min(1.0, _clean(nz)))
            ftx, fty, ftz = _clean(tx), _clean(ty), _clean(tz)
            fbx, fby, fbz = _clean(bx), _clean(by), _clean(bz)
            cx = fny * ftz - fnz * fty
            cy = fnz * ftx - fnx * ftz
            cz = fnx * fty - fny * ftx
            handed = 1 if (cx * fbx + cy * fby + cz * fbz) >= 0.0 else 0
            key = key_base + (handed,)
        else:
            key = key_base
        idx = seen.get(key)
        if idx is None:
            idx = len(keep)
            seen[key] = idx
            keep.append(i)
        remap[i] = idx

    new_vcount = len(keep)
    if new_vcount == vert_count:
        return verts_bytes, uvs_bytes, tans_bytes, vert_count, remap

    new_verts = bytearray(new_vcount * GTR2_VERTEX_SIZE)
    new_uvs = bytearray(new_vcount * GTR2_UV_SIZE)
    new_tans = bytearray(new_vcount * GTR2_TANGENT_SIZE) if has_tan else None
    for dst_i, src_i in enumerate(keep):
        s_v = src_i * GTR2_VERTEX_SIZE
        d_v = dst_i * GTR2_VERTEX_SIZE
        new_verts[d_v:d_v + GTR2_VERTEX_SIZE] = verts_bytes[s_v:s_v + GTR2_VERTEX_SIZE]
        s_u = src_i * GTR2_UV_SIZE
        d_u = dst_i * GTR2_UV_SIZE
        new_uvs[d_u:d_u + GTR2_UV_SIZE] = uvs_bytes[s_u:s_u + GTR2_UV_SIZE]
        if has_tan:
            s_t = src_i * GTR2_TANGENT_SIZE
            d_t = dst_i * GTR2_TANGENT_SIZE
            new_tans[d_t:d_t + GTR2_TANGENT_SIZE] = tans_bytes[s_t:s_t + GTR2_TANGENT_SIZE]
    return bytes(new_verts), bytes(new_uvs), (bytes(new_tans) if has_tan else None), new_vcount, remap


def _remap_triangles(data, tri_src, tcount, remap, old_vcount):
    """Remap uint16 mesh triangle indices through per-mesh vertex deduplication."""
    if tcount == 0:
        return b""
    if _np is not None:
        tris = _np.frombuffer(data, dtype="<u2", count=tcount, offset=tri_src)
        valid = tris < old_vcount
        safe = _np.where(valid, tris, 0)
        remapped = _np.where(valid, remap[safe], tris).astype("<u2")
        return remapped.tobytes()
    tris = struct.unpack_from("<%dH" % tcount, data, tri_src)
    remapped = [remap[idx] if idx < old_vcount else idx for idx in tris]
    return struct.pack("<%dH" % tcount, *remapped)


def _remap_face_vertices_local(face_bytes, nface, mesh_starts, mesh_vcounts, mesh_remaps):
    """Convert rF2 global FaceData vertex indices to GTR2 mesh-local deduped indices."""
    if nface == 0:
        return face_bytes
    nmesh = len(mesh_remaps)
    if _np is not None:
        face_dt = _np.dtype([
            ("v0", "<u2"), ("v1", "<u2"), ("v2", "<u2"), ("flg", "<u2"),
            ("mesh", "<u4"), ("ni", "<u4"),
            ("plane_d", "<f4"), ("cx", "<f4"), ("cy", "<f4"), ("cz", "<f4"), ("radius", "<f4"),
        ])
        faces = _np.frombuffer(face_bytes, dtype=face_dt, count=nface).copy()
        for mi in range(nmesh):
            mask = faces["mesh"] == mi
            if not mask.any():
                continue
            vstart = mesh_starts[mi]
            vcnt = mesh_vcounts[mi]
            remap = mesh_remaps[mi]
            for col in ("v0", "v1", "v2"):
                v = faces[col][mask].astype(_np.int64)
                in_global = (v >= vstart) & (v < vstart + vcnt)
                local = _np.where(in_global, v - vstart, v)
                valid = (local >= 0) & (local < vcnt)
                safe = _np.where(valid, local, 0)
                faces[col][mask] = _np.where(valid, remap[safe], faces[col][mask]).astype("<u2")
        return faces.tobytes()

    out = bytearray(face_bytes)
    for fi in range(nface):
        off = fi * GTR2_FACE_SIZE
        v0, v1, v2, _flg, mi = struct.unpack_from("<4HI", out, off)
        if mi >= nmesh:
            continue
        vstart = mesh_starts[mi]
        vcnt = mesh_vcounts[mi]
        remap = mesh_remaps[mi]
        remapped = []
        for v in (v0, v1, v2):
            if vstart <= v < vstart + vcnt:
                remapped.append(remap[v - vstart])
            elif v < vcnt:
                remapped.append(remap[v])
            else:
                remapped.append(v)
        struct.pack_into("<3H", out, off, remapped[0], remapped[1], remapped[2])
    return bytes(out)


def _dominant_axis_flag(nx, ny, nz):
    """Return gMotor face dominant-axis code: 0/+X 1/-X 2/+Y 3/-Y 4/+Z 5/-Z."""
    ax, ay, az = abs(nx), abs(ny), abs(nz)
    if ax >= ay and ax >= az:
        return 0 if nx >= 0.0 else 1
    if ay >= ax and ay >= az:
        return 2 if ny >= 0.0 else 3
    return 4 if nz >= 0.0 else 5


# Corrupted long needle-sliver triangles (e.g. 50m-340m long, <10m wide) on
# road/kerb meshes overlap hundreds of cells in GTR2's 2D XZ HTree quadtree
# and can trigger out-of-memory aborts in SpecialFX::Init().
SLIVER_SPAN_LIMIT = 50.0
SLIVER_MAX_ALTITUDE = 10.0


def _rebuild_faces_and_normals(data, voff, vcount, foff, nface, noff, nnorm,
                               max_face_span=None):
    """Rebuild GTR2 36-byte FaceData (flags, plane_d, centroid, radius) and NormalData.

    Recomputes true face normals, dominant-axis flags, plane distance
    (dot(normal, p0)), centroid, and face bounding-sphere radius directly from
    triangle vertex geometry to fix io_rf2_gmt_release's swapped-normal and
    area-vs-radius collision bugs.

    Zero-area degenerate triangles (which produce zero normals and trigger
    GTR2 ``htree.cpp`` Gaussian elimination errors) are dropped. When
    ``max_face_span`` is set, faces whose XZ bounding box exceeds that span
    (in meters) are also dropped to prevent oversized outer-terrain slivers
    from exploding GTR2's 2D XZ ``HTree`` quadtree.
    """
    if nface == 0:
        return b"", b"", 0
    span_limit = float(max_face_span) if (max_face_span is not None and max_face_span > 0.0) else None

    if _np is not None:
        verts_f32 = _np.frombuffer(
            data, dtype="<f4", count=vcount * (RF2_VERTEX_SIZE // 4), offset=voff
        ).reshape(vcount, RF2_VERTEX_SIZE // 4)
        pos = verts_f32[:, :3].astype(_np.float64)

        in_face_dt = _np.dtype([
            ("v0", "<u2"), ("v1", "<u2"), ("v2", "<u2"), ("flg", "<u2"),
            ("mesh", "<u4"), ("ni", "<u4"),
            ("cx", "<f4"), ("cy", "<f4"), ("cz", "<f4"), ("radius", "<f4"),
        ])
        out_face_dt = _np.dtype([
            ("v0", "<u2"), ("v1", "<u2"), ("v2", "<u2"), ("flg", "<u2"),
            ("mesh", "<u4"), ("ni", "<u4"),
            ("plane_d", "<f4"), ("cx", "<f4"), ("cy", "<f4"), ("cz", "<f4"), ("radius", "<f4"),
        ])
        norm_dt = _np.dtype([
            ("nx", "<f4"), ("ny", "<f4"), ("nz", "<f4"), ("sg", "<u4"),
        ])
        in_faces = _np.frombuffer(data, dtype=in_face_dt, count=nface, offset=foff)
        in_norms = (_np.frombuffer(data, dtype=norm_dt, count=nnorm, offset=noff)
                    if nnorm > 0 else _np.zeros(0, dtype=norm_dt))

        v0 = in_faces["v0"].astype(_np.int64)
        v1 = in_faces["v1"].astype(_np.int64)
        v2 = in_faces["v2"].astype(_np.int64)
        valid_idx = (v0 < vcount) & (v1 < vcount) & (v2 < vcount)
        sv0 = _np.where(valid_idx, v0, 0)
        sv1 = _np.where(valid_idx, v1, 0)
        sv2 = _np.where(valid_idx, v2, 0)

        p0 = pos[sv0]
        p1 = pos[sv1]
        p2 = pos[sv2]
        cross = -_np.cross(p1 - p0, p2 - p0)
        lengths = _np.linalg.norm(cross, axis=1)
        keep = valid_idx & (lengths > 1e-6)
        dx = (_np.maximum(_np.maximum(p0[:, 0], p1[:, 0]), p2[:, 0])
              - _np.minimum(_np.minimum(p0[:, 0], p1[:, 0]), p2[:, 0]))
        dz = (_np.maximum(_np.maximum(p0[:, 2], p1[:, 2]), p2[:, 2])
              - _np.minimum(_np.minimum(p0[:, 2], p1[:, 2]), p2[:, 2]))
        if span_limit is not None:
            keep = keep & (dx <= span_limit) & (dz <= span_limit)
        # Drop extreme needle slivers (>50m XZ span with altitude <10m) on all meshes.
        long_span = keep & ((dx > SLIVER_SPAN_LIMIT) | (dz > SLIVER_SPAN_LIMIT))
        if long_span.any():
            e0 = _np.linalg.norm(p1[long_span] - p0[long_span], axis=1)
            e1 = _np.linalg.norm(p2[long_span] - p1[long_span], axis=1)
            e2 = _np.linalg.norm(p0[long_span] - p2[long_span], axis=1)
            emax = _np.maximum(_np.maximum(e0, e1), e2)
            # lengths == 2 * area, so altitude perpendicular to longest edge is lengths / emax.
            sliver = lengths[long_span] < (SLIVER_MAX_ALTITUDE * emax)
            keep[long_span] = ~sliver

        out_nface = int(_np.count_nonzero(keep))
        if out_nface == 0:
            return b"", b"", 0

        kept_in = in_faces[keep]
        kp0 = p0[keep]
        kp1 = p1[keep]
        kp2 = p2[keep]
        kcross = cross[keep]
        klen = lengths[keep]

        centroid = (kp0 + kp1 + kp2) / 3.0
        r0 = _np.linalg.norm(kp0 - centroid, axis=1)
        r1 = _np.linalg.norm(kp1 - centroid, axis=1)
        r2 = _np.linalg.norm(kp2 - centroid, axis=1)
        radius = _np.maximum(_np.maximum(r0, r1), r2)

        inv_len = 1.0 / klen
        nx = kcross[:, 0] * inv_len
        ny = kcross[:, 1] * inv_len
        nz = kcross[:, 2] * inv_len
        sg = _np.zeros(out_nface, dtype=_np.uint32)
        if nnorm > 0:
            ni = kept_in["ni"].astype(_np.int64)
            valid_ni = ni < nnorm
            sni = _np.where(valid_ni, ni, 0)
            sg = _np.where(valid_ni, in_norms["sg"][sni], 0)

        plane_d = nx * kp0[:, 0] + ny * kp0[:, 1] + nz * kp0[:, 2]

        ax, ay, az = _np.abs(nx), _np.abs(ny), _np.abs(nz)
        is_x = (ax >= ay) & (ax >= az)
        is_y = (~is_x) & (ay >= ax) & (ay >= az)
        flg = _np.where(
            is_x,
            _np.where(nx >= 0.0, 0, 1),
            _np.where(is_y, _np.where(ny >= 0.0, 2, 3), _np.where(nz >= 0.0, 4, 5)),
        ).astype(_np.uint16)

        out_faces = _np.zeros(out_nface, dtype=out_face_dt)
        out_norms = _np.zeros(out_nface, dtype=norm_dt)
        out_faces["v0"] = kept_in["v0"]
        out_faces["v1"] = kept_in["v1"]
        out_faces["v2"] = kept_in["v2"]
        out_faces["flg"] = flg
        out_faces["mesh"] = kept_in["mesh"]
        out_faces["ni"] = _np.arange(out_nface, dtype=_np.uint32)
        out_faces["plane_d"] = plane_d.astype(_np.float32)
        out_faces["cx"] = centroid[:, 0].astype(_np.float32)
        out_faces["cy"] = centroid[:, 1].astype(_np.float32)
        out_faces["cz"] = centroid[:, 2].astype(_np.float32)
        out_faces["radius"] = radius.astype(_np.float32)
        out_norms["nx"] = nx.astype(_np.float32)
        out_norms["ny"] = ny.astype(_np.float32)
        out_norms["nz"] = nz.astype(_np.float32)
        out_norms["sg"] = sg
        return out_faces.tobytes(), out_norms.tobytes(), out_nface

    out_faces = bytearray()
    out_norms = bytearray()
    out_idx = 0
    for fi in range(nface):
        fpos = foff + fi * RF2_FACE_SIZE
        v0, v1, v2, _flg, mesh_i, ni, _cx, _cy, _cz, _radius = struct.unpack_from(
            "<4H2I4f", data, fpos)
        if not (v0 < vcount and v1 < vcount and v2 < vcount):
            continue
        p0 = struct.unpack_from("<3f", data, voff + v0 * RF2_VERTEX_SIZE)
        p1 = struct.unpack_from("<3f", data, voff + v1 * RF2_VERTEX_SIZE)
        p2 = struct.unpack_from("<3f", data, voff + v2 * RF2_VERTEX_SIZE)
        ux, uy, uz = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
        vx, vy, vz = p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2]
        tnx = -(uy * vz - uz * vy)
        tny = -(uz * vx - ux * vz)
        tnz = -(ux * vy - uy * vx)
        length = (tnx * tnx + tny * tny + tnz * tnz) ** 0.5
        if length <= 1e-6:
            continue
        dx = max(p0[0], p1[0], p2[0]) - min(p0[0], p1[0], p2[0])
        dz = max(p0[2], p1[2], p2[2]) - min(p0[2], p1[2], p2[2])
        if span_limit is not None and (dx > span_limit or dz > span_limit):
            continue
        if dx > SLIVER_SPAN_LIMIT or dz > SLIVER_SPAN_LIMIT:
            e0 = (ux * ux + uy * uy + uz * uz) ** 0.5
            w1x, w1y, w1z = p2[0] - p1[0], p2[1] - p1[1], p2[2] - p1[2]
            e1 = (w1x * w1x + w1y * w1y + w1z * w1z) ** 0.5
            e2 = (vx * vx + vy * vy + vz * vz) ** 0.5
            emax = max(e0, e1, e2)
            if length < SLIVER_MAX_ALTITUDE * emax:
                continue
        nx, ny, nz = tnx / length, tny / length, tnz / length
        sg = struct.unpack_from("<I", data, noff + ni * NORMAL_SIZE + 12)[0] if ni < nnorm else 0
        cx = (p0[0] + p1[0] + p2[0]) / 3.0
        cy = (p0[1] + p1[1] + p2[1]) / 3.0
        cz = (p0[2] + p1[2] + p2[2]) / 3.0
        radius = max(
            ((p[0] - cx) ** 2 + (p[1] - cy) ** 2 + (p[2] - cz) ** 2) ** 0.5
            for p in (p0, p1, p2)
        )
        plane_d = nx * p0[0] + ny * p0[1] + nz * p0[2]
        flg = _dominant_axis_flag(nx, ny, nz)
        out_faces.extend(struct.pack(
            "<4H2I5f", v0, v1, v2, flg, mesh_i, out_idx, plane_d, cx, cy, cz, radius))
        out_norms.extend(struct.pack("<3fI", nx, ny, nz, sg))
        out_idx += 1
    return bytes(out_faces), bytes(out_norms), out_idx


def _bbox_corners(bbox_min, bbox_max):
    """Build the 8 bounding-box corners in gMotor bottom-then-top order."""
    x0, y0, z0 = bbox_min
    x1, y1, z1 = bbox_max
    return (
        x1, y0, z0,
        x1, y0, z1,
        x0, y0, z1,
        x0, y0, z0,
        x1, y1, z0,
        x1, y1, z1,
        x0, y1, z1,
        x0, y1, z0,
    )


def _brighten_material_colors(m_colors, brightness):
    """Lift GMT material Diffuse/Ambient RGB toward 1.0 by ``0.5 * brightness``.

    At ``brightness == 0.0`` returns exact input bytes; ``1.0`` lifts halfway
    toward 1.0 (bright sunny look). Leaves alpha, specular, and emissive
    unchanged so transparency, specular highlights, and night lighting stay
    natural.
    """
    b = float(brightness or 0.0)
    if b <= 0.0:
        return m_colors
    t = 0.5 * min(1.0, b)
    cols = list(struct.unpack("<16f", m_colors))
    if max(cols[0], cols[1], cols[2], cols[4], cols[5], cols[6]) <= 0.0:
        return m_colors
    for i in (0, 1, 2, 4, 5, 6):
        c = max(0.0, min(1.0, cols[i]))
        cols[i] = c + (1.0 - c) * t
    return struct.pack("<16f", *cols)


def collect_diffuse_textures(gmt_dir):
    """Return lowercased diffuse DDS basenames used in stage-0 slots of GTR2 GMTs.

    Excludes skybox/gate helper GMTs, pseudo-textures (``MATH``), helper maps,
    and any texture also bound to a bump or specular stage.
    """
    skip_gmts = {"skyboxi.gmt", "xpitin.gmt", "xpitout.gmt"}
    skip_texs = {"math", "_invisible.dds", "_flat_nm.dds", "_flat_spec.dds", "_white.dds"}
    diffuse = set()
    protected = set()
    if not os.path.isdir(gmt_dir):
        return diffuse
    for fname in os.listdir(gmt_dir):
        if not fname.lower().endswith(".gmt") or fname.lower() in skip_gmts:
            continue
        path = os.path.join(gmt_dir, fname)
        try:
            with open(path, "rb") as f:
                b = f.read()
        except OSError:
            continue
        if not is_gtr2_gmt(b) or len(b) < GTR2_HEADER_SIZE:
            continue
        mat_off = struct.unpack_from("<I", b, 0)[0] + 4
        if mat_off + 4 > len(b):
            continue
        nmat = struct.unpack_from("<I", b, mat_off)[0]
        pos = mat_off + 4
        for _ in range(nmat):
            need = (GTR2_MAT_HEADER_SIZE + 3 * GTR2_SHADER_INFO_SIZE
                    + GTR2_MAT_POST_SHADERS_PAD + 4)
            if pos + need > len(b):
                break
            pos += GTR2_MAT_HEADER_SIZE
            levels = []
            for _lv in range(3):
                tcnt = struct.unpack_from("<I", b, pos + 4)[0]
                sh = b[pos + 8:pos + 72].split(b"\x00", 1)[0].decode(
                    "ascii", errors="replace").upper()
                levels.append((sh, tcnt))
                pos += GTR2_SHADER_INFO_SIZE
            pos += GTR2_MAT_POST_SHADERS_PAD
            ntex = struct.unpack_from("<I", b, pos)[0]
            pos += 4
            tex_names = []
            for _ in range(ntex):
                if pos + 80 > len(b):
                    break
                sz = tex_record_size(b, pos)
                if pos + sz > len(b):
                    break
                tname = b[pos:pos + 64].split(b"\x00", 1)[0].decode(
                    "ascii", errors="replace").strip().lower()
                tex_names.append(tname)
                pos += sz
            cursor = 0
            for sh, tcnt in levels:
                slice_tex = tex_names[cursor:cursor + tcnt]
                cursor += tcnt
                is_two_stage = "T0XT1" in sh
                for ti, tname in enumerate(slice_tex):
                    if not tname or tname in skip_texs:
                        continue
                    if ti == 0:
                        diffuse.add(tname)
                    elif (is_two_stage and ti >= 2) or (not is_two_stage and ti >= 1):
                        protected.add(tname)
    return diffuse - protected


def convert_rf2_to_gtr2(data, label="GMT", mesh_coarsening=0.0,
                        include_faces=True, drop_flat_tangents=False,
                        track_brightness=0.0, max_face_span=None):
    """Convert raw rF2 GMT v2.52 bytes to GTR2 GMT v2.10 bytes.

    Already-converted GTR2 GMT v2.10 inputs are returned unchanged.
    When ``include_faces`` is False (for non-collidable visual-only GMTs),
    collision FaceData and NormalData sections are omitted (nface=0, nnorm=0).
    When ``drop_flat_tangents`` is True, flat-normal/flat-spec ``L2BUMPSPECMAPT0``
    cutouts are downgraded to ``L2DIFFUSET0`` without UVTangents.
    When ``max_face_span`` is set (meters), oversized collision faces whose XZ
    span exceeds it are omitted from FaceData.
    """
    if is_gtr2_gmt(data):
        return data
    n_quant = normal_dedup_quant(mesh_coarsening)
    if not is_rf2_gmt(data):
        raise RuntimeError(
            "unsupported GMT version or corrupted header in %s" % label)
    if len(data) < RF2_HEADER_SIZE:
        raise RuntimeError("truncated rF2 GMT header in %s" % label)

    mat_off = struct.unpack_from("<I", data, 0)[0] + 4
    bbox_min = struct.unpack_from("<3f", data, 0x0C8)
    bbox_max = struct.unpack_from("<3f", data, 0x0D4)
    radius = struct.unpack_from("<f", data, 0x0E0)[0]
    origin = struct.unpack_from("<3f", data, 0x0E4)
    pivot = struct.unpack_from("<3f", data, 0x0F0)
    lod_in, lod_out = struct.unpack_from("<2f", data, 0x100)
    (vcount, voff_rel, tcount, toff_rel, nmesh, moff_rel,
     nface, foff_rel, nnorm, noff_rel, bcnt, _boff_rel) = struct.unpack_from(
        "<12I", data, 0x13C)
    name_raw = data[0x17C:0x1BC]

    if bcnt != 0:
        raise RuntimeError(
            "rigged rF2 GMT with %d bones is not supported: %s" % (bcnt, label))

    voff = voff_rel + 4
    toff = toff_rel + 4
    moff = moff_rel + 4
    foff = foff_rel + 4
    noff = noff_rel + 4

    for section, start, size in (
        ("meshes", moff, nmesh * RF2_MESH_SIZE),
        ("faces", foff, nface * RF2_FACE_SIZE),
        ("normals", noff, nnorm * NORMAL_SIZE),
        ("vertices", voff, vcount * RF2_VERTEX_SIZE),
        ("triangles", toff, tcount * 2),
    ):
        if start + size > len(data):
            raise RuntimeError(
                "truncated rF2 GMT %s section in %s" % (section, label))
    if mat_off + 4 > len(data):
        raise RuntimeError("truncated rF2 GMT materials section in %s" % label)

    # Parse rF2 materials first so each mesh knows its shader, UV permutation,
    # and whether tangent/bitangent streams are required.
    nmat = struct.unpack_from("<I", data, mat_off)[0]
    pos = mat_off + 4
    materials = []
    for mi in range(nmat):
        if pos + RF2_MAT_HEADER_SIZE > len(data):
            raise RuntimeError(
                "truncated rF2 GMT material %d in %s" % (mi, label))
        m_name = data[pos:pos + 64]
        m_flags = struct.unpack_from("<I", data, pos + 64)[0] & ~MAT_FLAG_RF2_CARBODY
        m_colors = _brighten_material_colors(data[pos + 68:pos + 132], track_brightness)
        src_blend, dst_blend = struct.unpack_from("<2I", data, pos + 132)
        spec_power = struct.unpack_from("<f", data, pos + 140)[0]
        rf2_shader = data[pos + 148:pos + 212].split(b"\x00", 1)[0].decode(
            "ascii", errors="replace")
        ntex = struct.unpack_from("<I", data, pos + 256)[0]
        pos += RF2_MAT_HEADER_SIZE
        textures = []
        for ti in range(ntex):
            if pos + RF2_TEXTURE_SIZE > len(data):
                raise RuntimeError(
                    "truncated rF2 GMT texture %d in %s" % (ti, label))
            tex_raw = data[pos:pos + RF2_TEXTURE_SIZE]
            anim = struct.unpack_from("<I", tex_raw, 72)[0]
            if anim != 1:
                raise RuntimeError(
                    "animated texture (type %d) is not supported in %s"
                    % (anim, label))
            textures.append(tex_raw)
            pos += RF2_TEXTURE_SIZE
        has_bump, uv_perm, levels = _map_material_for_gtr2(
            rf2_shader, textures, drop_flat_tangents=drop_flat_tangents)
        materials.append({
            "name": m_name,
            "flags": m_flags,
            "colors": m_colors,
            "src_blend": src_blend,
            "dst_blend": dst_blend,
            "spec_power": spec_power,
            "has_bump": has_bump,
            "uv_perm": uv_perm,
            "levels": levels,
        })
    if pos != len(data):
        raise RuntimeError(
            "unexpected trailing bytes (%d) after rF2 GMT materials in %s"
            % (len(data) - pos, label))

    # Parse rF2 mesh descriptors.
    meshes = []
    for mi in range(nmesh):
        base = moff + mi * RF2_MESH_SIZE
        vals = struct.unpack_from("<10I", data, base)
        m_flags = vals[0]
        m_vcount = vals[1]
        m_tcount = vals[3]
        m_vstart = vals[6]
        m_tstart = vals[8]
        m_mat = vals[9]
        if m_vstart + m_vcount > vcount or m_tstart + m_tcount > tcount:
            raise RuntimeError(
                "rF2 GMT mesh %d geometry range out of bounds in %s" % (mi, label))
        if nmat and m_mat >= nmat:
            raise RuntimeError(
                "rF2 GMT mesh %d material index %d out of range in %s"
                % (mi, m_mat, label))
        meshes.append((m_flags, m_vcount, m_tcount, m_vstart, m_tstart, m_mat))

    # Assemble GTR2 GMT v2.10 binary.
    out = bytearray(GTR2_HEADER_SIZE + nmesh * GTR2_MESH_SIZE)

    if include_faces:
        face_bytes, norm_bytes, out_nface = _rebuild_faces_and_normals(
            data, voff, vcount, foff, nface, noff, nnorm,
            max_face_span=max_face_span)
        out_nnorm = out_nface
    else:
        face_bytes, norm_bytes, out_nface, out_nnorm = b"", b"", 0, 0

    mesh_payloads = []
    mesh_starts = []
    mesh_vcounts = []
    mesh_remaps = []
    for mi, (m_flags, m_vcount, m_tcount, m_vstart, m_tstart, m_mat) in enumerate(meshes):
        mat = materials[m_mat] if materials else None
        has_bump = bool(mat["has_bump"]) if mat else False
        uv_perm = mat["uv_perm"] if mat else (0, 1, 2, 3)

        verts_bytes, uvs_bytes, tans_bytes = _split_vertices(
            data, voff + m_vstart * RF2_VERTEX_SIZE, m_vcount, uv_perm, has_bump)
        verts_bytes, uvs_bytes, tans_bytes, new_vcount, remap = _dedup_mesh_vertices(
            verts_bytes, uvs_bytes, tans_bytes, m_vcount, normal_quant=n_quant)
        tris_bytes = _remap_triangles(
            data, toff + m_tstart * 2, m_tcount, remap, m_vcount)

        mesh_starts.append(m_vstart)
        mesh_vcounts.append(m_vcount)
        mesh_remaps.append(remap)
        mesh_payloads.append((
            m_flags, has_bump, new_vcount, m_tcount, m_mat,
            verts_bytes, uvs_bytes, tans_bytes, tris_bytes,
        ))

    if include_faces and out_nface > 0:
        face_bytes = _remap_face_vertices_local(
            face_bytes, out_nface, mesh_starts, mesh_vcounts, mesh_remaps)

    face_off = len(out)
    out.extend(face_bytes)

    norm_off = len(out)
    out.extend(norm_bytes)

    first_vert_off = None
    for mi, (m_flags, has_bump, new_vcount, m_tcount, m_mat,
             verts_bytes, uvs_bytes, tans_bytes, tris_bytes) in enumerate(mesh_payloads):
        m_voff = len(out)
        if first_vert_off is None:
            first_vert_off = m_voff
        out.extend(verts_bytes)

        m_uvoff = len(out)
        out.extend(uvs_bytes)

        if has_bump:
            m_tanoff = len(out)
            out.extend(tans_bytes)
            gtr2_mflags = m_flags | MESH_FLAG_TANGENTS
            gtr2_tan_rel = m_tanoff - 4
        else:
            gtr2_mflags = m_flags & ~MESH_FLAG_TANGENTS
            gtr2_tan_rel = 0

        m_trioff = len(out)
        out.extend(tris_bytes)

        # Write 108-byte GTR2 TGMTMesh descriptor.
        mbase = GTR2_HEADER_SIZE + mi * GTR2_MESH_SIZE
        struct.pack_into(
            "<11I", out, mbase,
            gtr2_mflags,    # +0x00 flags
            m_mat,          # +0x04 material index
            new_vcount,     # +0x08 vertex count
            m_voff - 4,     # +0x0C vertex offset
            0,              # +0x10 unused
            m_uvoff - 4,    # +0x14 UV offset
            0,              # +0x18 unused (extra UV stream when bit 0x20 set)
            gtr2_tan_rel,   # +0x1C tangent offset
            0,              # +0x20 unused
            m_tcount,       # +0x24 triangle index count
            m_trioff - 4,   # +0x28 triangle index offset
        )
        struct.pack_into("<I", out, mbase + 0x5C, 0xFFFFFFFF)

    # Write GTR2 materials section (identical 3-level structure to rF1).
    gtr2_mat_off = len(out)
    out.extend(struct.pack("<I", len(materials)))
    for mat in materials:
        out.extend(mat["name"])
        out.extend(struct.pack("<I", mat["flags"]))
        out.extend(mat["colors"])
        out.extend(struct.pack("<2If", mat["src_blend"], mat["dst_blend"],
                               mat["spec_power"]))
        tex_entries = []
        for shader_name, level_texs in mat["levels"]:
            first_tex = len(tex_entries)
            shader_block = bytearray(GTR2_SHADER_INFO_SIZE)
            struct.pack_into("<2I", shader_block, 0, first_tex, len(level_texs))
            shader_block[8:72] = _pad_ascii(shader_name, 64)
            out.extend(shader_block)
            for tex_raw, stage in level_texs:
                tex_entries.append(_rf2_tex_to_gtr2(tex_raw, stage))
        out.extend(bytes(GTR2_MAT_POST_SHADERS_PAD))
        out.extend(struct.pack("<I", len(tex_entries)))
        for entry in tex_entries:
            out.extend(entry)

    # Fill GTR2 header (532 bytes = 0x214).
    struct.pack_into("<2I", out, 0, gtr2_mat_off - 4, GTR2_GMT_FLAGS)
    out[GTR2_MAGIC_OFFSET:GTR2_MAGIC_OFFSET + len(GTR2_GMT_MAGIC)] = GTR2_GMT_MAGIC
    struct.pack_into("<f", out, 0x018, radius)
    struct.pack_into("<24f", out, 0x01C, *_bbox_corners(bbox_min, bbox_max))
    struct.pack_into("<3f", out, 0x0DC, *origin)
    struct.pack_into("<3f", out, 0x0E8, *pivot)
    struct.pack_into("<2f", out, 0x0F4, lod_in, lod_out)
    struct.pack_into(
        "<8I", out, 0x16C,
        nmesh,
        GTR2_HEADER_SIZE - 4,
        out_nface,
        (face_off - 4) if out_nface else 0,
        out_nnorm,
        (norm_off - 4) if out_nnorm else 0,
        0,
        (first_vert_off - 4) if first_vert_off is not None else 0,
    )
    out[0x194:0x1D4] = name_raw
    return bytes(out)


def make_minimal_gtr2_gmt(name="skyboxi", texture="_invisible.dds"):
    """Build a valid minimal double-sided GTR2 GMT v2.10 quad."""
    return make_quad_gtr2_gmt(
        name,
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 0.0, 1.0), (0.0, 0.0, 1.0)),
        texture=texture,
    )


def make_quad_gtr2_gmt(name, corners, texture="_invisible.dds"):
    """Build a valid double-sided 4-triangle (8-vertex) GTR2 GMT v2.10 quad."""
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    zs = [c[2] for c in corners]
    bmin = (min(xs), min(ys), min(zs))
    bmax = (max(xs), max(ys), max(zs))
    center = tuple((a + b) * 0.5 for a, b in zip(bmin, bmax))
    dx, dy, dz = (b - a for a, b in zip(bmin, bmax))
    radius = max(1.0, 0.5 * (dx * dx + dy * dy + dz * dz) ** 0.5)

    # Double-sided vertices: 0..3 front face, 4..7 back face.
    verts = list(corners) + list(corners)
    tris = ((0, 1, 2), (0, 2, 3), (4, 6, 5), (4, 7, 6))

    tri_info = []
    for i0, i1, i2 in tris:
        p0, p1, p2 = verts[i0], verts[i1], verts[i2]
        ux, uy, uz = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
        vx, vy, vz = p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2]
        nx = -(uy * vz - uz * vy)
        ny = -(uz * vx - ux * vz)
        nz = -(ux * vy - uy * vx)
        nlen = (nx * nx + ny * ny + nz * nz) ** 0.5
        if nlen > 1e-12:
            nx, ny, nz = nx / nlen, ny / nlen, nz / nlen
        else:
            nx, ny, nz = 0.0, 1.0, 0.0
        plane_d = nx * p0[0] + ny * p0[1] + nz * p0[2]
        cx = (p0[0] + p1[0] + p2[0]) / 3.0
        cy = (p0[1] + p1[1] + p2[1]) / 3.0
        cz = (p0[2] + p1[2] + p2[2]) / 3.0
        face_rad = max(
            ((p[0] - cx) ** 2 + (p[1] - cy) ** 2 + (p[2] - cz) ** 2) ** 0.5
            for p in (p0, p1, p2)
        )
        flg = _dominant_axis_flag(nx, ny, nz)
        tri_info.append((i0, i1, i2, flg, plane_d, cx, cy, cz, face_rad, nx, ny, nz))

    fn = tri_info[0][9:12]
    bn = tri_info[2][9:12]
    vnorms = [fn] * 4 + [bn] * 4

    out = bytearray(GTR2_HEADER_SIZE + GTR2_MESH_SIZE)
    face_off = len(out)
    for ti, (i0, i1, i2, flg, plane_d, cx, cy, cz, face_rad, _nx, _ny, _nz) in enumerate(tri_info):
        out.extend(struct.pack("<4H2I5f", i0, i1, i2, flg, 0, ti, plane_d, cx, cy, cz, face_rad))
    norm_off = len(out)
    for _i0, _i1, _i2, _flg, _pd, _cx, _cy, _cz, _rad, nx, ny, nz in tri_info:
        out.extend(struct.pack("<3fI", nx, ny, nz, 0))
    vert_off = len(out)
    for (vx, vy, vz), (nx, ny, nz) in zip(verts, vnorms):
        out.extend(struct.pack("<6f2I", vx, vy, vz, nx, ny, nz, 0xFFFFFFFF, 0))
    uv_off = len(out)
    for u_, v_ in ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)) * 2:
        out.extend(struct.pack("<8f", u_, v_, u_, v_, u_, v_, u_, v_))
    tri_off = len(out)
    for i0, i1, i2 in tris:
        out.extend(struct.pack("<3H", i0, i1, i2))
    mat_off = len(out)
    out.extend(struct.pack("<I", 1))
    out.extend(_pad_ascii(name, 64))
    out.extend(struct.pack("<I", 0x68900003))
    out.extend(struct.pack("<16f", *([1.0, 1.0, 1.0, 1.0] * 3 + [0.0, 0.0, 0.0, 1.0])))
    out.extend(struct.pack("<2If", 2, 1, 1.0))
    for si, sname in enumerate(("L0DIFFUSET0", "L1DIFFUSET0", "L2DIFFUSET0")):
        sb = bytearray(GTR2_SHADER_INFO_SIZE)
        struct.pack_into("<2I", sb, 0, si, 1)
        sb[8:72] = _pad_ascii(sname, 64)
        out.extend(sb)
    out.extend(bytes(GTR2_MAT_POST_SHADERS_PAD))
    out.extend(struct.pack("<I", 3))
    for _ in range(3):
        tb = bytearray(GTR2_TEXTURE_SIZE)
        tb[:64] = _pad_ascii(texture, 64)
        struct.pack_into("<IiIIIfI", tb, 64, 0x18, -1, 1, 1, 3, 1.0, 0)
        struct.pack_into("<I", tb, 100, 1)
        out.extend(tb)
    struct.pack_into(
        "<11I", out, GTR2_HEADER_SIZE,
        0x20004011, 0, 8, vert_off - 4, 0, uv_off - 4, 0, 0, 0, 12, tri_off - 4,
    )
    struct.pack_into("<I", out, GTR2_HEADER_SIZE + 0x5C, 0xFFFFFFFF)
    struct.pack_into("<2I", out, 0, mat_off - 4, GTR2_GMT_FLAGS)
    out[GTR2_MAGIC_OFFSET:GTR2_MAGIC_OFFSET + len(GTR2_GMT_MAGIC)] = GTR2_GMT_MAGIC
    struct.pack_into("<f", out, 0x018, radius)
    struct.pack_into("<24f", out, 0x01C, *_bbox_corners(bmin, bmax))
    struct.pack_into("<3f", out, 0x0DC, *center)
    struct.pack_into("<3f", out, 0x0E8, *center)
    struct.pack_into("<2f", out, 0x0F4, 0.0, 2000.0)
    struct.pack_into(
        "<8I", out, 0x16C,
        1, GTR2_HEADER_SIZE - 4,
        4, face_off - 4, 4, norm_off - 4, 0, vert_off - 4,
    )
    out[0x194:0x1D4] = _pad_ascii(name, 64)
    return bytes(out)


# rF1 .tdf surface prefixes not present in GTR2's GameData\Locations\specialfx.tec,
# mapped to the closest GTR2 specialfx.tec prefix so roads/walls/grass keep proper grip.
_GTR2_SPECIALFX_PREFIXES = (
    "road", "conc", "lgroad", "b1road", "b2road", "b3road", "marbles", "gbrm",
    "gras", "logo", "fldgrass", "rdgrass",
    "grv", "sand", "sber", "dirt", "grav", "gber", "bsand",
    "brick", "rmbl", "rmbbl", "rdgreen", "drain",
    "twall", "cema", "cwal", "cmwl", "grdr",
)

_RF1_SURFACE_PREFIX_MAP = (
    ("rdax", "road_"),
    ("rdta", "road_"),
    ("rdcp", "road_"),
    ("rged", "road_"),
    ("stick", "road_"),
    ("grxs", "gras_"),
    ("dstg", "grvl_"),
    ("gtrp", "grvl_"),
    ("twal", "twall_"),
    ("cem", "cwal_"),
    ("wall", "cwal_"),
    ("safer", "cwal_"),
)


def _remap_rf1_surface_name(name):
    """Ensure rF1 .tdf surface material prefixes match GTR2's specialfx.tec."""
    low = name.lower()
    if any(low.startswith(p) for p in _GTR2_SPECIALFX_PREFIXES):
        return name
    for rf1_pre, gtr2_pre in _RF1_SURFACE_PREFIX_MAP:
        if low.startswith(rf1_pre):
            return (gtr2_pre + name)[:63]
    return name


def _rebuild_rf1_faces_and_normals(data, foff, nface, noff, nnorm, mesh_verts):
    """Convert rF1 32-byte FaceData (mesh-local indices) to GTR2 36-byte FaceData."""
    if nface == 0:
        return b"", b"", 0

    if _np is not None:
        in_face_dt = _np.dtype([
            ("v0", "<u2"), ("v1", "<u2"), ("v2", "<u2"), ("flg", "<u2"),
            ("mesh", "<u4"), ("ni", "<u4"),
            ("cx", "<f4"), ("cy", "<f4"), ("cz", "<f4"), ("radius", "<f4"),
        ])
        out_face_dt = _np.dtype([
            ("v0", "<u2"), ("v1", "<u2"), ("v2", "<u2"), ("flg", "<u2"),
            ("mesh", "<u4"), ("ni", "<u4"), ("plane_d", "<f4"),
            ("cx", "<f4"), ("cy", "<f4"), ("cz", "<f4"), ("radius", "<f4"),
        ])
        norm_dt = _np.dtype([
            ("nx", "<f4"), ("ny", "<f4"), ("nz", "<f4"), ("sg", "<u4"),
        ])
        in_faces = _np.frombuffer(data, dtype=in_face_dt, count=nface, offset=foff)
        in_norms = (
            _np.frombuffer(data, dtype=norm_dt, count=nnorm, offset=noff)
            if nnorm > 0 else None
        )
        out_faces = _np.empty(nface, dtype=out_face_dt)
        out_norms = _np.empty(nface, dtype=norm_dt)

        # Build a flat lookup of mesh vertex positions indexed by (mesh_base + local_idx).
        mesh_bases = []
        pos_parts = []
        running = 0
        for mv in mesh_verts:
            mesh_bases.append(running)
            if len(mv):
                vf = _np.frombuffer(mv, dtype="<f4").reshape(-1, 8)
                pos_parts.append(vf[:, :3].astype(_np.float64, copy=False))
                running += vf.shape[0]
        if pos_parts:
            all_pos = _np.concatenate(pos_parts, axis=0)
        else:
            all_pos = _np.zeros((0, 3), dtype=_np.float64)
        mesh_vcnts = _np.array([len(mv) // GTR2_VERTEX_SIZE for mv in mesh_verts], dtype=_np.int64)
        mesh_bases_arr = _np.array(mesh_bases, dtype=_np.int64)

        mi = in_faces["mesh"].astype(_np.int64)
        v0 = in_faces["v0"].astype(_np.int64)
        v1 = in_faces["v1"].astype(_np.int64)
        v2 = in_faces["v2"].astype(_np.int64)
        valid_m = (mi >= 0) & (mi < len(mesh_verts))
        smi = _np.where(valid_m, mi, 0)
        vc = mesh_vcnts[smi] if len(mesh_verts) else _np.zeros(nface, dtype=_np.int64)
        valid_idx = valid_m & (v0 < vc) & (v1 < vc) & (v2 < vc) & (all_pos.shape[0] > 0)
        base = mesh_bases_arr[smi] if len(mesh_verts) else _np.zeros(nface, dtype=_np.int64)
        g0 = _np.where(valid_idx, base + v0, 0)
        g1 = _np.where(valid_idx, base + v1, 0)
        g2 = _np.where(valid_idx, base + v2, 0)

        if all_pos.shape[0] > 0:
            p0 = all_pos[g0]
            p1 = all_pos[g1]
            p2 = all_pos[g2]
            u = p1 - p0
            v = p2 - p0
            cross = -_np.cross(u, v)
            lengths = _np.sqrt(_np.sum(cross * cross, axis=1))
            keep = valid_idx & (lengths > 1e-6)
        else:
            keep = _np.zeros(nface, dtype=bool)

        out_nface = int(_np.count_nonzero(keep))
        if out_nface == 0:
            return b"", b"", 0

        kept_in = in_faces[keep]
        kp0 = p0[keep]
        kp1 = p1[keep]
        kp2 = p2[keep]
        kcross = cross[keep]
        klen = lengths[keep]

        centroid = (kp0 + kp1 + kp2) / 3.0
        d0 = _np.sum((kp0 - centroid) ** 2, axis=1)
        d1 = _np.sum((kp1 - centroid) ** 2, axis=1)
        d2 = _np.sum((kp2 - centroid) ** 2, axis=1)
        radius = _np.sqrt(_np.maximum(_np.maximum(d0, d1), d2))

        inv_len = 1.0 / klen
        nx = kcross[:, 0] * inv_len
        ny = kcross[:, 1] * inv_len
        nz = kcross[:, 2] * inv_len
        sg = _np.zeros(out_nface, dtype=_np.uint32)
        if in_norms is not None:
            ni = kept_in["ni"].astype(_np.int64)
            valid_ni = ni < nnorm
            sni = _np.where(valid_ni, ni, 0)
            sg = _np.where(valid_ni, in_norms["sg"][sni], 0)

        plane_d = nx * kp0[:, 0] + ny * kp0[:, 1] + nz * kp0[:, 2]
        ax, ay, az = _np.abs(nx), _np.abs(ny), _np.abs(nz)
        is_x = (ax >= ay) & (ax >= az)
        is_y = (~is_x) & (ay >= ax) & (ay >= az)
        flg = _np.where(
            is_x,
            _np.where(nx >= 0.0, 0, 1),
            _np.where(is_y, _np.where(ny >= 0.0, 2, 3), _np.where(nz >= 0.0, 4, 5)),
        ).astype(_np.uint16)

        out_faces = _np.empty(out_nface, dtype=out_face_dt)
        out_norms = _np.empty(out_nface, dtype=norm_dt)
        out_faces["v0"] = kept_in["v0"]
        out_faces["v1"] = kept_in["v1"]
        out_faces["v2"] = kept_in["v2"]
        out_faces["flg"] = flg
        out_faces["mesh"] = kept_in["mesh"]
        out_faces["ni"] = _np.arange(out_nface, dtype=_np.uint32)
        out_faces["plane_d"] = plane_d.astype(_np.float32)
        out_faces["cx"] = centroid[:, 0].astype(_np.float32)
        out_faces["cy"] = centroid[:, 1].astype(_np.float32)
        out_faces["cz"] = centroid[:, 2].astype(_np.float32)
        out_faces["radius"] = radius.astype(_np.float32)
        out_norms["nx"] = nx.astype(_np.float32)
        out_norms["ny"] = ny.astype(_np.float32)
        out_norms["nz"] = nz.astype(_np.float32)
        out_norms["sg"] = sg
        return out_faces.tobytes(), out_norms.tobytes(), out_nface

    out_faces = bytearray()
    out_norms = bytearray()
    out_idx = 0
    for fi in range(nface):
        v0, v1, v2, _flg, mesh_i, ni, _cx, _cy, _cz, _radius = struct.unpack_from(
            "<4H2I4f", data, foff + fi * RF1_FACE_SIZE
        )
        if mesh_i >= len(mesh_verts):
            continue
        mv = mesh_verts[mesh_i]
        vcnt = len(mv) // GTR2_VERTEX_SIZE
        if not (v0 < vcnt and v1 < vcnt and v2 < vcnt):
            continue
        p0 = struct.unpack_from("<3f", mv, v0 * GTR2_VERTEX_SIZE)
        p1 = struct.unpack_from("<3f", mv, v1 * GTR2_VERTEX_SIZE)
        p2 = struct.unpack_from("<3f", mv, v2 * GTR2_VERTEX_SIZE)
        ux, uy, uz = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
        vx, vy, vz = p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2]
        tnx = -(uy * vz - uz * vy)
        tny = -(uz * vx - ux * vz)
        tnz = -(ux * vy - uy * vx)
        length = (tnx * tnx + tny * tny + tnz * tnz) ** 0.5
        if length <= 1e-6:
            continue
        nx, ny, nz = tnx / length, tny / length, tnz / length
        sg = struct.unpack_from("<I", data, noff + ni * NORMAL_SIZE + 12)[0] if ni < nnorm else 0
        cx = (p0[0] + p1[0] + p2[0]) / 3.0
        cy = (p0[1] + p1[1] + p2[1]) / 3.0
        cz = (p0[2] + p1[2] + p2[2]) / 3.0
        radius = max(
            ((p[0] - cx) ** 2 + (p[1] - cy) ** 2 + (p[2] - cz) ** 2) ** 0.5
            for p in (p0, p1, p2)
        )
        plane_d = nx * p0[0] + ny * p0[1] + nz * p0[2]
        flg = _dominant_axis_flag(nx, ny, nz)
        out_faces.extend(struct.pack(
            "<4H2I5f", v0, v1, v2, flg, mesh_i, out_idx, plane_d, cx, cy, cz, radius,
        ))
        out_norms.extend(struct.pack("<3fI", nx, ny, nz, sg))
        out_idx += 1
    return bytes(out_faces), bytes(out_norms), out_idx


RF1_SHADER_MAP = {
    "L2USEVERTEXONLY": "L2USEVERTEXPIXEL",
    # rF1 CubeMap Add T0 variants -> GTR2 CUBEMAP.GFX exact stage-count matches
    # (L0=3: Diffuse+Cube+Math, L1=4: Diffuse+Cube+2*Math, L2=2: Diffuse+Cube).
    "L0CMAPADDT0": "L0CMAPT0",
    "L1CMAPADDT0": "L1CMAPT0",
    "L2CMAPADDT0": "L2CMAPT0",
    "L0CMAPADDT0T1": "L0CMAPT0",
    "L1CMAPADDT0T1": "L1CMAPT0T1",
    "L2CMAPADDT0T1": "L2CMAPT0T1",
    "L0CMAPADDT0XT1": "L0CMAPT0",
    "L1CMAPADDT0XT1": "L1CMAPT0XT1",
    "L2CMAPADDT0XT1": "L2CMAPT0XT1",
    # rF1 vertex-alpha lerp diffuse variants -> GTR2 DIFFUSE.GFX exact stage-count
    # multiply (T0xT1*) matches. GTR2's T0T1* variants are additive (T0+T1, which
    # blows terrain out to bright yellow-white) and L2DiffuseT0lerpT1 is unlit.
    "L1DIFFUSET0LERPT1T2VERTEXALPHA": "L1DIFFUSET0XT1T2",
    "L2DIFFUSET0LERPT1T2VERTEXALPHA": "L2DIFFUSET0XT1T2",
    "L1DIFFUSET0LERPT1XT2VERTEXALPHA": "L1DIFFUSET0XT1XT2",
    "L2DIFFUSET0LERPT1XT2VERTEXALPHA": "L2DIFFUSET0XT1XT2",
    "L1DIFFUSET0LERPT1T2T3VERTEXALPHA": "L1DIFFUSET0XT1T2T3",
    "L2DIFFUSET0LERPT1T2T3VERTEXALPHA": "L2DIFFUSET0XT1T2T3",
    "L1DIFFUSET0LERPT1XT2T3VERTEXALPHA": "L1DIFFUSET0XT1XT2T3",
    "L2DIFFUSET0LERPT1XT2T3VERTEXALPHA": "L2DIFFUSET0XT1XT2T3",
    "L1DIFFUSET0LERPT1T2XT3VERTEXALPHA": "L1DIFFUSET0XT1T2XT3",
    "L2DIFFUSET0LERPT1T2XT3VERTEXALPHA": "L2DIFFUSET0XT1T2XT3",
    "L1DIFFUSET0LERPT1XT2XT3VERTEXALPHA": "L1DIFFUSET0XT1XT2XT3",
    "L2DIFFUSET0LERPT1XT2XT3VERTEXALPHA": "L2DIFFUSET0XT1XT2XT3",
}


# Custom rFactor 1 / Stock Car Extreme (Reiza) shaders that require both a shader
# name remap AND selecting/trimming/appending texture stages to match stock GTR2
# GFX.GTR Pass-0 layouts:
#   shader_upper -> (gtr2_shader, kept_indices_or_None, extra_math_specs)
# where extra_math_specs is a tuple of (stage, flags, unk68) MATH records to append.
RF1_SHADER_STAGE_MAP = {
    # rF1 2-texture vertex-alpha lerp diffuse & bump shaders:
    # - rF1 L0DIFFUSET0LERPT1VERTEXALPHA carries 3 stages (T0, T1, MATH), while
    #   GTR2 L0/L1/L2DiffuseT0xT1 take 2 stages (T0, T1) with diffuse lighting
    #   (GTR2's L2DiffuseT0lerpT1 is unlit skybox-only blend).
    # - GTR2 BUMPMAP.GFX has no L2BumpT0lerpT1; L2BumpT0xT1 is the exact 3-stage
    #   (T0, T1, Bump) match.
    "L0DIFFUSET0LERPT1VERTEXALPHA": ("L0DIFFUSET0XT1", (0, 1), ()),
    "L1DIFFUSET0LERPT1VERTEXALPHA": ("L1DIFFUSET0XT1", (0, 1), ()),
    "L2DIFFUSET0LERPT1VERTEXALPHA": ("L2DIFFUSET0XT1", (0, 1), ()),
    "L1BUMPT0LERPT1VERTEXALPHA": ("L1DIFFUSET0XT1", (0, 1), ()),
    "L2BUMPT0LERPT1VERTEXALPHA": ("L2BUMPT0XT1", (0, 1, 2), ()),
    "L2BUMPSPECT0LERPT1VERTEXALPHA": ("L2BUMPT0XT1", (0, 1, 2), ()),
    # Reiza road shaders -> GTR2 L2BUMPT0XT1 (0=Diffuse, 1=Detail, 2=Bump)
    "SIMPLEROADSHADERV2": ("L2BUMPT0XT1", (0, 1, 3), ()),
    "SIMPLEROADSHADER": ("L2BUMPT0XT1", (0, 1, 3), ()),
    "ADVANCEDNONLINEDROADSHADERV2": ("L2BUMPT0XT1", (0, 1, 4), ()),
    "ADVANCEDROADSHADERV2": ("L2BUMPT0XT1", (0, 1, 4), ()),
    "ADVANCEDROADSHADER": ("L2BUMPT0XT1", (0, 1, 4), ()),
    # Reiza rumble strips -> GTR2 L2BUMPT0 (0=Diffuse, 1=Bump)
    "RZRUMBLESTRIP": ("L2BUMPT0", (0, 2), ()),
    "RUMBLESTRIPSHADER": ("L2BUMPT0", (0, 2), ()),
    # Reiza terrain and grass shaders
    "TERRAINV2MAPPEDVC": ("L2DIFFUSET0", (0,), ()),
    "TERRAINV2": ("L2DIFFUSET0", (0,), ()),
    "GRASSSHADERSHAREDDETAIL": ("L2DIFFUSET0XT1", (0, 1), ()),
    "GRASSSHADERSIMPLE": ("L2DIFFUSET0XT1", (0, 1), ()),
    "RZGRASSSPRITES": ("L2DIFFUSET0", (0,), ()),
    "RZGRASSSPRITES2": ("L2DIFFUSET0", (0,), ()),
    # Reiza tree / billboard / crowd shaders
    "TREESHADER": ("L2DIFFUSET0", (0,), ()),
    "TREESHADERBB": ("L2DIFFUSET0", (0,), ()),
    "RZTREESHADERBBMULTIPLE": ("L2DIFFUSET0", (0,), ()),
    # Reiza diffuse / bump / spec shaders
    "DIFFUSEALPHASPEC": ("L2DIFFUSET0", (0,), ()),
    "DIFFUSEALPHAMASK": ("L2DIFFUSET0", (0,), ()),
    "DIFFUSEBUMPALPHASPEC": ("L2BUMPT0", (0, 1), ()),
    "DIFFUSEAOBUMPALPHASPEC": ("L2BUMPT0XT1", (0, 1, 2), ()),
    "DIFFUSEBUMPSPECR": ("L2BUMPSPECMAPT0", (0, 1, 2), ((3, 0x8000, 1), (4, 0x18, -1))),
    # Reiza water shader -> GTR2 L2CMAPT0 (0=Diffuse, 1=CubeMap)
    "RZTS_WATERADVSHADER": ("L2CMAPT0", (0, 5), ()),
    "RZWATERSHADER": ("L2CMAPT0", (0, 5), ()),
    # Reiza custom cubemap / blend / spec shaders
    "L2BUMPCMAPSPECMAPADDSPECULARREFLECTT0": ("L2BUMPCMAPSPECMAPADDALPHAREFLECTT0", None, ()),
    "L2CMAPADDT0CUSTOM": ("L2CMAPT0", (0, 1), ()),
    "L1CMAPADDALPHAREFLECTT0T1": ("L1CMAPT0T1", (0, 1, 2), ((3, 0x8000, 1),)),
    "L2BUMPSPECMAPT0LERPT1VERTEXALPHA": ("L2BUMPT0XT1", (0, 1, 3), ()),
    "L1T0MULT1LERPT2T3ALPHACUSTOM": ("L1DIFFUSET0T1XT2T3", (0, 1, 2, 3), ()),
    "L2T0MULT1LERPT2T3ALPHACUSTOM": ("L2DIFFUSET0T1XT2T3", (0, 1, 2, 3), ()),
    "L0SPECULARMAPT0": ("L0SPECULART0", (0,), ()),
    # SRPL sky shaders (fallback if ever present outside skyboxi)
    "SRPLDX9SKY02": ("L2DIFFUSET0", (0,), ()),
    "SRPLDX9SUN01": ("L2DIFFUSET0", (0,), ()),
    "SRPLDX9CLOUDS02": ("L2DIFFUSET0", (0,), ()),
    "SRPLDX9MOON01": ("L2DIFFUSET0", (0,), ()),
    "SRPLDX9MOONGLOW01": ("L2DIFFUSET0", (0,), ()),
}

_BUMP_DOWNGRADE_MAP = {
    "L2BUMPT0": ("L2DIFFUSET0", (0,)),
    "L2BUMPT0XT1": ("L2DIFFUSET0XT1", (0, 1)),
    "L2BUMPSPECMAPT0": ("L2DIFFUSET0", (0,)),
    "L2BUMPSPECT0": ("L2DIFFUSET0", (0,)),
}


def _remap_rf1_shader_name(name):
    """Map rF1/SCE-only shader names to stage-compatible stock GTR2 GFX.GTR shaders."""
    up = name.strip().upper()
    if not up:
        return name
    if up in RF1_SHADER_STAGE_MAP:
        return RF1_SHADER_STAGE_MAP[up][0]
    if up in RF1_SHADER_MAP:
        return RF1_SHADER_MAP[up]
    if up.endswith("VERTEXALPHA"):
        base = up[:-len("VERTEXALPHA")]
        if "LERPT1" in base:
            base = base.replace("LERPT1", "XT1", 1)
        return base
    return name


def _rewrite_rf1_materials(mat_bytes, label="GMT", remap_surfaces=True,
                           tex_resolver=None, mat_has_tangents=None):
    """Validate and adapt an rF1/SCE GMT material section for GTR2 v2.10.

    - Maps rF1/SCE-only shaders (``L2USEVERTEXONLY``, ``L*CMAPADDT0*``,
      ``*LERPT1*VERTEXALPHA``, and Reiza GSCE custom shaders like
      ``SIMPLEROADSHADERV2``, ``ADVANCEDNONLINEDROADSHADERV2``,
      ``TERRAINV2MAPPEDVC``, ``TREESHADERBB``, ``RZRUMBLESTRIP``, etc.) to
      stage-compatible GTR2 ``GFX.GTR`` shaders, rebuilding per-level texture
      slices cleanly.
    - Appends GTR2's required ``MATH`` pseudo-texture stages on rF1 bump-spec
      shaders (``L2BUMPSPECMAPT0`` 3 -> 5 stages, ``L2BUMPSPECT0`` 2 -> 4).
    - Remaps rF1-only .tdf surface prefixes to GTR2 specialfx.tec prefixes.
    - Optionally resolves texture filenames (e.g. .tga -> .dds) via ``tex_resolver``.

    Returns ``(new_mat_bytes, uv4_remap_mats)`` where ``uv4_remap_mats`` is the
    set of material indices where a kept texture originally referenced UV channel
    4 (requiring ``uv4 -> uv2`` copy from the rF1 secondary UV stream if present).
    """
    if len(mat_bytes) < 4:
        raise RuntimeError("truncated rF1 GMT materials in %s" % label)
    nmat = struct.unpack_from("<I", mat_bytes, 0)[0]
    pos = 4
    out = bytearray(struct.pack("<I", nmat))
    uv4_remap_mats = set()

    for mi in range(nmat):
        need = (GTR2_MAT_HEADER_SIZE + 3 * GTR2_SHADER_INFO_SIZE
                + GTR2_MAT_POST_SHADERS_PAD + 4)
        if pos + need > len(mat_bytes):
            raise RuntimeError("truncated rF1 GMT material %d in %s" % (mi, label))

        mhdr = bytearray(mat_bytes[pos:pos + GTR2_MAT_HEADER_SIZE])
        mflags = struct.unpack_from("<I", mhdr, 64)[0]
        if mflags & MAT_FLAG_RF1_UNSUPPORTED:
            struct.pack_into("<I", mhdr, 64, mflags & ~MAT_FLAG_RF1_UNSUPPORTED)
        if remap_surfaces:
            mname = bytes(mhdr[:64]).split(b"\x00", 1)[0].decode("ascii", errors="replace")
            mapped = _remap_rf1_surface_name(mname)
            if mapped != mname:
                mhdr[:64] = _pad_ascii(mapped, 64)
        pos += GTR2_MAT_HEADER_SIZE

        levels = []
        for _lv in range(3):
            sb = bytearray(mat_bytes[pos:pos + GTR2_SHADER_INFO_SIZE])
            first_t, tcnt = struct.unpack_from("<2I", sb, 0)
            sh = bytes(sb[8:72]).split(b"\x00", 1)[0].decode("ascii", errors="replace")
            levels.append([first_t, tcnt, sh.strip().upper(), sb])
            pos += GTR2_SHADER_INFO_SIZE

        post_pad = mat_bytes[pos:pos + GTR2_MAT_POST_SHADERS_PAD]
        pos += GTR2_MAT_POST_SHADERS_PAD
        ntex = struct.unpack_from("<I", mat_bytes, pos)[0]
        pos += 4

        tex_records = []
        for ti in range(ntex):
            if pos + 80 > len(mat_bytes):
                raise RuntimeError("truncated rF1 GMT texture %d in %s" % (ti, label))
            sz = tex_record_size(mat_bytes, pos)
            if pos + sz > len(mat_bytes):
                raise RuntimeError("truncated rF1 GMT texture %d payload in %s" % (ti, label))
            tb = bytearray(mat_bytes[pos:pos + sz])
            tfl = struct.unpack_from("<I", tb, 64)[0]
            if tfl & TEX_FLAG_UNIQUE_MAT:
                struct.pack_into("<I", tb, 64, tfl & ~TEX_FLAG_UNIQUE_MAT)
            if tex_resolver is not None:
                tname = bytes(tb[:64]).split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()
                if tname and tname.upper() != "MATH":
                    resolved = tex_resolver(tname)
                    if resolved and resolved != tname:
                        tb[:64] = _pad_ascii(resolved, 64)
            tex_records.append(bytes(tb))
            pos += sz

        # Determine whether any level requires stage-slice rebuilding (Reiza custom
        # shaders or missing MATH stages or missing tangents on bump targets).
        has_tan = True if mat_has_tangents is None else mat_has_tangents.get(mi, True)
        needs_rebuild = False
        for first_t, tcnt, orig_sh, _sb in levels:
            mapped_sh = _remap_rf1_shader_name(orig_sh).upper()
            if orig_sh in RF1_SHADER_STAGE_MAP:
                needs_rebuild = True
            elif mapped_sh == "L2BUMPSPECMAPT0" and tcnt == 3:
                needs_rebuild = True
            elif mapped_sh == "L2BUMPSPECT0" and tcnt == 2:
                needs_rebuild = True
            elif not has_tan and mapped_sh in _BUMP_DOWNGRADE_MAP:
                needs_rebuild = True

        if not needs_rebuild:
            for first_t, tcnt, orig_sh, sb in levels:
                mapped_sh = _remap_rf1_shader_name(orig_sh)
                if mapped_sh.upper() != orig_sh:
                    sb[8:72] = _pad_ascii(mapped_sh, 64)
            out_texs = tex_records
        else:
            out_texs = []
            for lv_i, (first_t, tcnt, orig_sh, sb) in enumerate(levels):
                src_slice = tex_records[first_t:first_t + tcnt] if first_t + tcnt <= len(tex_records) else []
                spec = RF1_SHADER_STAGE_MAP.get(orig_sh)
                kept_src_idxs = None
                if spec is not None:
                    mapped_sh, keep_idxs, extra_math = spec
                    if keep_idxs is None:
                        kept = list(src_slice)
                        kept_src_idxs = list(range(len(src_slice)))
                    else:
                        kept_src_idxs = [k for k in keep_idxs if k < len(src_slice)]
                        kept = [src_slice[k] for k in kept_src_idxs]
                        if not kept and src_slice:
                            kept = [src_slice[0]]
                            kept_src_idxs = [0]
                else:
                    mapped_sh = _remap_rf1_shader_name(orig_sh).upper()
                    kept = list(src_slice)
                    kept_src_idxs = list(range(len(src_slice)))
                    extra_math = ()
                    if mapped_sh == "L2BUMPSPECMAPT0" and len(kept) == 3:
                        extra_math = ((3, 0x8000, 1), (4, 0x18, -1))
                    elif mapped_sh == "L2BUMPSPECT0" and len(kept) == 2:
                        extra_math = ((2, 0x8000, 1), (3, 0x18, -1))

                # Downgrade bump target shaders when the mesh has no tangent stream.
                if not has_tan and mapped_sh in _BUMP_DOWNGRADE_MAP:
                    mapped_sh, dg_idxs = _BUMP_DOWNGRADE_MAP[mapped_sh]
                    kept = [kept[k] for k in dg_idxs if k < len(kept)]
                    kept_src_idxs = [kept_src_idxs[k] for k in dg_idxs if k < len(kept_src_idxs)]
                    extra_math = ()

                # Clamp UV channel indices on kept static textures to GTR2's
                # 4-channel (0..3) TEXCOORD space and re-index dropped stages
                # (e.g. stage 3 bump moved to stage 2 on L2BumpT0xT1); track
                # uv4 -> uv2 remapping for meshes with uv2 stream. Skip animated
                # records (anim_frames > 1) because offset 88 falls inside their
                # frame sequence array.
                fixed_kept = []
                for new_si, raw_tb in enumerate(kept):
                    tb = bytearray(raw_tb)
                    orig_si = kept_src_idxs[new_si] if new_si < len(kept_src_idxs) else new_si
                    if len(tb) >= 92 and struct.unpack_from("<I", tb, 72)[0] <= 1:
                        uv_ch = struct.unpack_from("<I", tb, 88)[0]
                        if uv_ch == 4 and new_si == 2:
                            struct.pack_into("<I", tb, 88, 2)
                            uv4_remap_mats.add(mi)
                        elif uv_ch >= 4 or (orig_si != new_si and uv_ch == orig_si and uv_ch >= len(kept)):
                            struct.pack_into("<I", tb, 88, min(new_si, 3))
                    fixed_kept.append(bytes(tb))

                for m_stage, m_flags, m_unk68 in extra_math:
                    fixed_kept.append(_make_math_tex_record(m_stage, m_flags, m_unk68))

                new_first = len(out_texs)
                out_texs.extend(fixed_kept)
                struct.pack_into("<2I", sb, 0, new_first, len(fixed_kept))
                sb[8:72] = _pad_ascii(mapped_sh, 64)

        out.extend(mhdr)
        for _ft, _tc, _sh_up, sb in levels:
            out.extend(sb)
        out.extend(post_pad)
        out.extend(struct.pack("<I", len(out_texs)))
        for tb in out_texs:
            out.extend(tb)

    if pos != len(mat_bytes):
        raise RuntimeError(
            "unexpected trailing bytes (%d) after rF1 GMT materials in %s"
            % (len(mat_bytes) - pos, label)
        )
    return bytes(out), uv4_remap_mats


def convert_rf1_to_gtr2(data, label="GMT", remap_surfaces=True, tex_resolver=None):
    """Convert raw rFactor 1 GMT v2.00 bytes to GTR2 GMT v2.10 bytes.

    Already-converted GTR2 GMT v2.10 inputs are returned unchanged.
    """
    if is_gtr2_gmt(data):
        return data
    if not is_rf1_gmt(data):
        raise RuntimeError("not a valid rFactor 1 GMT v2.00 file: %s" % label)

    mat_off = struct.unpack_from("<I", data, 0)[0] + 4
    flags = struct.unpack_from("<I", data, 4)[0]
    nmesh, moff_r, nface, foff_r, nnorm, noff_r, bcnt, _boff_r = struct.unpack_from(
        "<8I", data, 0x174
    )
    if bcnt != 0:
        raise RuntimeError("rigged rF1 GMT with %d bones is not supported: %s" % (bcnt, label))
    moff = moff_r + 4
    foff = foff_r + 4
    noff = noff_r + 4
    nmat = struct.unpack_from("<I", data, mat_off)[0] if mat_off + 4 <= len(data) else 0

    mesh_records = []
    mesh_verts = []
    mat_has_tangents = {}
    for mi in range(nmesh):
        desc_base = moff + mi * RF1_MESH_SIZE
        u = struct.unpack_from("<10I", data, desc_base)
        # Clear bit 0x20 (secondary UV4..7 stream) since GTR2 v2.10 only carries
        # the primary 4-channel UV stream at +0x14.
        m_flags = u[0] & ~0x20
        voff = u[1] + 4
        uvoff = u[3] + 4
        uv2off = (u[4] + 4) if u[4] else 0
        tanoff = (u[5] + 4) if u[5] else 0
        vcnt = u[6]
        icnt = u[8]
        trioff = u[9] + 4
        # In rF1's 132-byte mesh descriptor, material index lives at +0x38.
        mat_idx = struct.unpack_from("<I", data, desc_base + 0x38)[0]
        if nmat and mat_idx >= nmat:
            raise RuntimeError(
                "rF1 GMT mesh %d material index %d out of range (%d) in %s"
                % (mi, mat_idx, nmat, label)
            )

        v_end = voff + vcnt * GTR2_VERTEX_SIZE
        uv_end = uvoff + vcnt * GTR2_UV_SIZE
        tri_end = trioff + icnt * 2
        if v_end > mat_off or uv_end > mat_off or tri_end > mat_off:
            raise RuntimeError("rF1 GMT mesh %d geometry out of bounds in %s" % (mi, label))
        verts = data[voff:v_end]
        uvs = data[uvoff:uv_end]
        uv2s = None
        if uv2off and uv2off + vcnt * GTR2_UV_SIZE <= mat_off:
            uv2s = data[uv2off:uv2off + vcnt * GTR2_UV_SIZE]
        if tanoff:
            tan_end = tanoff + vcnt * GTR2_TANGENT_SIZE
            if tan_end > mat_off:
                raise RuntimeError("rF1 GMT mesh %d tangents out of bounds in %s" % (mi, label))
            tans = data[tanoff:tan_end]
        else:
            tans = None
            m_flags &= ~MESH_FLAG_TANGENTS
        if mat_idx not in mat_has_tangents:
            mat_has_tangents[mat_idx] = (tans is not None)
        else:
            mat_has_tangents[mat_idx] = mat_has_tangents[mat_idx] and (tans is not None)
        tris = data[trioff:tri_end]
        mesh_verts.append(verts)
        mesh_records.append([m_flags, mat_idx, vcnt, icnt, verts, uvs, uv2s, tans, tris])

    faces_bytes, norms_bytes, out_nface = _rebuild_rf1_faces_and_normals(
        data, foff, nface, noff, nnorm, mesh_verts
    )
    out_nnorm = out_nface
    mat_bytes, uv4_remap_mats = _rewrite_rf1_materials(
        data[mat_off:], label=label, remap_surfaces=remap_surfaces,
        tex_resolver=tex_resolver, mat_has_tangents=mat_has_tangents,
    )

    # Copy UV channel 4 from rF1's secondary UV stream into channel 2 when a
    # remapped Reiza shader (e.g. ADVANCEDNONLINEDROADSHADERV2) moved its bump
    # map from uv4 -> uv2.
    if uv4_remap_mats:
        for rec in mesh_records:
            if rec[1] in uv4_remap_mats and rec[6] is not None and rec[2] > 0:
                vcnt = rec[2]
                uv_buf = bytearray(rec[5])
                uv2_src = rec[6]
                for vi in range(vcnt):
                    dst_off = vi * GTR2_UV_SIZE + 16  # channel 2 (u2, v2)
                    src_off = vi * GTR2_UV_SIZE       # channel 4 (u4, v4)
                    uv_buf[dst_off:dst_off + 8] = uv2_src[src_off:src_off + 8]
                rec[5] = bytes(uv_buf)

    out = bytearray(GTR2_HEADER_SIZE + nmesh * GTR2_MESH_SIZE)
    face_off = len(out)
    out.extend(faces_bytes)
    norm_off = len(out)
    out.extend(norms_bytes)

    first_vert_off = len(out)
    for mi, (m_flags, mat_idx, vcnt, icnt, verts, uvs, _uv2s, tans, tris) in enumerate(mesh_records):
        v_pos = len(out)
        if mi == 0:
            first_vert_off = v_pos
        out.extend(verts)
        uv_pos = len(out)
        out.extend(uvs)
        if tans is not None:
            tan_pos = len(out)
            out.extend(tans)
            tan_rel = tan_pos - 4
        else:
            tan_rel = 0
        tri_pos = len(out)
        out.extend(tris)

        desc_pos = GTR2_HEADER_SIZE + mi * GTR2_MESH_SIZE
        struct.pack_into(
            "<11I", out, desc_pos,
            m_flags, mat_idx, vcnt, v_pos - 4, 0, uv_pos - 4, 0,
            tan_rel, 0, icnt, tri_pos - 4,
        )
        struct.pack_into("<I", out, desc_pos + 0x5C, 0xFFFFFFFF)

    out_mat_off = len(out)
    out.extend(mat_bytes)

    # Assemble 532-byte GTR2 v2.10 header from rF1 header (+0x10 shift for magic).
    struct.pack_into("<2I", out, 0, out_mat_off - 4, flags)
    out[GTR2_MAGIC_OFFSET:GTR2_MAGIC_OFFSET + len(GTR2_GMT_MAGIC)] = GTR2_GMT_MAGIC
    out[0x018:0x158] = data[0x008:0x148]
    struct.pack_into(
        "<8I", out, 0x16C,
        nmesh, GTR2_HEADER_SIZE - 4,
        out_nface, (face_off - 4) if out_nface else 0,
        out_nnorm, (norm_off - 4) if out_nnorm else 0,
        0, first_vert_off - 4,
    )
    out[0x194:0x1D4] = data[0x194:0x1D4]
    return bytes(out)


def gtr2_gmt_face_count(path):
    """Return FaceData face count of a GTR2 GMT v2.10 file, or None if unreadable."""
    try:
        with open(path, "rb") as f:
            head = f.read(0x18C)
    except OSError:
        return None
    if len(head) < 0x18C or not is_gtr2_gmt(head):
        return None
    return struct.unpack_from("<I", head, 0x174)[0]


def gmt_bbox_xz(path):
    """Return ``(xmin, xmax, zmin, zmax)`` from an rF2 v2.52 or GTR2 v2.10 GMT header."""
    try:
        with open(path, "rb") as f:
            head = f.read(RF2_HEADER_SIZE)
    except OSError:
        return None
    if len(head) >= 0x0E0 and is_rf2_gmt(head):
        bmin = struct.unpack_from("<3f", head, 0x0C8)
        bmax = struct.unpack_from("<3f", head, 0x0D4)
        return (min(bmin[0], bmax[0]), max(bmin[0], bmax[0]),
                min(bmin[2], bmax[2]), max(bmin[2], bmax[2]))
    if len(head) >= 0x07C and is_gtr2_gmt(head):
        corners = struct.unpack_from("<24f", head, 0x01C)
        xs = corners[0::3]
        zs = corners[2::3]
        return (min(xs), max(xs), min(zs), max(zs))
    return None


def convert_file(path, mesh_coarsening=0.0, include_faces=True,
                 drop_flat_tangents=False, track_brightness=0.0,
                 remap_surfaces=True, tex_resolver=None, max_face_span=None):
    """Convert a GMT file on disk (rF2 v2.52 or rF1 v2.00) to GTR2 v2.10 in place."""
    with open(path, "rb") as f:
        original = f.read()
    if is_gtr2_gmt(original):
        return False
    if is_rf1_gmt(original):
        converted = convert_rf1_to_gtr2(
            original, label=path, remap_surfaces=remap_surfaces,
            tex_resolver=tex_resolver,
        )
    else:
        converted = convert_rf2_to_gtr2(
            original, label=path, mesh_coarsening=mesh_coarsening,
            include_faces=include_faces, drop_flat_tangents=drop_flat_tangents,
            track_brightness=track_brightness, max_face_span=max_face_span,
        )
    if converted is not original:
        with open(path, "wb") as f:
            f.write(converted)
        return True
    return False


def convert_assets(root, mesh_coarsening=0.0, collidable_gmts=None,
                   drop_flat_tangents=False, track_brightness=0.0,
                   max_face_span_gmts=None, max_face_span=None):
    """Convert all loose GMT files under a staged GTR2 location to GMT v2.10.

    GTR2 uses unencrypted GMT v2.10 and DDS assets. When ``collidable_gmts``
    is provided (a collection of lowercased GMT basenames), only those GMTs
    keep FaceData/NormalData collision tables; non-collidable visual GMTs are
    converted with ``include_faces=False``. When ``drop_flat_tangents`` is
    True, flat-normal/flat-spec bump cutouts are downgraded to ``L2DIFFUSET0``
    without UVTangents. When ``track_brightness`` > 0.0, material diffuse and
    ambient RGB levels are lifted smoothly toward 1.0. When
    ``max_face_span_gmts`` and ``max_face_span`` are given, matching GMTs drop
    oversized collision faces exceeding ``max_face_span`` meters in X or Z.
    """
    collidable = (
        {name.lower() for name in collidable_gmts}
        if collidable_gmts is not None else None
    )
    capped = (
        {name.lower() for name in max_face_span_gmts}
        if max_face_span_gmts is not None else None
    )
    files = []
    for folder, _, names in os.walk(root):
        for name in names:
            if os.path.splitext(name)[1].lower() == ".gmt":
                files.append(os.path.join(folder, name))
    files.sort()

    for path in files:
        low = os.path.basename(path).lower()
        include_faces = True if collidable is None else (low in collidable)
        face_span = (
            max_face_span
            if (capped is not None and max_face_span and low in capped)
            else None
        )
        convert_file(
            path,
            mesh_coarsening=mesh_coarsening,
            include_faces=include_faces,
            drop_flat_tangents=drop_flat_tangents,
            track_brightness=track_brightness,
            max_face_span=face_span,
        )
        with open(path, "rb") as f:
            head = f.read(GTR2_MAGIC_OFFSET + len(GTR2_GMT_MAGIC))
        if not is_gtr2_gmt(head):
            raise RuntimeError("GTR2 GMT v2.10 verification failed for %s" % path)
    return len(files)
