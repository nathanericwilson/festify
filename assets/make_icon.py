"""Draws assets/festify.png and festify.ico (Keith Haring-style moshers in front of a stage). Usage: python assets/make_icon.py assets"""
import math, sys
from PIL import Image, ImageDraw

S = 1024
BLACK = (17, 17, 17, 255)
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# tile
d.rounded_rectangle([24, 24, S - 24, S - 24], radius=210, fill=(255, 214, 0, 255))
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle([24, 24, S - 24, S - 24], radius=210, fill=255)

layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
L = ImageDraw.Draw(layer)
# stage: back wall + platform, Spotify-green spotlights
L.rectangle([0, 0, S, 290], fill=BLACK)
for cx in (250, 512, 774):
    L.polygon([(cx - 30, 120), (cx + 30, 120), (cx + 120, 290), (cx - 120, 290)], fill=(29, 185, 84, 255))
for cx in (250, 512, 774):
    L.ellipse([cx - 45, 85, cx + 45, 150], fill=(255, 255, 255, 255))
L.rectangle([0, 290, S, 330], fill=(255, 255, 255, 255))
L.rectangle([0, 324, S, 346], fill=BLACK)

def seg(draw, a, b, w, col):
    draw.line([a, b], fill=col, width=w)
    for p in (a, b):
        draw.ellipse([p[0] - w / 2, p[1] - w / 2, p[0] + w / 2, p[1] + w / 2], fill=col)

def figure(f, col, W=58, O=22):
    segs = [(f["neck"], f["hip"]), (f["neck"], f["el1"]), (f["el1"], f["h1"]), (f["neck"], f["el2"]), (f["el2"], f["h2"]),
            (f["hip"], f["k1"]), (f["k1"], f["f1"]), (f["hip"], f["k2"]), (f["k2"], f["f2"])]
    hx, hy, hr = f["head"]
    for pass_w, c in ((W + O, BLACK), (W, col)):
        for a, b in segs:
            seg(L, a, b, pass_w, c)
        r = hr + (O / 2 if c == BLACK else 0)
        L.ellipse([hx - r, hy - r, hx + r, hy + r], fill=c)

def offset(f, dx, dy, s=0.74):
    out = {}
    for k, v in f.items():
        out[k] = (v[0] * s + dx, v[1] * s + dy) + tuple(x * s for x in v[2:])
    return out

# jumping figure, arms up
jump = {"head": (0, -262, 64), "neck": (0, -170), "hip": (0, 0),
        "el1": (-140, -200), "h1": (-200, -320), "el2": (140, -200), "h2": (200, -320),
        "k1": (-110, 40), "f1": (-60, 140), "k2": (110, 40), "f2": (60, 140)}
# leaning mosher, arms out
lean = {"head": (40, -245, 62), "neck": (20, -165), "hip": (-20, 0),
        "el1": (-80, -150), "h1": (-150, -210), "el2": (120, -170), "h2": (190, -110),
        "k1": (-90, 70), "f1": (-110, 170), "k2": (60, 80), "f2": (40, 180)}
lean2 = {k: ((-v[0],) + tuple(v[1:])) for k, v in lean.items()}

figure(offset(lean, 268, 760), (255, 59, 48, 255), W=46, O=18)
figure(offset(lean2, 756, 760), (0, 122, 255, 255), W=46, O=18)
figure(offset(jump, 512, 690), (255, 45, 150, 255), W=46, O=18)

# Haring "radiant" motion lines
def dash(a, b):
    L.line([a, b], fill=BLACK, width=22)
    for p in (a, b):
        L.ellipse([p[0] - 11, p[1] - 11, p[0] + 11, p[1] + 11], fill=BLACK)
for cx, cy, r, angs in [(512, 496, 105, (-90, -125, -55)), (298, 579, 85, (-125, 200)), (726, 579, 85, (-55, -20))]:
    for a in angs:
        t = math.radians(a)
        dash((cx + r * math.cos(t), cy + r * math.sin(t)), (cx + (r + 50) * math.cos(t), cy + (r + 50) * math.sin(t)))

tile = Image.new("RGBA", (S, S), (0, 0, 0, 0))
tile.paste(layer, (0, 0), Image.composite(layer, Image.new("RGBA", (S, S)), mask).split()[3])
img.alpha_composite(tile)
# outline the tile
d2 = ImageDraw.Draw(img)
d2.rounded_rectangle([24, 24, S - 24, S - 24], radius=210, outline=BLACK, width=26)

out = sys.argv[1]
img.save(out + "/festify.png")
img.resize((256, 256), Image.LANCZOS).save(out + "/festify.ico", sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
img.resize((48, 48), Image.LANCZOS).resize((192, 192), Image.NEAREST).save(out + "/preview48.png")
