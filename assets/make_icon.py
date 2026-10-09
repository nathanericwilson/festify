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
    neck = go(H, 180 - lean, 0.55)
    head = go(neck, 180 - lean - tilt, 0.24)
    limbs = []
    for side, (a1, b1) in zip((1, -1), arms):
        sh = add(neck, mul(s, 0.17 * side * scale))
        el = go(sh, a1, 0.30)
        limbs.append(("arm", side, [sh, el, go(el, a1 + b1, 0.28)]))
    for side, (a1, b1) in zip((1, -1), legs):
        hp = add(H, mul(s, 0.1 * side * scale))
        kn = go(hp, a1, 0.46)
        limbs.append(("leg", side, [hp, kn, go(kn, a1 + b1, 0.46)]))
    return {"head": head, "torso": [H, neck], "limbs": limbs, "base": base, "side": s, "f": f,
            "color": color, "scale": scale}


def capsule(d, pts3, radius, color, edge=0.022):
    pts = [proj(p) for p in pts3]
    for rw, col in ((radius + edge, INK), (radius, color)):
        for (a, za), (b, zb) in zip(pts, pts[1:]):
            n = 10
            for i in range(n + 1):
                t = i / n
                x, y, z = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, za + (zb - za) * t
                r = FOCAL * rw / z
                d.ellipse([x - r, y - r, x + r, y + r], fill=col)
            d.line([a, b], fill=col, width=int(2 * FOCAL * rw / max(za, zb)))


def ball(d, p3, radius, color, edge=0.022):
    (x, y), z = proj(p3)
    for rw, col in ((radius + edge, INK), (radius, color)):
        r = FOCAL * rw / z
        d.ellipse([x - r, y - r, x + r, y + r], fill=col)


def draw_person(d, p):
    near = 1 if dot(p["side"], sub(CAM, p["base"])) > 0 else -1  # which side faces the camera
    col = tuple(p["color"][:3]) + (255,)
    k = p["scale"]
    for _, side, pts in p["limbs"]:
        if side != near:
            capsule(d, pts, 0.078 * k, shade(col, 0.68))
    capsule(d, p["torso"], 0.135 * k, col)
    ball(d, p["head"], 0.15 * k, col)
    for _, side, pts in p["limbs"]:
        if side == near:
            capsule(d, pts, 0.078 * k, col)


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


# ---------------------------------------------------------------- the scene
STAGE = dict(x0=1.25, x1=5.6, y=0.7, z0=1.3, z1=7.5)
PIT = (-0.55, 2.75)  # circle pit centre (x, z)
PIT_R = 1.3


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
    amp = ((60, 60, 70, 255), (25, 25, 32, 255), (40, 40, 48, 255))
    box(d, 4.6, 5.3, sy, sy + 1.1, 5.4, 6.1, *amp)
    box(d, 2.2, 2.9, sy, sy + 1.0, 4.8, 5.4, *amp)

    # light beams onto the band
    beams = Image.new("RGBA", img.size, (0, 0, 0, 0))
    bd = ImageDraw.Draw(beams)
    for lx, lz, col in ((1.95, 2.3, (30, 215, 96)), (2.95, 2.75, (255, 95, 190)), (3.95, 3.15, (30, 215, 96)), (4.75, 4.2, (255, 95, 190))):
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
    mic_base, mic_top, mic = stage_at(1.5, 2.3), (1.58, sy + 1.40, 2.3), (1.62, sy + 1.46, 2.3)
    put(depth(mic_base) + 0.05, lambda dd: (capsule(dd, [mic_base, mic_top], 0.018, (200, 200, 210, 255), 0.012),
                                            ball(dd, mic, 0.05, (60, 60, 70, 255))))

    def guitar(p, body_col):
        f, s, b = p["f"], p["side"], p["base"]
        near = 1 if dot(s, sub(CAM, b)) > 0 else -1
        c = add(b, add(mul(f, 0.18), add((0, 0.95, 0), mul(s, 0.22 * near))))
        neck_end = add(c, add(mul(f, 0.7), (0, 0.36, 0)))
        body = [add(c, add(mul(f, 0.24 * math.cos(a)), (0, 0.2 * math.sin(a), 0))) for a in [i * math.pi / 16 for i in range(32)]]

        def fn(dd):
            capsule(dd, [c, neck_end], 0.025, (60, 40, 30, 255), 0.014)
            dd.polygon([P(q) for q in body], fill=body_col, outline=INK, width=6)
        return fn

    gtr = person(stage_at(2.95, 2.75), 180, BAND["guitar"], lean=-14, tilt=-20, arms=((70, 35), (55, 60)), legs=((30, -10), (-20, 0)))
    bass = person(stage_at(3.95, 3.15), 180, BAND["bass"], lean=12, tilt=15, arms=((60, 40), (45, 70)), legs=((18, -8), (-15, 3)))
    drummer = person(stage_at(5.0, 4.2), 180, BAND["drums"], hip=0.55, lean=5, arms=((150, 20), (120, 30)), legs=((90, -90), (90, -90)))
    for p in (singer, gtr, bass, drummer):
        put(depth(p["base"]), lambda dd, p=p: draw_person(dd, p))
        shadows.append(p["base"])
    put(depth(gtr["base"]) - 0.3, guitar(gtr, (230, 40, 50, 255)))
    put(depth(bass["base"]) - 0.3, guitar(bass, (240, 240, 250, 255)))
    kit = (4.35, sy + 0.32, 4.2)
    put(depth(kit) - 0.4, lambda dd: drum(dd, kit, 0.32, 0.4, "x", (230, 40, 50, 255)))
    for cx, cz, h in ((4.25, 3.65, 1.1), (4.3, 4.75, 1.25)):
        top = (cx, sy + h, cz)
        put(depth(top) - 0.5, lambda dd, cx=cx, cz=cz, top=top: (
            capsule(dd, [(cx, sy, cz), top], 0.012, (200, 200, 210, 255), 0.01),
            dd.polygon([P(q) for q in disc(top, 0.28, "y", 24)], fill=(250, 210, 60, 255), outline=INK, width=5)))

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
    impact = add(victim_base, (-0.3, 1.3, -0.1))

    def bang(dd):
        (x, y), z = proj(impact)
        r = FOCAL * 0.12 / z
        for k in range(8):
            t = math.radians(k * 45 + 20)
            dd.line([(x + r * math.cos(t), y + r * math.sin(t)), (x + 2.2 * r * math.cos(t), y + 2.2 * r * math.sin(t))],
                    fill=(255, 240, 120, 255), width=int(r * 0.35))
    put(depth(impact) - 1, bang)

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
    img = finish(draw())
    img.save(f"{out}/festify.png")
    img.resize((256, 256), Image.LANCZOS).save(
        f"{out}/festify.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    if "--preview" in sys.argv:
        img.resize((48, 48), Image.LANCZOS).resize((192, 192), Image.NEAREST).save(f"{out}/preview48.png")
