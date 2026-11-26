# Aqua 3D kit

Made for AIs and for you. Build 3D models with plain Python, see them instantly, export them anywhere.

1. `aqua3d.py` - the model builder (standard library only). `from aqua3d import Scene`
2. `viewer.html` - open it in the Preview pane: `3d/viewer.html?m=../models/house.glb` (orbit, zoom, wireframe, auto-rotate, save picture)
3. `example.py` - a complete house with roof, door, windows, fence and tree: `python example.py`
4. `vendor/` - three.js r160 (MIT), so the viewer works offline

## Cheat sheet

```python
from aqua3d import Scene
s = Scene("my model")                      # Y is up; 1 unit = 1 metre is a good habit
s.box(size=(w, h, d), pos=(x, y, z), color="#d9c7a3")
s.sphere(r=0.5, seg=24)                   # seg = smoothness, smooth=False for a low-poly look
s.cylinder(r=0.5, h=2, r2=0.3)            # r2 = top radius (a tapered pillar); axis is Y, centred
s.cone(r=1, h=2, seg=4, smooth=False)     # seg=4 gives a pyramid
s.torus(R=1, r=0.25)                       # ring / donut / wheel tyre
s.plane(10, 10)                            # ground
s.extrude(outline, depth=3)                # outline=[(x,z),...] seen from above, grows UP (floor plans, walls)
s.extrude(profile, depth=5, plane="xy")    # profile=[(x,y),...] extruded along Z (gable roofs, beams)
s.lathe([(0.5, 0), (0.6, 0.4), (0.3, 1.0), (0.4, 1.2)], seg=32)   # spin a (radius, y) profile: vases, bottles, columns
s.mesh(vertices, faces)                    # your own triangles/quads
with s.group(pos=(5, 0, 0), rot=(0, 30, 0), scale=1.5):           # move/rotate/scale a set of parts together
    s.box(...); s.sphere(...)
s.save("models/thing.glb")                # path is relative to where you run python: from the workspace root use models/, from inside 3d/ use ../models/
                                           # .obj (+.mtl) and .stl work too; the viewer lists everything in models/
```

Fast big scenes (no Blender, no installs; 1000+ parts build in under a second):

```python
from aqua_parts import tree, pine, rock, house, tower, fence, lamp_post, car, table, chair, bench, bridge, windmill
for (x, y, z) in Scene.grid(4, 3, 14, 14):  house(s, pos=(x, 0, z))      # grid of houses
for (x, y, z, ang) in Scene.ring(12, 30):   pine(s, pos=(x, 0, z))       # ring of trees
t = Scene("tower"); tower(t); s.use(t, pos=(20, 0, 0), scale=1.5)        # build once, reuse
s.check()                                                                # text self-check: floating or sunk parts
```

Look at your work: the AI tool `look_3d` renders 4 views and a text report; you can also open `3d/viewer.html?m=../models/x.glb&sheet=1` yourself.

Every shape also takes `rot=(rx, ry, rz)` degrees, `scale=`, `metal=0..1`, `rough=0..1`, `name=`.

## Make it look good

- Build from many small, well-placed parts (a house is walls + roof + door + windows + chimney + fence + tree), not one blob.
- Use a small colour palette (3-6 colours) and real proportions (door about 2 m high, table about 0.75 m).
- Sit things on the ground (y = height / 2 for a box centred on pos) and avoid parts floating or sinking.
- Use `smooth=False` for crisp low-poly, `seg=32+` and `smooth=True` for round things.
- Reuse with loops and `s.group(...)`.
- Run `s.report()` to check the size, then look at it in the viewer and fix what looks wrong.
