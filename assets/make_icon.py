"""Draws assets/festify.png and festify.ico: a small 3D venue. A circle pit on the left, a rock band
(singer, guitarist, bassist, drummer) in side profile on a stage on the right, facing the pit.

Usage: python assets/make_icon.py assets [--preview]

It's a tiny software renderer: a perspective camera, a floor, box-shaped props, and people built from
3D joints (drawn as rounded strokes whose width follows depth), all painted back to front.
"""
import math
import sys

from PIL import Image, ImageDraw, ImageFilter

SS = 2048  # supersampled canvas; downscaled at the end for smooth edges
OUT = 1024
INK = (10, 8, 22, 255)

# ---------------------------------------------------------------- camera
CAM = (1.0, 4.4, -4.2)
TARGET = (1.55, 0.55, 3.0)
FOCAL = 0.9 * SS


def sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def add(a, b): return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
def mul(a, k): return (a[0] * k, a[1] * k, a[2] * k)
def dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def cross(a, b): return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
def norm(a): return mul(a, 1 / math.sqrt(dot(a, a)))


FWD = norm(sub(TARGET, CAM))
RIGHT = norm(cross((0, 1, 0), FWD))
UP = cross(FWD, RIGHT)


def proj(p):
    v = sub(p, CAM)
    z = dot(v, FWD)
    return (SS / 2 + FOCAL * dot(v, RIGHT) / z, SS * 0.66 - FOCAL * dot(v, UP) / z), z


def P(p): return proj(p)[0]
def depth(p): return proj(p)[1]


def shade(c, k):
    return tuple(max(0, min(255, int(v * k))) for v in c[:3]) + (255,)


# ---------------------------------------------------------------- palette
BG_TOP, BG_BOTTOM = (20, 16, 44), (44, 24, 70)
FLOOR = (38, 30, 70, 255)
STAGE_TOP, STAGE_FRONT, STAGE_SIDE = (70, 60, 120, 255), (28, 22, 54, 255), (40, 32, 74, 255)
PEOPLE = [(255, 82, 82), (255, 230, 80), (80, 200, 255), (190, 110, 255), (120, 235, 140), (240, 240, 250), (255, 95, 190), (255, 150, 40)]
BAND = {"singer": (255, 255, 255), "guitar": (255, 170, 40), "bass": (80, 200, 255), "drums": (255, 95, 190)}


# ---------------------------------------------------------------- people
STYLE = "silhouette"  # or "color": solid colored figures


def person(base, yaw, color, lean=10, tilt=0, hip=0.95, arms=((20, 60), (-20, 70)), legs=((25, -40), (-20, -35)), scale=1.0):
    """Joints for a person standing at base (x, y, z), facing yaw degrees (0 = +x, 90 = +z).
    Limb angles are in the person's side plane: 0 = straight down, 90 = forward, 180 = up.
    arms/legs are (right, left) pairs of (upper angle, extra bend for the lower segment)."""
    t = math.radians(yaw)
    f, s, u = (math.cos(t), 0, math.sin(t)), (-math.sin(t), 0, math.cos(t)), (0, 1, 0)

    def go(o, deg, length):
        r = math.radians(deg)
        return add(o, add(mul(f, length * math.sin(r) * scale), mul(u, -length * math.cos(r) * scale)))

    H = add(base, (0, hip * scale, 0))
    chest = go(H, 180 - lean, 0.36)
    neck = go(H, 180 - lean, 0.55)
    head = go(neck, 180 - lean - tilt, 0.25)
    limbs = []
    for side, (a1, b1) in zip((1, -1), arms):
        sh = add(go(H, 180 - lean, 0.5), mul(s, 0.19 * side * scale))
        el = go(sh, a1, 0.30)
        hand = go(el, a1 + b1, 0.27)
        limbs.append((side, [[sh, el, hand], [0.085, 0.07, 0.058]], [hand, 0.068]))
    for side, (a1, b1) in zip((1, -1), legs):
        hp = add(H, mul(s, 0.11 * side * scale))
        kn = go(hp, a1, 0.46)
        ank = go(kn, a1 + b1, 0.44)
        toe = add(ank, add(mul(f, 0.17 * scale), (0, -0.02 * scale, 0)))
        limbs.append((side, [[hp, kn, ank], [0.11, 0.085, 0.068]], [[ank, toe], [0.07, 0.06]]))
    shoulders = [add(go(H, 180 - lean, 0.5), mul(s, 0.19 * scale)), add(go(H, 180 - lean, 0.5), mul(s, -0.19 * scale))]
    hips = [add(H, mul(s, 0.11 * scale)), add(H, mul(s, -0.11 * scale))]
    return {"head": head, "trunk": [H, chest, neck], "shoulders": shoulders, "hips": hips, "limbs": limbs,
            "base": base, "side": s, "f": f, "color": color, "scale": scale}


