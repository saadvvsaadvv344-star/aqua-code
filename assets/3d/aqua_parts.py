"""Ready-made parts so big scenes take minutes, not hours.  All sit on y = 0 and are about real-world size (metres).

    import sys; sys.path.insert(0, '3d')
    from aqua3d import Scene
    from aqua_parts import tree, pine, rock, house, tower, fence, lamp_post, car, table, chair, bench, bridge, windmill
    s = Scene("village")
    for (x, y, z) in Scene.grid(4, 4, 12, 12):
        house(s, pos=(x, 0, z), rot=(0, 90, 0))
    tree(s, pos=(3, 0, 3), scale=1.4)

Every function: part(s, pos=(0,0,0), rot=(0,0,0), scale=1, **options).  Change colours with the options.
"""
import math


def _g(s, pos, rot, scale):
    return s.group(pos=pos, rot=rot, scale=scale)


def tree(s, pos=(0, 0, 0), rot=(0, 0, 0), scale=1, trunk="#6b4a2b", leaves="#3f8f3f"):
    with _g(s, pos, rot, scale):
        s.cylinder(r=0.2, h=2.0, r2=0.15, pos=(0, 1.0, 0), color=trunk, seg=10, name="trunk")
        s.sphere(r=1.2, pos=(0, 2.8, 0), color=leaves, seg=10, smooth=False, name="leaves")
        s.sphere(r=0.8, pos=(0.6, 3.5, 0.2), color=leaves, seg=10, smooth=False, name="leaves top")


def pine(s, pos=(0, 0, 0), rot=(0, 0, 0), scale=1, trunk="#5a3d22", leaves="#2f6f3a"):
    with _g(s, pos, rot, scale):
        s.cylinder(r=0.18, h=1.0, pos=(0, 0.5, 0), color=trunk, seg=8, name="trunk")
        for i, (r, h, y) in enumerate(((1.3, 1.6, 1.3), (1.0, 1.5, 2.3), (0.7, 1.4, 3.2))):
            s.cone(r=r, h=h, pos=(0, y + h / 2 - 0.2, 0), color=leaves, seg=10, smooth=False, name="needles%d" % i)


def rock(s, pos=(0, 0, 0), rot=(0, 0, 0), scale=1, color="#8a8f98"):
    with _g(s, pos, rot, scale):
        s.sphere(r=0.6, pos=(0, 0.3, 0), scale=(1.2, 0.6, 0.9), color=color, seg=8, smooth=False, rough=0.95, name="rock")
        s.sphere(r=0.3, pos=(0.5, 0.15, 0.3), scale=(1, 0.6, 1), color=color, seg=7, smooth=False, rough=0.95, name="rock small")


def house(s, pos=(0, 0, 0), rot=(0, 0, 0), scale=1, wall="#e8d9b8", roof="#9b3d2e", wood="#6b4a2b", glass="#8fc4e8"):
    """6 x 5 m footprint, door and windows on the +z side"""
    with _g(s, pos, rot, scale):
        s.box(size=(6, 3, 5), pos=(0, 1.5, 0), color=wall, name="walls")
        s.extrude([(-3.4, 0), (3.4, 0), (0, 2.2)], depth=5.6, plane="xy", pos=(0, 3, 0), color=roof, name="roof")
        s.box(size=(0.7, 1.4, 0.7), pos=(1.9, 4.1, -1), color="#8a8f98", name="chimney")
        s.box(size=(1.0, 2.0, 0.15), pos=(0, 1.0, 2.55), color=wood, name="door")
        for x in (-2, 2):
            s.box(size=(1.0, 1.0, 0.12), pos=(x, 1.7, 2.53), color=glass, rough=0.1, name="window")
            s.box(size=(1.2, 0.12, 0.2), pos=(x, 1.1, 2.57), color=wood, name="sill")


def tower(s, pos=(0, 0, 0), rot=(0, 0, 0), scale=1, stone="#9aa0aa", roof="#b0413a", height=8.0, radius=2.0):
    with _g(s, pos, rot, scale):
        s.cylinder(r=radius, h=height, r2=radius * 0.92, pos=(0, height / 2, 0), color=stone, seg=20, name="tower body")
        s.cylinder(r=radius * 1.12, h=0.5, pos=(0, height + 0.25, 0), color=stone, seg=20, name="tower rim")
        for i in range(10):
            a = 2 * math.pi * i / 10
            s.box(size=(0.5, 0.6, 0.5), pos=((radius * 1.02) * math.sin(a), height + 0.8, (radius * 1.02) * math.cos(a)), rot=(0, math.degrees(a), 0), color=stone, name="merlon")
        s.cone(r=radius * 1.2, h=radius * 1.6, pos=(0, height + 0.5 + radius * 0.8, 0), color=roof, seg=20, name="tower roof")
        s.box(size=(0.35, 0.9, 0.2), pos=(0, height * 0.7, radius * 0.93), color="#2b2f36", name="slit")


def fence(s, pos=(0, 0, 0), rot=(0, 0, 0), scale=1, length=6.0, color="#f2f2f2"):
    """runs along x from -length/2 to length/2"""
    with _g(s, pos, rot, scale):
        n = max(2, int(length / 1.2) + 1)
        for i in range(n):
            s.box(size=(0.12, 1.0, 0.12), pos=(-length / 2 + i * length / (n - 1), 0.5, 0), color=color, name="post")
        for y in (0.35, 0.8):
            s.box(size=(length, 0.08, 0.06), pos=(0, y, 0), color=color, name="rail")


