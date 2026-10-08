"""Draws assets/festify.png and festify.ico: Keith Haring-style circle pit (left) and a rock band on stage (right).

Usage: python assets/make_icon.py assets [--preview]
Each figure is posed with joint angles (degrees; 0 = right, 90 = down, -90 = up) and drawn as thick, smoothly
bent strokes: a black outline pass, then a color pass. Figures are drawn back to front so overlaps read.
"""
import math
import sys

from PIL import Image, ImageDraw

S = 1024
BLACK = (17, 17, 17, 255)
WHITE = (255, 255, 255, 255)
GREEN = (29, 185, 84, 255)


def at(p, ang, length):
    t = math.radians(ang)
    return (p[0] + length * math.cos(t), p[1] + length * math.sin(t))


def bend(a, m, b, n=24):
    """Smooth curve from a to b that passes through m (a limb with a soft elbow/knee)."""
    c = (2 * m[0] - (a[0] + b[0]) / 2, 2 * m[1] - (a[1] + b[1]) / 2)
    return [((1 - t) ** 2 * a[0] + 2 * (1 - t) * t * c[0] + t * t * b[0],
             (1 - t) ** 2 * a[1] + 2 * (1 - t) * t * c[1] + t * t * b[1]) for t in (k / n for k in range(n + 1))]


def stroke(d, path, w, col):
    r = w / 2
    for p, q in zip(path, path[1:]):
        d.line([p, q], fill=col, width=int(w))
    for p in path:
        d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=col)


def pose(hip, u, lean=0, tilt=0, arms=((-120, -100), (-60, -80)), legs=((110, 95), (70, 85)), curl=0):
    """Joint positions for a figure of unit size u. lean tilts the torso (+ = toward the right)."""
    up = -90 + lean
    mid = at(hip, up + curl, 0.9 * u)
    neck = at(hip, up, 1.75 * u)
    head = at(neck, up + tilt, 1.0 * u)
    limbs = [bend(hip, mid, neck)]
    for a1, a2 in arms:
        e = at(neck, a1, 1.0 * u)
        limbs.append(bend(neck, e, at(e, a2, 1.0 * u)))
    for l1, l2 in legs:
        k = at(hip, l1, 1.05 * u)
        limbs.append(bend(hip, k, at(k, l2, 1.05 * u)))
    return {"u": u, "head": head, "limbs": limbs}


def figure(d, f, col):
    u = f["u"]
    w, o, hr = 0.56 * u, 0.26 * u, 0.7 * u
    hx, hy = f["head"]
    for width, c in ((w + o, BLACK), (w, col)):
        for limb in f["limbs"]:
            stroke(d, limb, width, c)
    # head last, with its own outline, so raised arms never swallow it
    d.ellipse([hx - hr - o / 2, hy - hr - o / 2, hx + hr + o / 2, hy + hr + o / 2], fill=BLACK)
    d.ellipse([hx - hr, hy - hr, hx + hr, hy + hr], fill=col)


def flip(angles):
    """Mirror a pose's limb angles left-right."""
    return tuple((180 - a1, 180 - a2) for a1, a2 in angles)


def burst(d, center, r, angles, length=30, w=14):
    for a in angles:
        stroke(d, [at(center, a, r), at(center, a, r + length)], w, BLACK)


def outlined(d, shape, xy, fill, w=12, **kw):
    getattr(d, shape)(xy, fill=fill, outline=BLACK, width=w, **kw)


RUN_ARMS, RUN_LEGS = ((-30, -75), (160, 110)), ((40, 100), (140, 110))


def band(d, x0, floor, u):
    """Rock band on a stage whose floor is at y=floor: guitarist, drummer behind the kit, singer at the mic."""
    k = u / 40  # scale for props
    # amp stack
    outlined(d, "rectangle", [x0 + 8, floor - 150 * k, x0 + 78 * k, floor], (60, 60, 60, 255), w=8)
    for y in (floor - 112 * k, floor - 45 * k):
        d.ellipse([x0 + 22 * k, y - 22 * k, x0 + 64 * k, y + 22 * k], fill=BLACK)
    # drummer + kit
    dx = x0 + 250 * k
    figure(d, pose((dx, floor - 105 * k), u * 0.9, arms=((-150, -110), (-30, -70)), legs=((110, 90), (70, 90))), CYAN)
    for side in (-1, 1):  # cymbals on stands
        cx = dx + side * 105 * k
        stroke(d, [(cx, floor - 175 * k), (cx, floor)], 7, WHITE)
        d.line([(cx - 38 * k, floor - 172 * k), (cx + 38 * k, floor - 182 * k)], fill=WHITE, width=10)
    outlined(d, "ellipse", [dx - 62 * k, floor - 125 * k, dx + 62 * k, floor - 1], WHITE, w=10)   # bass drum
    d.ellipse([dx - 18 * k, floor - 80 * k, dx + 18 * k, floor - 44 * k], fill=RED)
    for side in (-1, 1):  # toms
        cx = dx + side * 62 * k
        outlined(d, "ellipse", [cx - 28 * k, floor - 150 * k, cx + 28 * k, floor - 108 * k], RED, w=8)
    # guitarist leaning back, guitar slung low
    gx = x0 + 120 * k
    figure(d, pose((gx, floor - 95 * k), u, lean=-15, tilt=-20, arms=((40, -10), (10, 20)), legs=((125, 95), (60, 85))), ORANGE)
    stroke(d, [(gx + 12 * k, floor - 100 * k), (gx + 110 * k, floor - 185 * k)], 13, BLACK)   # neck
    outlined(d, "ellipse", [gx - 22 * k, floor - 125 * k, gx + 46 * k, floor - 70 * k], RED, w=8)
    # singer, fist up, at the mic stand
    sx = x0 + 380 * k
    stroke(d, [(sx - 38 * k, floor), (sx - 38 * k, floor - 175 * k)], 8, WHITE)
    d.ellipse([sx - 52 * k, floor - 195 * k, sx - 24 * k, floor - 167 * k], fill=WHITE)
    figure(d, pose((sx, floor - 95 * k), u, lean=-8, tilt=-15, arms=((-160, -150), (-80, -105)),
                   legs=((120, 95), (65, 90))), PINK)