def capsule(d, pts3, radii, color):
    """A tapered tube through 3D points; radii is one value or one per point (world units)."""
    if not isinstance(radii, (list, tuple)):
        radii = [radii] * len(pts3)
    pts = [proj(p) for p in pts3]
    for i in range(len(pts) - 1):
        (a, za), (b, zb) = pts[i], pts[i + 1]
        ra, rb = radii[i], radii[i + 1]
        d.line([a, b], fill=color, width=max(1, int(2 * FOCAL * min(ra, rb) / max(za, zb))))
        n = 14
        for k in range(n + 1):
            t = k / n
            x, y, z = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, za + (zb - za) * t
            r = FOCAL * (ra + (rb - ra) * t) / z
            d.ellipse([x - r, y - r, x + r, y + r], fill=color)


def ball(d, p3, radius, color):
    (x, y), z = proj(p3)
    r = FOCAL * radius / z
    d.ellipse([x - r, y - r, x + r, y + r], fill=color)


def rod(d, pts3, radius, color, edge=0.016):
    capsule(d, pts3, radius + edge, INK)
    capsule(d, pts3, radius, color)


def knob(d, p3, radius, color, edge=0.016):
    ball(d, p3, radius + edge, INK)
    ball(d, p3, radius, color)


def draw_person(d, p):
    """One clean outline around the whole body, then the body: far limbs, trunk, head, near limbs."""
    near = 1 if dot(p["side"], sub(CAM, p["base"])) > 0 else -1  # which side faces the camera
    k = p["scale"]
    col = tuple(p["color"][:3]) + (255,)
    if STYLE == "silhouette":
        edge, outline, body, far_body = 0.034, col, (22, 16, 40, 255), (16, 12, 30, 255)
    else:
        edge, outline, body, far_body = 0.024, INK, col, shade(col, 0.7)

    def parts(side_filter):
        out = []
        for side, (pts, radii), extra in p["limbs"]:
            if side_filter(side):
                out.append(("tube", pts, [r * k for r in radii]))
                if isinstance(extra[0], list):  # foot
                    out.append(("tube", extra[0], [r * k for r in extra[1]]))
                else:  # hand
                    out.append(("ball", extra[0], extra[1] * k))
        return out

    far, near_parts = parts(lambda s: s != near), parts(lambda s: s == near)
    core = [("tube", p["hips"], [0.11 * k] * 2), ("tube", p["trunk"], [0.13 * k, 0.155 * k, 0.11 * k]),
            ("tube", p["shoulders"], [0.1 * k] * 2), ("ball", p["head"], 0.16 * k)]

    def paint(items, color, grow=0.0):
        for kind, geo, r in items:
            if kind == "tube":
                capsule(d, geo, [x + grow for x in r], color)
            else:
                ball(d, geo, r + grow, color)

    paint(far + core + near_parts, outline, edge)
    paint(far, far_body)
    paint(core, body)
    if STYLE != "silhouette":  # a thin line keeps the near limbs readable where they cross the body
        paint(near_parts, INK, 0.012)
    paint(near_parts, body)


def shadow(img, base, r=0.38):
    pts = [P((base[0] + r * math.cos(a), base[1] + 0.002, base[2] + r * 0.75 * math.sin(a)))
           for a in [i * math.pi / 12 for i in range(24)]]
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).polygon(pts, fill=(0, 0, 0, 120))
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(10)))