def lamp_post(s, pos=(0, 0, 0), rot=(0, 0, 0), scale=1, color="#2b2f36", light="#ffe9a8"):
    with _g(s, pos, rot, scale):
        s.cylinder(r=0.08, h=4.0, pos=(0, 2.0, 0), color=color, metal=0.6, seg=10, name="pole")
        s.cylinder(r=0.2, h=0.2, pos=(0, 0.1, 0), color=color, seg=10, name="base")
        s.sphere(r=0.25, pos=(0, 4.2, 0), color=light, rough=0.2, seg=12, name="lamp")


def car(s, pos=(0, 0, 0), rot=(0, 0, 0), scale=1, body="#c0392b", glass="#a9d6f5"):
    """4.2 m long along x, 1.8 m wide"""
    with _g(s, pos, rot, scale):
        s.box(size=(4.2, 0.7, 1.8), pos=(0, 0.65, 0), color=body, metal=0.5, rough=0.35, name="body")
        s.box(size=(2.2, 0.6, 1.6), pos=(-0.2, 1.25, 0), color=glass, rough=0.1, name="cabin")
        for x in (-1.3, 1.3):
            for z in (-0.95, 0.95):
                s.cylinder(r=0.35, h=0.25, pos=(x, 0.35, z), rot=(90, 0, 0), color="#1b1b1f", seg=16, name="wheel")
        for z in (-0.6, 0.6):
            s.box(size=(0.08, 0.18, 0.3), pos=(2.12, 0.7, z), color="#fff3b0", name="headlight")


def table(s, pos=(0, 0, 0), rot=(0, 0, 0), scale=1, wood="#8a5a33", w=1.6, d=0.9):
    with _g(s, pos, rot, scale):
        s.box(size=(w, 0.06, d), pos=(0, 0.75, 0), color=wood, name="top")
        for x in (-w / 2 + 0.06, w / 2 - 0.06):
            for z in (-d / 2 + 0.06, d / 2 - 0.06):
                s.box(size=(0.08, 0.72, 0.08), pos=(x, 0.36, z), color=wood, name="leg")


def chair(s, pos=(0, 0, 0), rot=(0, 0, 0), scale=1, wood="#7a4b2a", seat="#c9b79c"):
    """faces +z"""
    with _g(s, pos, rot, scale):
        s.box(size=(0.45, 0.05, 0.45), pos=(0, 0.45, 0), color=seat, name="seat")
        for x in (-0.2, 0.2):
            for z in (-0.2, 0.2):
                s.box(size=(0.05, 0.43, 0.05), pos=(x, 0.215, z), color=wood, name="leg")
        s.box(size=(0.45, 0.45, 0.05), pos=(0, 0.72, -0.21), color=wood, name="back")


def bench(s, pos=(0, 0, 0), rot=(0, 0, 0), scale=1, wood="#8a5a33", metal="#2b2f36"):
    with _g(s, pos, rot, scale):
        for z in (-0.15, 0.0, 0.15):
            s.box(size=(1.5, 0.04, 0.1), pos=(0, 0.45, z), color=wood, name="slat")
        s.box(size=(1.5, 0.3, 0.04), pos=(0, 0.7, -0.22), color=wood, name="back")
        for x in (-0.65, 0.65):
            s.box(size=(0.05, 0.45, 0.45), pos=(x, 0.22, 0), color=metal, name="side")


def bridge(s, pos=(0, 0, 0), rot=(0, 0, 0), scale=1, length=10.0, width=3.0, stone="#a7a39a"):
    """spans along x, deck at y = 1.2"""
    with _g(s, pos, rot, scale):
        s.box(size=(length, 0.4, width), pos=(0, 1.0, 0), color=stone, name="deck")
        for z in (-width / 2 + 0.15, width / 2 - 0.15):
            s.box(size=(length, 0.5, 0.3), pos=(0, 1.45, z), color=stone, name="parapet")
        for x in (-length / 2 + 0.6, length / 2 - 0.6):
            s.box(size=(1.2, 1.0, width), pos=(x, 0.5, 0), color=stone, name="pier")


def windmill(s, pos=(0, 0, 0), rot=(0, 0, 0), scale=1, body="#e8e0d0", roof="#8b3a2f", blades="#f2f2f2"):
    with _g(s, pos, rot, scale):
        s.cylinder(r=2.0, h=7.0, r2=1.2, pos=(0, 3.5, 0), color=body, seg=14, name="tower")
        s.cone(r=1.5, h=1.8, pos=(0, 7.9, 0), color=roof, seg=14, name="cap")
        s.box(size=(0.9, 1.0, 0.1), pos=(0, 0.5, 1.95), color="#6b4a2b", name="door")
        s.cylinder(r=0.15, h=0.7, pos=(0, 7.0, 1.3), rot=(90, 0, 0), color="#4a3a2a", seg=8, name="axle")
        for i in range(4):
            with s.group(pos=(0, 7.0, 1.7), rot=(0, 0, 90 * i + 20)):
                s.box(size=(0.25, 4.5, 0.06), pos=(0, 2.4, 0), color="#6b4a2b", name="arm")
                s.box(size=(0.9, 3.0, 0.04), pos=(0.5, 2.9, 0.04), color=blades, name="sail")
