"""Animated hero for the picks email: the total counts up and the eleven positions grow in.
Writes dashboard/email_hero.gif (plays once, then holds on the final frame)."""
import json, re, math
from PIL import Image, ImageDraw, ImageFont
from config import *

D = json.loads(re.sub(r"^window\.DASH = |;$", "", (DASHBOARD_DIR / "data.js").read_text(encoding="utf-8")))
P = D["picks"]
W, H, S = 480, 215, 2                     # css size and render scale
w, h = W * S, H * S
PAPER, INK, INK2, INK3, TRACK, UP = (255, 255, 255), (18, 18, 18), (91, 91, 87), (143, 143, 137), (236, 236, 233), (11, 122, 59)
F = "C:/Windows/Fonts/"
big = ImageFont.truetype(F + "segoeuil.ttf", 62 * S)
dollar = ImageFont.truetype(F + "segoeuil.ttf", 32 * S)
mono = ImageFont.truetype(F + "consola.ttf", 11 * S)
mono_b = ImageFont.truetype(F + "consolab.ttf", 12 * S)
sans = ImageFont.truetype(F + "segoeui.ttf", 12 * S)

total = sum(p["usd"] for p in P)
cash = D["starting_cash"] - total
ease = lambda t: 1 - (1 - max(0.0, min(1.0, t))) ** 4

FRAMES, DT = 8, 180                       # 64 frames at 40 ms = 2.6 s, then a hold
frames = []
for f in range(FRAMES + 1):
    t = f / FRAMES
    im = Image.new("RGB", (w, h), PAPER)
    d = ImageDraw.Draw(im)
    # hero number
    n = int(round(total * ease((t + 0.15) / 0.35)))
    txt = f"{n:,}"
    d.text((40 * S, 18 * S), "$", font=dollar, fill=INK3)
    d.text((62 * S, 0 * S), txt, font=big, fill=INK)
    sub_a = ease((t + 0.1) / 0.3)
    col = tuple(int(PAPER[i] + (INK2[i] - PAPER[i]) * sub_a) for i in range(3))
    d.text((40 * S, 84 * S), f"{len(P)} names   ${cash:,.0f} kept as cash   bought September to November", font=mono, fill=col)
    # position bars, one column per pick plus cash
    items = [("cash", cash, True)] + [(p["ticker"], p["usd"], False) for p in P]
    left, right, base, top = 40 * S, (W - 40) * S, (H - 30) * S, 112 * S
    gap = 8 * S
    cw = (right - left - gap * (len(items) - 1)) / len(items)
    maxv = max(v for _, v, _ in items)
    for i, (name, v, is_cash) in enumerate(items):
        a = ease((t - 0.22 - i * 0.03) / 0.35)
        x0 = left + i * (cw + gap)
        full = (base - top) * (v / maxv)
        y0 = base - full * a
        d.rectangle([x0, base - full, x0 + cw, base], fill=TRACK)
        if a > 0:
            d.rectangle([x0, y0, x0 + cw, base], fill=(200, 200, 196) if is_cash else INK)
        lab_a = ease((t - 0.4 - i * 0.03) / 0.25)
        lc = tuple(int(PAPER[k] + (INK2[k] - PAPER[k]) * lab_a) for k in range(3))
        d.text((x0 + cw / 2, base + 6 * S), name, font=mono_b if not is_cash else mono, fill=lc, anchor="ma")
        vc = tuple(int(PAPER[k] + (INK3[k] - PAPER[k]) * lab_a) for k in range(3))
        d.text((x0 + cw / 2, base - full - 5 * S), f"{v / 1000:.0f}k", font=mono, fill=vc, anchor="md")
    # baseline rule draws in
    d.line([left, base, right, base], fill=INK, width=1 * S)
    frames.append(im.resize((W, H), Image.LANCZOS).quantize(colors=6, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE))

durations = [DT] * len(frames)
durations[-1] = 4000
out = DASHBOARD_DIR / "email_hero.gif"
frames[0].save(out, save_all=True, append_images=frames[1:], duration=durations, loop=1, optimize=True, disposal=1)
print("wrote", out, out.stat().st_size // 1024, "KB", len(frames), "frames")
