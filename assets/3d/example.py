"""Example: a small house with a roof, door, windows, chimney, a fence and a tree.  Run:  python example.py"""
from aqua3d import Scene

s = Scene("house")
WALL, ROOF, WOOD, GLASS, GRASS, STONE = "#e8d9b8", "#9b3d2e", "#6b4a2b", "#8fc4e8", "#6aa84f", "#8a8f98"

s.plane(14, 14, color=GRASS, name="lawn")                                              # ground
s.box(size=(6, 3, 5), pos=(0, 1.5, 0), color=WALL, name="walls")                       # main block
s.extrude([(-3.4, 0), (3.4, 0), (0, 2.2)], depth=5.6, plane="xy", pos=(0, 3, 0), color=ROOF, name="roof")   # gable roof
s.box(size=(0.7, 1.4, 0.7), pos=(1.9, 4.1, -1), color=STONE, name="chimney")
s.box(size=(1.0, 2.0, 0.15), pos=(0, 1.0, 2.55), color=WOOD, name="door")
s.sphere(r=0.06, pos=(0.35, 1.0, 2.66), color="#d4af37", metal=1.0, rough=0.3, name="door knob")
for x in (-2, 2):
    s.box(size=(1.0, 1.0, 0.12), pos=(x, 1.7, 2.53), color=GLASS, rough=0.1, name="window")
    s.box(size=(1.2, 0.12, 0.2), pos=(x, 1.1, 2.57), color=WOOD, name="sill")
for i in range(8):                                                                       # fence
    s.box(size=(0.15, 1.0, 0.15), pos=(-6 + i * 1.7, 0.5, 5.5), color="#f2f2f2", name="post")
s.box(size=(13, 0.12, 0.08), pos=(0, 0.8, 5.5), color="#f2f2f2", name="rail")
with s.group(pos=(-5, 0, -3)):                                                           # tree
    s.cylinder(r=0.25, h=2.2, pos=(0, 1.1, 0), color=WOOD, name="trunk")
    s.sphere(r=1.3, pos=(0, 3.0, 0), color="#3f8f3f", seg=14, smooth=False, name="leaves")
    s.sphere(r=0.9, pos=(0.7, 3.9, 0.2), color="#4aa04a", seg=14, smooth=False, name="leaves2")
s.report()
s.save("../models/house.glb")