def box(d, x0, x1, y0, y1, z0, z1, top, front, side):
    c = [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
    faces = []
    for idx, col, normal in (([1, 3, 7, 5], top, (0, 1, 0)), ([0, 2, 6, 4], front, (0, 0, -1)), ([0, 1, 3, 2], side, (-1, 0, 0))):
        center = mul(add(add(c[idx[0]], c[idx[1]]), add(c[idx[2]], c[idx[3]])), 0.25)
        if dot(normal, sub(CAM, center)) > 0:
            faces.append((depth(center), [P(c[i]) for i in idx], col))
    for _, pts, col in sorted(faces, key=lambda f: -f[0]):
        d.polygon(pts, fill=col, outline=INK, width=6)


def disc(center, r, axis, n=32):
    """Points on a circle of radius r around center, perpendicular to axis ('x', 'y' or 'z')."""
    out = []
    for i in range(n):
        a = 2 * math.pi * i / n
        u, v = r * math.cos(a), r * math.sin(a)
        out.append(add(center, {"x": (0, u, v), "y": (u, 0, v), "z": (u, v, 0)}[axis]))
    return out


def convex_hull(points):
    pts = sorted(set((round(x, 2), round(y, 2)) for x, y in points))
    if len(pts) < 3:
        return pts

    def half(seq):
        out = []
        for p in seq:
            while len(out) >= 2 and (out[-1][0] - out[-2][0]) * (p[1] - out[-2][1]) - (out[-1][1] - out[-2][1]) * (p[0] - out[-2][0]) <= 0:
                out.pop()
            out.append(p)
        return out
    lower, upper = half(pts), half(list(reversed(pts)))
    return lower[:-1] + upper[:-1]


def drum(d, center, r, length, axis, color):
    """Cylinder: the hull between both end discs, then the end facing the camera."""
    half = {"x": (length / 2, 0, 0), "y": (0, length / 2, 0), "z": (0, 0, length / 2)}[axis]
    a, b = disc(sub(center, half), r, axis), disc(add(center, half), r, axis)
    front = a if depth(a[0]) + depth(a[8]) < depth(b[0]) + depth(b[8]) else b
    d.polygon(convex_hull([P(q) for q in a + b]), fill=shade(color, 0.75), outline=INK, width=6)
    d.polygon([P(q) for q in front], fill=(240, 240, 250, 255), outline=INK, width=6)


def drum_shell(d, center, r, length, axis, shell, head=(236, 236, 244, 255), logo=None):
    """Cylinder: the hull between both end discs, then the end facing the camera (optionally with a logo)."""
    half = {"x": (length / 2, 0, 0), "y": (0, length / 2, 0), "z": (0, 0, length / 2)}[axis]
    a, b = disc(sub(center, half), r, axis), disc(add(center, half), r, axis)
    if axis == "y":
        front = b  # seen from above: the top head shows
    else:
        front = a if depth(a[0]) + depth(a[8]) < depth(b[0]) + depth(b[8]) else b
    d.polygon(convex_hull([P(q) for q in a + b]), fill=shell, outline=INK, width=6)
    d.polygon([P(q) for q in front], fill=head, outline=INK, width=6)
    if logo:
        fc = mul(add(front[0], front[len(front) // 2]), 0.5)
        d.polygon([P(add(fc, mul(sub(q, fc), 0.6))) for q in front], outline=logo, width=12)
        d.polygon([P(add(fc, mul(sub(q, fc), 0.22))) for q in front], fill=logo)


def cymbal(d, center, r, tilt=0.28, color=(232, 190, 64, 255)):
    pts = [(center[0] + r * math.cos(a), center[1] - tilt * r * math.cos(a), center[2] + r * math.sin(a))
           for a in [i * 2 * math.pi / 40 for i in range(40)]]
    d.polygon([P(q) for q in pts], fill=color, outline=INK, width=5)
    d.polygon([P(add(center, mul(sub(q, center), 0.3))) for q in pts], fill=shade(color, 1.15))


def amp(d, x0, x1, y0, y1, z0, z1, cones=2):
    box(d, x0, x1, y0, y1, z0, z1, (58, 58, 68, 255), (26, 26, 34, 255), (40, 40, 50, 255))
    m, top = 0.06, y1 - 0.16  # grille on the front face (z = z0), with speaker cones
    z = z0 - 0.002
    d.polygon([P((x0 + m, y0 + m, z)), P((x1 - m, y0 + m, z)), P((x1 - m, top, z)), P((x0 + m, top, z))],
              fill=(44, 44, 54, 255), outline=INK, width=4)
    r = min(x1 - x0 - 2 * m, (top - y0 - m) / cones) * 0.42
    for i in range(cones):
        c = ((x0 + x1) / 2, y0 + m + (i + 0.5) * (top - y0 - m) / cones, z0 - 0.004)
        d.polygon([P(q) for q in disc(c, r, "z")], fill=(18, 18, 24, 255), outline=(96, 96, 110, 255), width=4)
        d.polygon([P(q) for q in disc(c, r * 0.35, "z")], fill=(74, 74, 88, 255))
    for i in range(4):  # control knobs
        knob(d, (x0 + (x1 - x0) * (0.2 + 0.2 * i), y1 - 0.08, z0 - 0.01), 0.018, (220, 220, 230, 255), 0.008)


def guitar_fn(p, body, lower=0.23, upper=0.17, neck_len=0.95, accent=(20, 20, 26, 255)):
    """A guitar held across the body, in the player's side plane (so it reads in profile)."""
    f, s, b = p["f"], p["side"], p["base"]
    near = 1 if dot(s, sub(CAM, b)) > 0 else -1
    th = math.radians(24)
    n = add(mul(f, math.cos(th)), (0, math.sin(th), 0))      # along the neck
    m = add(mul(f, -math.sin(th)), (0, math.cos(th), 0))     # across the body
    hip = p["trunk"][0]
    origin = add(hip, add(mul(f, 0.14), add((0, -0.02, 0), mul(s, 0.24 * near))))

    def pt(a, c):
        return add(origin, add(mul(n, a), mul(m, c)))

    def circ(a0, c0, r, k=36):
        return [P(pt(a0 + r * math.cos(t), c0 + r * math.sin(t))) for t in [i * 2 * math.pi / k for i in range(k)]]

    def fn(dd):
        dd.polygon(circ(-0.06, 0, lower), fill=INK)
        dd.polygon(circ(0.17, 0.02, upper), fill=INK)
        dd.polygon(circ(-0.06, 0, lower - 0.013), fill=body)
        dd.polygon(circ(0.17, 0.02, upper - 0.013), fill=body)
        dd.polygon(circ(-0.07, 0, lower * 0.55), fill=shade(body, 1.3))       # sunburst
        rod(dd, [pt(0.16, 0.0), pt(neck_len, 0.0)], 0.03, (92, 58, 34, 255), 0.012)   # neck
        head = [pt(neck_len - 0.01, -0.04), pt(neck_len + 0.15, -0.055), pt(neck_len + 0.16, 0.04), pt(neck_len - 0.01, 0.035)]
        dd.polygon([P(q) for q in head], fill=(30, 22, 18, 255), outline=INK, width=4)
        for a in (-0.03, 0.09):  # pickups
            dd.polygon([P(pt(a - 0.018, -0.07)), P(pt(a + 0.018, -0.07)), P(pt(a + 0.018, 0.07)), P(pt(a - 0.018, 0.07))], fill=accent)
        dd.polygon([P(pt(-0.13, -0.08)), P(pt(-0.11, -0.08)), P(pt(-0.11, 0.08)), P(pt(-0.13, 0.08))], fill=(200, 200, 210, 255))
    return fn



# ---------------------------------------------------------------- the scene
STAGE = dict(x0=1.25, x1=5.6, y=0.7, z0=1.3, z1=7.5)
PIT = (-0.55, 2.75)  # circle pit centre (x, z)
PIT_R = 1.3
RISER = 0.3  # drum riser height


def draw():
    img = Image.new("RGBA", (SS, SS), (0, 0, 0, 255))
    d = ImageDraw.Draw(img)
    for y in range(SS):  # back wall gradient
        t = y / SS
        d.line([(0, y), (SS, y)], fill=tuple(int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3)) + (255,))
    d.polygon([P((-12, 0, -3)), P((12, 0, -3)), P((12, 0, 9)), P((-12, 0, 9))], fill=FLOOR)

    # the pit: a glowing ring worn into the floor
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    circle = lambda r, y: [P((PIT[0] + r * math.cos(a), y, PIT[1] + r * math.sin(a))) for a in [i * math.pi / 40 for i in range(80)]]
    g.polygon(circle(2.2, 0.002), fill=(255, 95, 190, 45))
    ring = circle(PIT_R, 0.003)
    g.line(ring + ring[:1], fill=(255, 95, 190, 170), width=26)
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(14)))
    d = ImageDraw.Draw(img)

    # stage, amps
    sy = STAGE["y"]
    box(d, STAGE["x0"], STAGE["x1"], 0, sy, STAGE["z0"], STAGE["z1"], STAGE_TOP, STAGE_FRONT, STAGE_SIDE)
    box(d, 3.75, 5.45, sy, sy + RISER, 4.1, 5.5, (82, 72, 136, 255), (34, 28, 64, 255), (48, 40, 86, 255))  # drum riser
    amp(d, 5.55, 6.2, sy, sy + 1.15, 5.0, 5.7)
    amp(d, 2.6, 3.35, sy, sy + 0.95, 4.7, 5.3)

    # light beams onto the band
    beams = Image.new("RGBA", img.size, (0, 0, 0, 0))
    bd = ImageDraw.Draw(beams)
    for lx, lz, col in ((1.95, 2.3, (30, 215, 96)), (2.95, 2.75, (255, 95, 190)), (4.75, 2.55, (30, 215, 96)), (4.4, 4.75, (255, 95, 190))):
        pool = [P((lx + 0.75 * math.cos(a), sy + 0.01, lz + 0.6 * math.sin(a))) for a in [i * math.pi / 16 for i in range(32)]]
        bd.polygon(convex_hull(pool + [P((lx, 4.8, lz))]), fill=col + (55,))
        bd.polygon(pool, fill=col + (90,))
    img.alpha_composite(beams.filter(ImageFilter.GaussianBlur(6)))

    items, shadows = [], []  # (depth, draw function)

    def put(z, fn):
        items.append((z, fn))

    def stage_at(x, z):
        return (x, sy, z)

    # ---- the band, facing the pit (yaw 180 = facing -x), so the camera sees them side-on
    singer = person(stage_at(1.95, 2.3), 180, BAND["singer"], lean=-8, tilt=-15,
                    arms=((165, 5), (95, 70)), legs=((12, -5), (-12, 5)))
    pole_x, pole_z = 1.45, 2.3
    hub, pole_top = (pole_x, sy + 0.16, pole_z), (pole_x, sy + 1.12, pole_z)
    boom_end, mic_tip = (1.72, sy + 1.5, pole_z), (1.8, sy + 1.53, pole_z)

    def mic_stand(dd):
        metal = (190, 190, 204, 255)
        for a in (0, 120, 240):
            t = math.radians(a + 30)
            rod(dd, [hub, (pole_x + 0.2 * math.cos(t), sy, pole_z + 0.2 * math.sin(t))], 0.011, metal, 0.008)
        rod(dd, [hub, pole_top], 0.014, metal, 0.009)
        rod(dd, [pole_top, boom_end], 0.011, metal, 0.008)
        rod(dd, [boom_end, mic_tip], 0.026, (30, 30, 36, 255), 0.01)
        knob(dd, mic_tip, 0.036, (120, 120, 132, 255), 0.01)
    put(depth(hub) - 0.05, mic_stand)

    gtr = person(stage_at(2.95, 2.75), 180, BAND["guitar"], lean=-14, tilt=-20, arms=((70, 35), (55, 60)), legs=((30, -10), (-20, 0)))
    bass = person(stage_at(4.75, 2.55), 180, BAND["bass"], lean=12, tilt=15, arms=((60, 40), (45, 70)), legs=((18, -8), (-15, 3)))
    drummer = person((4.8, sy + RISER, 4.75), 180, BAND["drums"], hip=0.55, lean=5, arms=((150, 20), (120, 30)), legs=((90, -90), (90, -90)))
    for p in (singer, gtr, bass, drummer):
        put(depth(p["base"]), lambda dd, p=p: draw_person(dd, p))
        shadows.append(p["base"])
    put(depth(gtr["base"]) - 0.3, guitar_fn(gtr, (214, 40, 46, 255)))
    put(depth(bass["base"]) - 0.3, guitar_fn(bass, (34, 150, 200, 255), lower=0.25, upper=0.18, neck_len=1.22,
                                             accent=(235, 235, 240, 255)))

    # drum kit in front of the drummer (who faces -x), on the riser
    metal, shell = (190, 190, 204, 255), (196, 30, 58, 255)
    ry, sx, sz = sy + RISER, 4.8, 4.75   # riser top, drum seat
    kick, snare, rack = (sx - 0.62, ry + 0.3, sz), (sx - 0.32, ry + 0.62, sz + 0.28), (sx - 0.55, ry + 0.78, sz - 0.02)
    floor_tom, hihat = (sx - 0.3, ry + 0.38, sz - 0.4), (sx - 0.4, ry + 0.98, sz + 0.62)
    crash, ride = (sx - 0.78, ry + 1.32, sz - 0.48), (sx - 0.72, ry + 1.2, sz + 0.52)

    def stand(dd, top):
        rod(dd, [(top[0], ry, top[2]), top], 0.011, metal, 0.008)

    def floor_tom_fn(dd):
        for a in (0.5, 2.6, 4.6):
            rod(dd, [add(floor_tom, (0.15 * math.cos(a), -0.1, 0.15 * math.sin(a))),
                     (floor_tom[0] + 0.2 * math.cos(a), ry, floor_tom[2] + 0.2 * math.sin(a))], 0.01, metal, 0.007)
        drum_shell(dd, floor_tom, 0.19, 0.36, "y", shell)

    put(depth(kick) - 0.2, lambda dd: drum_shell(dd, kick, 0.32, 0.42, "x", shell, logo=(255, 95, 190, 255)))
    put(depth(floor_tom) - 0.3, floor_tom_fn)
    put(depth(rack) - 0.35, lambda dd: drum_shell(dd, rack, 0.14, 0.18, "y", shell))
    put(depth(snare) - 0.3, lambda dd: (stand(dd, add(snare, (0, -0.07, 0))), drum_shell(dd, snare, 0.17, 0.13, "y", (200, 200, 212, 255))))
    put(depth(hihat) - 0.2, lambda dd: (stand(dd, hihat), cymbal(dd, add(hihat, (0, -0.05, 0)), 0.17, tilt=0.0), cymbal(dd, hihat, 0.17, tilt=0.0)))
    put(depth(crash) - 0.6, lambda dd: (stand(dd, crash), cymbal(dd, crash, 0.26)))
    put(depth(ride) - 0.4, lambda dd: (stand(dd, ride), cymbal(dd, ride, 0.29, tilt=-0.22)))

    # ---- the circle pit: runners going round counter-clockwise
    for i, a in enumerate((200, 330, 15, 60, 105, 150)):
        r = math.radians(a)
        base = (PIT[0] + PIT_R * math.cos(r), 0, PIT[1] + PIT_R * math.sin(r))
        stride = 1 if i % 2 else -1
        jumping = i == 3
        p = person(base, a + 90, PEOPLE[i], lean=24, tilt=5, hip=0.95 + (0.3 if jumping else 0),
                   arms=((70 * stride + 10, 70), (-60 * stride + 10, 80)),
                   legs=((80, -110), (60, -100)) if jumping else ((45 * stride, -60), (-40 * stride, -45)))
        put(depth(base), lambda dd, p=p: draw_person(dd, p))
        shadows.append(base)

    # ---- the shove at the front of the circle
    pa = math.radians(258)
    shover_base = (PIT[0] + PIT_R * math.cos(pa) - 0.35, 0, PIT[1] + PIT_R * math.sin(pa))
    shover = person(shover_base, 0, PEOPLE[6], lean=38, tilt=10, arms=((85, 5), (95, 0)), legs=((45, -15), (-35, -50)))
    victim_base = add(shover_base, (0.8, 0, 0.05))
    victim = person(victim_base, 180, PEOPLE[7], lean=-32, tilt=-35, hip=1.02,
                    arms=((150, 40), (170, -30)), legs=((55, -70), (-10, 10)))
    for p in (shover, victim):
        put(depth(p["base"]), lambda dd, p=p: draw_person(dd, p))
        shadows.append(p["base"])

    for b in shadows:
        shadow(img, b)
    d = ImageDraw.Draw(img)
    for _, fn in sorted(items, key=lambda it: -it[0]):
        fn(d)
    return img


def finish(img):
    img = img.resize((OUT, OUT), Image.LANCZOS)
    mask = Image.new("L", (OUT, OUT), 0)
    ImageDraw.Draw(mask).rounded_rectangle([24, 24, OUT - 24, OUT - 24], radius=210, fill=255)
    tile = Image.new("RGBA", (OUT, OUT), (0, 0, 0, 0))
    tile.paste(img, (0, 0), mask)
    ImageDraw.Draw(tile).rounded_rectangle([24, 24, OUT - 24, OUT - 24], radius=210, outline=INK, width=22)
    return tile


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "assets"
    if "--silhouette" in sys.argv:
        STYLE = "silhouette"
    img = finish(draw())
    img.save(f"{out}/festify.png")
    img.resize((256, 256), Image.LANCZOS).save(
        f"{out}/festify.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    if "--preview" in sys.argv:
        img.resize((48, 48), Image.LANCZOS).resize((192, 192), Image.NEAREST).save(f"{out}/preview48.png")