PURPLE, PINK, CYAN = (150, 80, 220, 255), (255, 45, 150, 255), (0, 190, 220, 255)
RED, BLUE, ORANGE = (255, 59, 48, 255), (0, 110, 255, 255), (255, 140, 0, 255)
GREEN_DARK = (25, 120, 60, 255)


def draw() -> Image.Image:
    layer = Image.new("RGBA", (S, S), (255, 214, 0, 255))
    d = ImageDraw.Draw(layer)

    # ---- stage on the right
    floor = 650
    d.rectangle([575, 0, S, floor], fill=BLACK)
    for cx in (655, 785, 905):
        d.polygon([(cx - 16, 150), (cx + 16, 150), (cx + 75, floor), (cx - 75, floor)], fill=GREEN_DARK)
        d.ellipse([cx - 28, 125, cx + 28, 168], fill=WHITE)
    band(d, 545, floor, 39)
    d.rectangle([565, floor, S, floor + 26], fill=WHITE)
    d.rectangle([565, floor + 22, S, floor + 90], fill=BLACK)

    for i, (x, col) in enumerate(((640, BLUE), (760, RED), (880, PURPLE))):
        figure(d, pose((x, 995), 40, lean=(-8, 6, -4)[i], arms=((-115, -95), (-50, -75 + 10 * i)),
                       legs=((100, 90), (80, 90))), col)

    # ---- circle pit on the left (runs counter-clockwise: back row heads left, front row right)
    box = [95, 300, 545, 800]
    for a, b in ((195, 245), (285, 335), (15, 65), (115, 165)):
        d.arc(box, a, b, fill=BLACK, width=14)
    figure(d, pose((200, 400), 36, lean=-25, arms=flip(RUN_ARMS), legs=flip(RUN_LEGS)), CYAN)
    figure(d, pose((400, 395), 36, lean=-30, tilt=-20, arms=flip(((-60, -110), (150, 100))), legs=flip(RUN_LEGS)), PURPLE)
    figure(d, pose((150, 600), 40, lean=-10, tilt=20, arms=((-140, -100), (-60, -20)), legs=((60, 120), (150, 90))), RED)
    figure(d, pose((500, 590), 40, lean=12, arms=((-120, -170), (-40, -90)), legs=((150, 60), (40, 90))), BLUE)
    # front: a shove sends the pink one flying
    figure(d, pose((190, 820), 46, lean=40, tilt=10, arms=((-5, 5), (15, 10)), legs=((150, 125), (60, 100))), ORANGE)
    figure(d, pose((385, 835), 46, lean=38, tilt=35, arms=((-175, -150), (165, 195)), legs=((150, 175), (60, 20)),
                   curl=-14), PINK)

    burst(d, (345, 785), 34, (-80, -40, 0, 40))   # shove lands
    burst(d, (470, 760), 56, (-90, -50, -10))         # head snapping back
    burst(d, (150, 500), 50, (-150, -110, -70))
    burst(d, (500, 490), 50, (-70, -30, 0))
    burst(d, (300, 300), 40, (-115, -65))

    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([24, 24, S - 24, S - 24], radius=210, fill=255)
    img.paste(layer, (0, 0), mask)
    ImageDraw.Draw(img).rounded_rectangle([24, 24, S - 24, S - 24], radius=210, outline=BLACK, width=26)
    return img


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "assets"
    img = draw()
    img.save(f"{out}/festify.png")
    img.resize((256, 256), Image.LANCZOS).save(
        f"{out}/festify.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    if "--preview" in sys.argv:
        img.resize((48, 48), Image.LANCZOS).resize((192, 192), Image.NEAREST).save(f"{out}/preview48.png")
