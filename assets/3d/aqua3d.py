"""aqua3d - make 3D models with plain Python (standard library only) and export them as GLB, OBJ or STL.

    from aqua3d import Scene
    s = Scene()
    s.box(size=(4, 2, 3), pos=(0, 1, 0), color="#d9c7a3", name="walls")
    s.cone(r=3, h=1.5, pos=(0, 2.75, 0), color="#a33", seg=4, smooth=False, rot=(0, 45, 0))
    s.save("../models/house.glb")          # .glb  .obj (+ .mtl)  .stl  by file extension

Y is up. Units are free (1 unit = 1 metre is a good habit). Every shape takes
    pos=(x, y, z)  rot=(rx, ry, rz) in degrees  scale=1 or (sx, sy, sz)  color="#rrggbb"  metal=0..1  rough=0..1  name="..."
Shapes: box, sphere, cylinder (r2= makes a cone/frustum), cone, torus, plane, extrude, lathe, mesh.
Compose with transforms:   with s.group(pos=(5, 0, 0), rot=(0, 30, 0)): s.box(...); s.sphere(...)
Low-poly look: smooth=False.   High quality: more seg=...
"""
import json
import math
import os
import struct

# ----------------------------------------------------------------------------- small maths
def _hex(c):
    if isinstance(c, (tuple, list)):
        return tuple(max(0.0, min(1.0, float(x))) for x in c[:3])
    c = str(c).strip().lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    try:
        return tuple(int(c[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return (0.7, 0.7, 0.7)


def _mm(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _mv(m, v):
    return (m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
            m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
            m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2])


def _rot(rx, ry, rz):
    rx, ry, rz = math.radians(rx), math.radians(ry), math.radians(rz)
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    mx = [[1, 0, 0], [0, cx, -sx], [0, sx, cx]]
    my = [[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]
    mz = [[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]]
    return _mm(mz, _mm(my, mx))


def _inv_t(m):
    """inverse transpose of a 3x3 (for normals under non-uniform scale)"""
    a, b, c = m[0]
    d, e, f = m[1]
    g, h, i = m[2]
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-12:
        return m
    inv = [[(e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det],
           [(f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det],
           [(d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det]]
    return [[inv[j][k] for j in range(3)] for k in range(3)]


def _norm(v):
    l = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return (v[0] / l, v[1] / l, v[2] / l) if l > 1e-12 else (0.0, 1.0, 0.0)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _tri_normal(p0, p1, p2):
    return _norm(_cross(_sub(p1, p0), _sub(p2, p0)))


def _tuple3(v, default):
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return (float(v),) * 3
    return (float(v[0]), float(v[1]), float(v[2]))


def _flatten(verts, tris):
    """un-index: every triangle gets its own vertices and a face normal (flat / low-poly shading)"""
    v2, n2, t2 = [], [], []
    for (a, b, c) in tris:
        p0, p1, p2 = verts[a], verts[b], verts[c]
        n = _tri_normal(p0, p1, p2)
        base = len(v2)
        v2.extend([p0, p1, p2])
        n2.extend([n, n, n])
        t2.append((base, base + 1, base + 2))
    return v2, n2, t2


def _smooth_normals(verts, tris):
    acc = [[0.0, 0.0, 0.0] for _ in verts]
    for (a, b, c) in tris:
        n = _cross(_sub(verts[b], verts[a]), _sub(verts[c], verts[a]))
        for i in (a, b, c):
            acc[i][0] += n[0]
            acc[i][1] += n[1]
            acc[i][2] += n[2]
    return [_norm(tuple(x)) for x in acc]


def _orient(p0, p1, p2, want):
    """return the triangle so that its normal points towards 'want'"""
    n = _cross(_sub(p1, p0), _sub(p2, p0))
    return (p0, p1, p2) if (n[0] * want[0] + n[1] * want[1] + n[2] * want[2]) >= 0 else (p0, p2, p1)


def _triangulate(poly):
    """ear clipping for a simple polygon [(a, b), ...]; returns index triples"""
    n = len(poly)
    idx = list(range(n))
    area = sum(poly[i][0] * poly[(i + 1) % n][1] - poly[(i + 1) % n][0] * poly[i][1] for i in range(n))
    if area < 0:
        idx.reverse()

    def cr(a, b, c):
        return (poly[b][0] - poly[a][0]) * (poly[c][1] - poly[a][1]) - (poly[b][1] - poly[a][1]) * (poly[c][0] - poly[a][0])

    def inside(p, a, b, c):
        pa, pb, pc = poly[a], poly[b], poly[c]
        pp = poly[p]
        s1 = (pb[0] - pa[0]) * (pp[1] - pa[1]) - (pb[1] - pa[1]) * (pp[0] - pa[0])
        s2 = (pc[0] - pb[0]) * (pp[1] - pb[1]) - (pc[1] - pb[1]) * (pp[0] - pb[0])
        s3 = (pa[0] - pc[0]) * (pp[1] - pc[1]) - (pa[1] - pc[1]) * (pp[0] - pc[0])
        return s1 >= -1e-12 and s2 >= -1e-12 and s3 >= -1e-12

    tris, guard = [], 0
    while len(idx) > 3 and guard < 20000:
        guard += 1
        cut = False
        for k in range(len(idx)):
            a, b, c = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            if cr(a, b, c) <= 1e-12:
                continue
            if any(inside(p, a, b, c) for p in idx if p not in (a, b, c)):
                continue
            tris.append((a, b, c))
            idx.pop(k)
            cut = True
            break
        if not cut:
            break
    if len(idx) == 3:
        tris.append(tuple(idx))
    return tris


# ----------------------------------------------------------------------------- the scene
class Scene:
    def __init__(self, name="model"):
        self.name = name
        self.prims = []
        self._stack = [([[1, 0, 0], [0, 1, 0], [0, 0, 1]], (0.0, 0.0, 0.0))]

    # -- transforms -------------------------------------------------------------
    def group(self, pos=(0, 0, 0), rot=(0, 0, 0), scale=1):
        scene = self

        class _G:
            def __enter__(self_):
                scene.push(pos, rot, scale)
                return scene

            def __exit__(self_, *a):
                scene.pop()
        return _G()

    def push(self, pos=(0, 0, 0), rot=(0, 0, 0), scale=1):
        pm, pt = self._stack[-1]
        s = _tuple3(scale, (1, 1, 1))
        r = _rot(*_tuple3(rot, (0, 0, 0)))
        local = _mm(r, [[s[0], 0, 0], [0, s[1], 0], [0, 0, s[2]]])
        t = _tuple3(pos, (0, 0, 0))
        wt = _mv(pm, t)
        self._stack.append((_mm(pm, local), (wt[0] + pt[0], wt[1] + pt[1], wt[2] + pt[2])))

    def pop(self):
        if len(self._stack) > 1:
            self._stack.pop()

    # -- low level --------------------------------------------------------------
    def _add(self, verts, tris, normals, color, metal, rough, name, pos, rot, scale, smooth):
        if not smooth or normals is None:
            verts, normals, tris = _flatten(verts, tris)
        s = _tuple3(scale, (1, 1, 1))
        local = _mm(_rot(*_tuple3(rot, (0, 0, 0))), [[s[0], 0, 0], [0, s[1], 0], [0, 0, s[2]]])
        lt = _tuple3(pos, (0, 0, 0))
        pm, pt = self._stack[-1]
        M = _mm(pm, local)
        T = _mv(pm, lt)
        T = (T[0] + pt[0], T[1] + pt[1], T[2] + pt[2])
        N = _inv_t(M)
        wv = []
        for v in verts:
            x = _mv(M, v)
            wv.append((x[0] + T[0], x[1] + T[1], x[2] + T[2]))
        wn = [_norm(_mv(N, n)) for n in normals]
        # a mirroring transform flips the winding: fix it so faces still point out
        det = (M[0][0] * (M[1][1] * M[2][2] - M[1][2] * M[2][1]) - M[0][1] * (M[1][0] * M[2][2] - M[1][2] * M[2][0]) + M[0][2] * (M[1][0] * M[2][1] - M[1][1] * M[2][0]))
        if det < 0:
            tris = [(a, c, b) for (a, b, c) in tris]
        self.prims.append({"v": wv, "n": wn, "t": list(tris), "color": _hex(color), "metal": float(metal), "rough": float(rough),
                           "name": name or ("part%d" % (len(self.prims) + 1))})
        return self

    def mesh(self, vertices, faces, color="#cccccc", metal=0.0, rough=0.6, name=None, pos=(0, 0, 0), rot=(0, 0, 0), scale=1, smooth=False):
        """your own triangles/quads: vertices=[(x,y,z),...], faces=[(i,j,k) or (i,j,k,l),...] (counter-clockwise from outside)"""
        tris = []
        for f in faces:
            for k in range(1, len(f) - 1):
                tris.append((f[0], f[k], f[k + 1]))
        verts = [tuple(float(c) for c in v) for v in vertices]
        return self._add(verts, tris, _smooth_normals(verts, tris) if smooth else None, color, metal, rough, name, pos, rot, scale, smooth)

    # -- shapes -----------------------------------------------------------------
    def box(self, size=(1, 1, 1), color="#cccccc", metal=0.0, rough=0.6, name=None, pos=(0, 0, 0), rot=(0, 0, 0), scale=1, smooth=False):
        sx, sy, sz = [c / 2.0 for c in _tuple3(size, (1, 1, 1))]
        v = [(-sx, -sy, -sz), (sx, -sy, -sz), (sx, sy, -sz), (-sx, sy, -sz), (-sx, -sy, sz), (sx, -sy, sz), (sx, sy, sz), (-sx, sy, sz)]
        quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 4, 7, 3), (1, 2, 6, 5), (3, 7, 6, 2), (0, 1, 5, 4)]
        tris = []
        for q in quads:
            tris.append((q[0], q[1], q[2]))
            tris.append((q[0], q[2], q[3]))
        c = (0.0, 0.0, 0.0)
        tris = [_orient_idx(v, t, c) for t in tris]
        return self._add(v, tris, _smooth_normals(v, tris), color, metal, rough, name, pos, rot, scale, smooth)

    def sphere(self, r=0.5, seg=24, rings=None, color="#cccccc", metal=0.0, rough=0.6, name=None, pos=(0, 0, 0), rot=(0, 0, 0), scale=1, smooth=True):
        rings = rings or max(3, seg // 2)
        v, n, t = [], [], []
        for i in range(rings + 1):
            ph = math.pi * i / rings
            for j in range(seg + 1):
                th = 2 * math.pi * j / seg
                nx, ny, nz = math.sin(ph) * math.cos(th), math.cos(ph), math.sin(ph) * math.sin(th)
                v.append((r * nx, r * ny, r * nz))
                n.append((nx, ny, nz))
        for i in range(rings):
            for j in range(seg):
                a = i * (seg + 1) + j
                b = a + seg + 1
                if i != 0:
                    t.append((a, b, a + 1))
                if i != rings - 1:
                    t.append((a + 1, b, b + 1))
        t = [_orient_idx(v, tr, (0, 0, 0)) for tr in t]
        return self._add(v, t, n, color, metal, rough, name, pos, rot, scale, smooth)

    def cylinder(self, r=0.5, h=1.0, r2=None, seg=24, caps=True, color="#cccccc", metal=0.0, rough=0.6, name=None, pos=(0, 0, 0), rot=(0, 0, 0), scale=1, smooth=True):
        """axis along Y, centred. r = bottom radius, r2 = top radius (default = r; 0 = cone)"""
        r2 = r if r2 is None else r2
        v, n, t = [], [], []
        slope = (r - r2) / h if h else 0.0
        for j in range(seg + 1):
            th = 2 * math.pi * j / seg
            c, s_ = math.cos(th), math.sin(th)
            v.append((r * c, -h / 2, r * s_))
            n.append(_norm((c, slope, s_)))
            v.append((r2 * c, h / 2, r2 * s_))
            n.append(_norm((c, slope, s_)))
        for j in range(seg):
            a, b, c2, d = 2 * j, 2 * j + 1, 2 * j + 2, 2 * j + 3
            t.append((a, c2, b))
            t.append((b, c2, d))
        t = [_orient_out(v, tr) for tr in t]
        if caps:
            for (rad, y, ny) in ((r, -h / 2, -1.0), (r2, h / 2, 1.0)):
                if rad <= 1e-9:
                    continue
                base = len(v)
                v.append((0, y, 0))
                n.append((0, ny, 0))
                for j in range(seg + 1):
                    th = 2 * math.pi * j / seg
                    v.append((rad * math.cos(th), y, rad * math.sin(th)))
                    n.append((0, ny, 0))
                for j in range(seg):
                    t.append(_fix(v, base, base + 1 + j, base + 2 + j, (0, ny, 0)))
        return self._add(v, t, n, color, metal, rough, name, pos, rot, scale, smooth)

    def cone(self, r=0.5, h=1.0, seg=24, **kw):
        return self.cylinder(r=r, h=h, r2=0.0, seg=seg, **kw)

    def torus(self, R=0.5, r=0.15, seg=32, tube=16, color="#cccccc", metal=0.0, rough=0.6, name=None, pos=(0, 0, 0), rot=(0, 0, 0), scale=1, smooth=True):
        v, n, t = [], [], []
        for i in range(seg + 1):
            u = 2 * math.pi * i / seg
            for j in range(tube + 1):
                w = 2 * math.pi * j / tube
                cx, cz = math.cos(u), math.sin(u)
                x = (R + r * math.cos(w)) * cx
                y = r * math.sin(w)
                z = (R + r * math.cos(w)) * cz
                v.append((x, y, z))
                n.append(_norm((math.cos(w) * cx, math.sin(w), math.cos(w) * cz)))
        for i in range(seg):
            for j in range(tube):
                a = i * (tube + 1) + j
                b = a + tube + 1
                t.append((a, a + 1, b))
                t.append((a + 1, b + 1, b))
        t = [_fix_by_normals(v, n, tr) for tr in t]
        return self._add(v, t, n, color, metal, rough, name, pos, rot, scale, smooth)

    def plane(self, w=1.0, d=1.0, color="#cccccc", metal=0.0, rough=0.6, name=None, pos=(0, 0, 0), rot=(0, 0, 0), scale=1):
        """flat rectangle on the ground (XZ), facing up"""
        x, z = w / 2.0, d / 2.0
        v = [(-x, 0, -z), (x, 0, -z), (x, 0, z), (-x, 0, z)]
        n = [(0, 1, 0)] * 4
        t = [_orient_idx(v, (0, 1, 2), None, (0, 1, 0)), _orient_idx(v, (0, 2, 3), None, (0, 1, 0))]
        return self._add(v, t, n, color, metal, rough, name, pos, rot, scale, True)

    def extrude(self, points, depth=1.0, plane="xz", color="#cccccc", metal=0.0, rough=0.6, name=None, pos=(0, 0, 0), rot=(0, 0, 0), scale=1, smooth=False):
        """a 2D outline made solid.  plane="xz": outline seen from above, extruded UP from y=0 to y=depth (floor plans, walls).
        plane="xy": outline seen from the front, extruded along Z (gable roofs, profiles), centred on z=0."""
        pts = [(float(a), float(b)) for a, b in points]
        if pts[0] == pts[-1]:
            pts = pts[:-1]
        n_ = len(pts)
        if plane == "xz":
            def at(i, level):
                return (pts[i][0], level * depth, pts[i][1])
            up = (0, 1, 0)
        else:
            def at(i, level):
                return (pts[i][0], pts[i][1], (level - 0.5) * depth)
            up = (0, 0, 1)
        verts, tris = [], []
        for lv in (0, 1):
            for i in range(n_):
                verts.append(at(i, lv))
        cap = _triangulate(pts)
        for (a, b, c) in cap:
            tris.append(_idx3(verts, a, b, c, (-up[0], -up[1], -up[2])))
            tris.append(_idx3(verts, a + n_, b + n_, c + n_, up))
        area = sum(pts[i][0] * pts[(i + 1) % n_][1] - pts[(i + 1) % n_][0] * pts[i][1] for i in range(n_))
        sgn = 1.0 if area >= 0 else -1.0
        for i in range(n_):
            j = (i + 1) % n_
            da, db = pts[j][0] - pts[i][0], pts[j][1] - pts[i][1]
            na, nb = db * sgn, -da * sgn                      # outward normal of this edge (works for concave outlines too)
            out = (na, 0.0, nb) if plane == "xz" else (na, nb, 0.0)
            a_, b_, c_, d_ = i, j, j + n_, i + n_
            tris.append(_idx3(verts, a_, b_, c_, out))
            tris.append(_idx3(verts, a_, c_, d_, out))
        return self._add(verts, tris, None, color, metal, rough, name, pos, rot, scale, False)

    def lathe(self, profile, seg=32, color="#cccccc", metal=0.0, rough=0.6, name=None, pos=(0, 0, 0), rot=(0, 0, 0), scale=1, smooth=True):
        """spin a side profile [(radius, y), ...] (bottom to top) around the Y axis: vases, bottles, columns, bowls, chess pieces"""
        prof = [(float(a), float(b)) for a, b in profile]
        v, t = [], []
        for (rad, y) in prof:
            for j in range(seg + 1):
                th = 2 * math.pi * j / seg
                v.append((rad * math.cos(th), y, rad * math.sin(th)))
        row = seg + 1
        for i in range(len(prof) - 1):
            for j in range(seg):
                a = i * row + j
                b = a + row
                t.append((a, b, a + 1))
                t.append((a + 1, b, b + 1))
        axis_y = sum(p[1] for p in prof) / len(prof)
        t = [_orient_out_axis(v, tr, axis_y) for tr in t]
        n = _smooth_normals(v, t)
        return self._add(v, t, n, color, metal, rough, name, pos, rot, scale, smooth)

    # -- info and export ----------------------------------------------------------
    def stats(self):
        mn = [1e18] * 3
        mx = [-1e18] * 3
        tri = vert = 0
        for p in self.prims:
            tri += len(p["t"])
            vert += len(p["v"])
            for x in p["v"]:
                for k in range(3):
                    mn[k] = min(mn[k], x[k])
                    mx[k] = max(mx[k], x[k])
        if not self.prims:
            mn, mx = [0, 0, 0], [0, 0, 0]
        return {"parts": len(self.prims), "triangles": tri, "vertices": vert, "min": mn, "max": mx, "size": [mx[k] - mn[k] for k in range(3)]}

    def report(self):
        s = self.stats()
        print("parts=%d triangles=%d size=%.2f x %.2f x %.2f" % (s["parts"], s["triangles"], *s["size"]))
        return s

    def save(self, path):
        if not self.prims:
            raise ValueError("The scene is empty: add shapes before saving.")
        ext = os.path.splitext(path)[1].lower()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        if ext == ".glb":
            _write_glb(self, path)
        elif ext == ".obj":
            _write_obj(self, path)
        elif ext == ".stl":
            _write_stl(self, path)
        else:
            raise ValueError("Use .glb, .obj or .stl")
        _update_manifest(path, self.stats())
        st = self.stats()
        print("saved %s  (%d parts, %d triangles, %.2f x %.2f x %.2f)" % (path, st["parts"], st["triangles"], *st["size"]))
        return path


# ----------------------------------------------------------------------------- winding helpers
def _orient_idx(verts, tri, centre, want=None):
    a, b, c = tri
    p0, p1, p2 = verts[a], verts[b], verts[c]
    n = _cross(_sub(p1, p0), _sub(p2, p0))
    if want is None:
        mid = ((p0[0] + p1[0] + p2[0]) / 3, (p0[1] + p1[1] + p2[1]) / 3, (p0[2] + p1[2] + p2[2]) / 3)
        want = _sub(mid, centre)
    return tri if (n[0] * want[0] + n[1] * want[1] + n[2] * want[2]) >= 0 else (a, c, b)


def _idx3(verts, a, b, c, want):
    return _orient_idx(verts, (a, b, c), None, want)


def _orient_out(verts, tri):
    """side wall of a Y-axis cylinder: normal must point away from the axis"""
    a, b, c = tri
    mid = tuple((verts[a][k] + verts[b][k] + verts[c][k]) / 3 for k in range(3))
    return _orient_idx(verts, tri, None, (mid[0], 0.0, mid[2]))


def _orient_out_axis(verts, tri, axis_y):
    a, b, c = tri
    mid = tuple((verts[a][k] + verts[b][k] + verts[c][k]) / 3 for k in range(3))
    want = (mid[0], 0.0, mid[2])
    if abs(want[0]) + abs(want[2]) < 1e-9:
        want = (0.0, 1.0 if mid[1] >= axis_y else -1.0, 0.0)
    return _orient_idx(verts, tri, None, want)


def _fix(verts, a, b, c, want):
    return _orient_idx(verts, (a, b, c), None, want)


def _fix_by_normals(verts, normals, tri):
    a, b, c = tri
    want = tuple(normals[a][k] + normals[b][k] + normals[c][k] for k in range(3))
    return _orient_idx(verts, tri, None, want)


# ----------------------------------------------------------------------------- exporters
def _write_glb(scene, path):
    bin_parts, views, accessors, prims_json, mats = [], [], [], [], []
    offset = 0

    def add_view(data, target):
        nonlocal offset
        pad = (-len(data)) % 4
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(data), "target": target})
        bin_parts.append(data + b"\x00" * pad)
        offset += len(data) + pad
        return len(views) - 1

    for pr in scene.prims:
        pos = b"".join(struct.pack("<3f", *v) for v in pr["v"])
        nor = b"".join(struct.pack("<3f", *n) for n in pr["n"])
        idx = b"".join(struct.pack("<3I", *t) for t in pr["t"])
        mn = [min(v[k] for v in pr["v"]) for k in range(3)]
        mx = [max(v[k] for v in pr["v"]) for k in range(3)]
        a_pos = len(accessors)
        accessors.append({"bufferView": add_view(pos, 34962), "componentType": 5126, "count": len(pr["v"]), "type": "VEC3", "min": mn, "max": mx})
        a_nor = len(accessors)
        accessors.append({"bufferView": add_view(nor, 34962), "componentType": 5126, "count": len(pr["n"]), "type": "VEC3"})
        a_idx = len(accessors)
        accessors.append({"bufferView": add_view(idx, 34963), "componentType": 5125, "count": len(pr["t"]) * 3, "type": "SCALAR"})
        mats.append({"name": pr["name"], "pbrMetallicRoughness": {"baseColorFactor": [pr["color"][0], pr["color"][1], pr["color"][2], 1.0],
                                                                  "metallicFactor": pr["metal"], "roughnessFactor": pr["rough"]}, "doubleSided": True})
        prims_json.append({"attributes": {"POSITION": a_pos, "NORMAL": a_nor}, "indices": a_idx, "material": len(mats) - 1, "mode": 4})
    doc = {"asset": {"version": "2.0", "generator": "aqua3d"}, "scene": 0, "scenes": [{"nodes": [0]}],
           "nodes": [{"mesh": 0, "name": scene.name}], "meshes": [{"name": scene.name, "primitives": prims_json}],
           "materials": mats, "accessors": accessors, "bufferViews": views, "buffers": [{"byteLength": offset}]}
    js = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    js += b" " * ((-len(js)) % 4)
    binb = b"".join(bin_parts)
    total = 12 + 8 + len(js) + 8 + len(binb)
    with open(path, "wb") as f:
        f.write(struct.pack("<4sII", b"glTF", 2, total))
        f.write(struct.pack("<I4s", len(js), b"JSON") + js)
        f.write(struct.pack("<I4s", len(binb), b"BIN\x00") + binb)


def _write_obj(scene, path):
    base = os.path.splitext(path)[0]
    mtl = os.path.basename(base) + ".mtl"
    with open(base + ".mtl", "w") as m:
        for i, p in enumerate(scene.prims):
            m.write("newmtl m%d\nKd %.4f %.4f %.4f\nNs 30\n\n" % (i, *p["color"]))
    with open(path, "w") as f:
        f.write("# aqua3d\nmtllib %s\n" % mtl)
        vo = 1
        for i, p in enumerate(scene.prims):
            f.write("o %s\nusemtl m%d\n" % (p["name"], i))
            for v in p["v"]:
                f.write("v %.6f %.6f %.6f\n" % v)
            for n in p["n"]:
                f.write("vn %.5f %.5f %.5f\n" % n)
            for (a, b, c) in p["t"]:
                f.write("f %d//%d %d//%d %d//%d\n" % (a + vo, a + vo, b + vo, b + vo, c + vo, c + vo))
            vo += len(p["v"])


def _write_stl(scene, path):
    tris = [(p["v"][a], p["v"][b], p["v"][c]) for p in scene.prims for (a, b, c) in p["t"]]
    with open(path, "wb") as f:
        f.write(b"aqua3d binary stl".ljust(80, b"\x00"))
        f.write(struct.pack("<I", len(tris)))
        for (p0, p1, p2) in tris:
            f.write(struct.pack("<12fH", *_tri_normal(p0, p1, p2), *p0, *p1, *p2, 0))


def _update_manifest(path, stats):
    """models/models.json lists every model so the viewer can offer them in a menu"""
    folder = os.path.dirname(os.path.abspath(path))
    mp = os.path.join(folder, "models.json")
    try:
        data = json.load(open(mp, encoding="utf-8"))
    except Exception:
        data = []
    name = os.path.basename(path)
    data = [d for d in data if d.get("file") != name]
    data.append({"file": name, "triangles": stats["triangles"]})
    json.dump(data, open(mp, "w", encoding="utf-8"), indent=1)
