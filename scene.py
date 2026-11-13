import math
import os
import random

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

FONTS = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")


def police(nom, taille):
    try:
        return ImageFont.truetype(os.path.join(FONTS, nom), max(6, int(taille)))
    except OSError:
        return ImageFont.load_default()


def degrade(w, h, arrets):
    col = Image.new("RGB", (1, h))
    px = col.load()
    for y in range(h):
        t = y / max(1, h - 1)
        for (p0, c0), (p1, c1) in zip(arrets, arrets[1:]):
            if p0 <= t <= p1:
                u = (t - p0) / max(1e-6, p1 - p0)
                px[0, y] = tuple(int(c0[k] + (c1[k] - c0[k]) * u) for k in range(3))
                break
        else:
            px[0, y] = arrets[-1][1]
    return col.resize((w, h))


def lueur(img, cx, cy, rx, couleur, force=1.0, ry=None):
    ry = ry or rx
    pad_x, pad_y = int(rx * 2.2) + 2, int(ry * 2.2) + 2
    m = Image.new("L", (pad_x * 2, pad_y * 2), 0)
    ImageDraw.Draw(m).ellipse((pad_x - rx, pad_y - ry, pad_x + rx, pad_y + ry), fill=int(255 * min(1.0, force)))
    m = m.filter(ImageFilter.GaussianBlur(max(1, min(rx, ry) * 0.55)))
    box = (int(cx) - pad_x, int(cy) - pad_y, int(cx) + pad_x, int(cy) + pad_y)
    x0, y0 = max(0, box[0]), max(0, box[1])
    x1, y1 = min(img.width, box[2]), min(img.height, box[3])
    if x1 <= x0 or y1 <= y0:
        return
    m = m.crop((x0 - box[0], y0 - box[1], x1 - box[0], y1 - box[1]))
    add = ImageChops.multiply(Image.new("RGB", m.size, couleur), Image.merge("RGB", (m, m, m)))
    zone = img.crop((x0, y0, x1, y1))
    img.paste(ImageChops.add(zone, add), (x0, y0))


def lueur_rgba(img, cx, cy, r, couleur, alpha=150, ry=None):
    ry = ry or r
    pad_x, pad_y = int(r * 2.2) + 2, int(ry * 2.2) + 2
    m = Image.new("L", (pad_x * 2, pad_y * 2), 0)
    ImageDraw.Draw(m).ellipse((pad_x - r, pad_y - ry, pad_x + r, pad_y + ry), fill=alpha)
    m = m.filter(ImageFilter.GaussianBlur(max(1, min(r, ry) * 0.55)))
    couche = Image.new("RGBA", m.size, couleur + (0,))
    couche.putalpha(m)
    _composite_clip(img, couche, int(cx) - pad_x, int(cy) - pad_y)


def _composite_clip(img, couche, x, y):
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(img.width, x + couche.width), min(img.height, y + couche.height)
    if x1 > x0 and y1 > y0:
        img.alpha_composite(couche.crop((x0 - x, y0 - y, x1 - x, y1 - y)), (x0, y0))


def melange(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def pluie(av, s, rnd, n=700, alpha=(26, 60)):
    couche = Image.new("RGBA", av.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(couche)
    for _ in range(n):
        x, y = rnd.uniform(0, av.width), rnd.uniform(0, av.height)
        lg = rnd.uniform(14, 34) * s
        c = rnd.choice([(150, 166, 188), (170, 184, 204), (128, 142, 164)])
        d.line([(x, y), (x - lg * 0.23, y + lg)], fill=c + (rnd.randint(*alpha),), width=1)
    av.alpha_composite(couche)


def _ville(img, W, H, s, horizon, rnd, zone_calme):
    d = ImageDraw.Draw(img)
    lx, ly = int(W * 0.83), int(H * 0.12)
    lueur(img, lx, ly, int(70 * s), (60, 60, 80), 0.9)
    d.ellipse((lx - 26 * s, ly - 26 * s, lx + 26 * s, ly + 26 * s), fill=(214, 218, 230))
    d.ellipse((lx - 14 * s, ly - 20 * s, lx - 2 * s, ly - 8 * s), fill=(196, 200, 214))
    for plan, (couleur, hmin, hmax) in enumerate((((20, 19, 36), 0.10, 0.26), ((11, 12, 21), 0.06, 0.30))):
        x = -rnd.randint(0, 80)
        while x < W:
            larg = int(rnd.randint(70, 190) * s)
            haut = int(H * rnd.uniform(hmin, hmax))
            top = horizon - haut
            d.rectangle((x, top, x + larg, horizon), fill=couleur)
            if plan == 1:
                if rnd.random() < 0.3:
                    d.rectangle((x + larg // 2 - 2, top - 26 * s, x + larg // 2 + 2, top), fill=couleur)
                    if rnd.random() < 0.5:
                        lueur(img, x + larg // 2, top - 26 * s, int(6 * s), (160, 30, 30), 0.8)
                fx, fy = int(10 * s), int(14 * s)
                for wy in range(top + int(10 * s), horizon - int(8 * s), fy + int(8 * s)):
                    for wx in range(x + int(8 * s), x + larg - fx - int(4 * s), fx + int(8 * s)):
                        if rnd.random() < 0.26:
                            c = rnd.choice([(255, 196, 112), (255, 222, 160), (236, 176, 108), (190, 150, 110)])
                            if not zone_calme(wx, wy) and rnd.random() < 0.12:
                                c = (120, 150, 235)
                            d.rectangle((wx, wy, wx + fx, wy + fy), fill=melange(c, couleur, 0.25))
            x += larg + int(rnd.randint(0, 24) * s)
    f = police("bahnschrift.ttf", 46 * s)
    for texte, couleur, fx in (("MOTEL", (255, 46, 136), 0.09), ("24/7", (0, 214, 255), 0.70), ("BAR", (255, 140, 40), 0.92)):
        x, y = int(W * fx), int(horizon - H * 0.17)
        lueur(img, x + 60 * s, y + 24 * s, int(110 * s), melange(couleur, (0, 0, 0), 0.55), 0.8, ry=int(50 * s))
        d.text((x, y), texte, font=f, fill=couleur)


def _rue_du_fond(img, W, H, s, horizon, bas):
    d = ImageDraw.Draw(img)
    d.rectangle((0, horizon, W, bas), fill=(17, 17, 23))
    d.rectangle((0, horizon, W, horizon + 5 * s), fill=(30, 30, 38))
    for x in range(0, W, int(150 * s)):
        d.rectangle((x, (horizon + bas) // 2, x + 70 * s, (horizon + bas) // 2 + 2 * s), fill=(70, 66, 50))
    for x in (0.18, 0.42, 0.66, 0.95):
        px = int(W * x)
        d.rectangle((px - 2 * s, horizon - 90 * s, px + 2 * s, horizon), fill=(26, 27, 34))
        lueur(img, px, horizon - 90 * s, int(40 * s), (150, 100, 40), 0.9)
        d.ellipse((px - 5 * s, horizon - 95 * s, px + 5 * s, horizon - 85 * s), fill=(255, 214, 150))


def _sol_mouille(img, W, H, s, haut, reflets):
    d = ImageDraw.Draw(img)
    d.rectangle((0, haut, W, H), fill=(13, 13, 18))
    for x, couleur in reflets:
        m = Image.new("L", (int(60 * s), H - haut), 0)
        dm = ImageDraw.Draw(m)
        for i in range(0, m.height, int(6 * s)):
            a = int(110 * (1 - i / m.height) * random.uniform(0.4, 1.0))
            dm.rectangle((m.width // 2 - random.randint(4, 14), i, m.width // 2 + random.randint(4, 14), i + int(3 * s)), fill=a)
        m = m.filter(ImageFilter.GaussianBlur(3 * s))
        add = ImageChops.multiply(Image.new("RGB", m.size, couleur), Image.merge("RGB", (m, m, m)))
        box = (int(x - m.width // 2), haut)
        if box[0] < 0 or box[0] + m.width > W:
            continue
        zone = img.crop((box[0], box[1], box[0] + m.width, box[1] + m.height))
        img.paste(ImageChops.add(zone, add), box)


def _personnage(av, x, sol, s, H):
    d = ImageDraw.Draw(av)
    sombre, lis = (27, 30, 42, 255), (150, 84, 190, 255)
    tete = (x - 46 * s, sol - 470 * s, x + 46 * s, sol - 370 * s)
    corps = [(x - 120 * s, H + 10), (x - 112 * s, sol - 300 * s), (x - 70 * s, sol - 365 * s),
             (x + 70 * s, sol - 365 * s), (x + 118 * s, sol - 300 * s), (x + 126 * s, H + 10)]
    bras = [(x + 90 * s, sol - 320 * s), (x + 190 * s, sol - 250 * s), (x + 205 * s, sol - 232 * s),
            (x + 180 * s, sol - 215 * s), (x + 80 * s, sol - 260 * s)]
    for poly, dec in ((corps, 3), (bras, 3)):
        d.polygon([(px + dec * s, py) for px, py in poly], fill=lis)
    d.ellipse((tete[0] + 3 * s, tete[1], tete[2] + 3 * s, tete[3]), fill=lis)
    d.polygon(corps, fill=sombre)
    d.polygon(bras, fill=sombre)
    d.ellipse(tete, fill=sombre)
    d.line([(x, sol - 360 * s), (x, sol - 120 * s)], fill=(16, 17, 24, 255), width=int(3 * s))
    d.chord((tete[0] - 10 * s, tete[1] + 30 * s, tete[2] + 10 * s, tete[3] + 50 * s), 180, 360, fill=(36, 40, 56, 255))
    d.ellipse((tete[0] + 8 * s, tete[1] + 6 * s, tete[2] - 8 * s, tete[3] - 4 * s), fill=(22, 24, 34, 255))


def _lampadaire(av, x, sol, s, haut):
    d = ImageDraw.Draw(av)
    d.rectangle((x - 7 * s, haut, x + 7 * s, sol), fill=(22, 23, 30, 255))
    d.rectangle((x - 7 * s, haut, x + 90 * s, haut + 10 * s), fill=(22, 23, 30, 255))
    lueur_rgba(av, x + 80 * s, haut + 30 * s, int(150 * s), (255, 176, 90), 70)
    d.polygon([(x + 60 * s, haut + 10 * s), (x + 100 * s, haut + 10 * s), (x + 92 * s, haut + 22 * s), (x + 68 * s, haut + 22 * s)],
              fill=(255, 222, 170, 255))


def _berline(av, x0, sol, L, s):
    d = ImageDraw.Draw(av)
    Hc = 0.30 * L
    P = lambda u, v: (x0 + u * L, sol - v * Hc)
    caisse = [P(0.01, 0.20), P(0.0, 0.42), P(0.03, 0.58), P(0.20, 0.63), P(0.31, 0.93), P(0.41, 1.0),
              P(0.62, 1.0), P(0.745, 0.67), P(0.92, 0.61), P(0.995, 0.53), P(1.0, 0.30), P(0.985, 0.18)]
    d.polygon(caisse, fill=(24, 26, 33, 255))
    d.line([P(0.04, 0.55), P(0.20, 0.60), P(0.74, 0.64), P(0.93, 0.585)], fill=(70, 76, 96, 255), width=int(3 * s))
    d.line([P(0.03, 0.30), P(0.98, 0.30)], fill=(16, 17, 22, 255), width=int(5 * s))
    vitre = [P(0.315, 0.66), P(0.33, 0.89), P(0.415, 0.955), P(0.61, 0.955), P(0.725, 0.67)]
    d.polygon(vitre, fill=(10, 14, 22, 120))
    d.line([P(0.515, 0.66), P(0.515, 0.955)], fill=(14, 15, 20, 255), width=int(14 * s))
    d.line(vitre + [vitre[0]], fill=(14, 15, 20, 255), width=int(5 * s))
    for u0, u1 in ((0.33, 0.515), (0.515, 0.735)):
        d.line([P(u0, 0.20), P(u0, 0.66)], fill=(12, 13, 17, 255), width=int(3 * s))
        d.line([P(u1, 0.20), P(u1, 0.66)], fill=(12, 13, 17, 255), width=int(3 * s))
    for u in (0.475, 0.695):
        xh, yh = P(u, 0.56)
        d.rounded_rectangle((xh - 22 * s, yh - 5 * s, xh + 8 * s, yh + 5 * s), radius=int(4 * s), fill=(90, 96, 112, 255))
    xm, ym = P(0.745, 0.70)
    d.polygon([(xm, ym), (xm + 34 * s, ym - 6 * s), (xm + 38 * s, ym + 14 * s), (xm, ym + 16 * s)], fill=(20, 22, 28, 255))
    xf, yf = P(0.985, 0.48)
    d.polygon([(xf - 40 * s, yf - 10 * s), (xf + 10 * s, yf - 14 * s), (xf + 12 * s, yf + 6 * s), (xf - 36 * s, yf + 6 * s)],
              fill=(210, 220, 235, 255))
    xr, yr = P(0.012, 0.50)
    d.polygon([(xr, yr - 12 * s), (xr + 42 * s, yr - 10 * s), (xr + 40 * s, yr + 8 * s), (xr + 2 * s, yr + 8 * s)],
              fill=(150, 18, 24, 255))
    r = 0.068 * L
    for u in (0.19, 0.80):
        cx, cy = P(u, 0)[0], sol - r
        d.ellipse((cx - r * 1.12, cy - r * 1.12, cx + r * 1.12, cy + r * 1.12), fill=(6, 6, 9, 255))
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(14, 14, 17, 255))
        d.ellipse((cx - r * 0.62, cy - r * 0.62, cx + r * 0.62, cy + r * 0.62), fill=(64, 68, 78, 255))
        for k in range(5):
            a = k * 2 * math.pi / 5
            d.line([(cx, cy), (cx + math.cos(a) * r * 0.58, cy + math.sin(a) * r * 0.58)], fill=(34, 36, 42, 255), width=int(7 * s))
        d.ellipse((cx - r * 0.14, cy - r * 0.14, cx + r * 0.14, cy + r * 0.14), fill=(110, 114, 124, 255))
    return {"clignotants": [P(0.99, 0.40), P(0.02, 0.40)], "plaque": P(0.96, 0.33)}


def scene_vehicule(W, H, seed=None):
    rnd = random.Random(seed)
    s = H / 1080.0
    cx, cy = W // 2, H // 2
    horizon, bas_fond = int(0.43 * H), int(0.52 * H)
    calme = lambda x, y: abs(x - cx) < 260 * s and abs(y - cy) < 220 * s
    fond = degrade(W, H, [(0, (5, 6, 13)), (0.30, (14, 12, 30)), (0.43, (52, 28, 62)), (0.44, (18, 16, 26)), (1, (12, 12, 16))])
    _ville(fond, W, H, s, horizon, rnd, calme)
    _rue_du_fond(fond, W, H, s, horizon, bas_fond)

    av = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sol = int(cy + 0.55 * 0.30 * 1250 * s)
    sol_img = Image.new("RGB", (W, H - bas_fond), (13, 13, 18))
    _sol_mouille(sol_img, W, H - bas_fond, s, 0, [(W * 0.18, (90, 60, 30)), (W * 0.42, (90, 60, 30)),
                                                   (W * 0.66, (90, 60, 30)), (W * 0.86, (120, 80, 40)),
                                                   (W * 0.09, (110, 20, 60)), (W * 0.72, (0, 70, 90))])
    av.paste(sol_img.convert("RGBA"), (0, bas_fond))
    d = ImageDraw.Draw(av)
    d.rectangle((0, bas_fond, W, bas_fond + 10 * s), fill=(36, 36, 44, 255))
    L = 1250 * s
    x0 = cx - 0.62 * L
    car = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    pos = _berline(car, x0, sol, L, s)
    reflet = car.crop((0, int(sol - 0.3 * L), W, sol)).transpose(Image.FLIP_TOP_BOTTOM)
    a = reflet.getchannel("A").point(lambda v: v * 0.22)
    reflet.putalpha(a)
    reflet = reflet.filter(ImageFilter.GaussianBlur(4 * s))
    _composite_clip(av, reflet, 0, sol)
    ombre = Image.new("RGBA", (int(L * 1.05), int(40 * s)), (0, 0, 0, 0))
    ImageDraw.Draw(ombre).ellipse((0, 0, ombre.width, ombre.height), fill=(0, 0, 0, 200))
    ombre = ombre.filter(ImageFilter.GaussianBlur(8 * s))
    _composite_clip(av, ombre, int(x0 - L * 0.025), int(sol - 20 * s))
    av.alpha_composite(car)
    px, py = pos["plaque"]
    d = ImageDraw.Draw(av)
    d.rectangle((px - 44 * s, py - 12 * s, px + 10 * s, py + 12 * s), fill=(220, 222, 226, 255))
    d.text((px - 40 * s, py - 10 * s), "84KRX", font=police("bahnschrift.ttf", 17 * s), fill=(20, 30, 90, 255))
    _lampadaire(av, int(W * 0.90), H, s, int(H * 0.30))
    _personnage(av, int(cx - 330 * s), H + int(60 * s), s, H)
    pluie(av, s, rnd)
    return fond, av, {"voies": [int(horizon + 0.026 * H), int(horizon + 0.038 * H)],
                      "clignotants": pos["clignotants"], "titre": "Berline · 84-KRX-27"}


def scene_porte(W, H, seed=None):
    rnd = random.Random(seed)
    s = H / 1080.0
    cx, cy = W // 2, H // 2
    fond = degrade(W, H, [(0, (6, 7, 14)), (0.40, (20, 16, 34)), (0.46, (36, 24, 46)), (0.47, (16, 15, 22)), (1, (10, 10, 14))])
    horizon = int(0.47 * H)
    _ville(fond, W, H, s, horizon, rnd, lambda x, y: abs(x - cx) < 260 * s and abs(y - cy) < 220 * s)
    _rue_du_fond(fond, W, H, s, horizon, int(0.56 * H))

    av = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(av)
    mur = Image.new("RGB", (W, H), (34, 22, 20))
    dm = ImageDraw.Draw(mur)
    bh, bw = int(26 * s), int(70 * s)
    for i, y in enumerate(range(0, H, bh)):
        dec = (bw // 2) if i % 2 else 0
        for x in range(-dec, W, bw):
            c = rnd.choice([(46, 28, 24), (52, 32, 26), (40, 25, 22), (58, 36, 28)])
            dm.rectangle((x + 2, y + 2, x + bw - 2, y + bh - 2), fill=c)
    mur = mur.filter(ImageFilter.GaussianBlur(0.6))
    av.paste(mur.convert("RGBA"))
    d = ImageDraw.Draw(av)
    pl, pr = int(cx - 175 * s), int(cx + 175 * s)
    pt, pb = int(cy - 340 * s), int(cy + 420 * s)
    d.rectangle((pl - 26 * s, pt - 26 * s, pr + 26 * s, pb), fill=(26, 20, 18, 255))
    d.rectangle((pl, pt, pr, pb), fill=(58, 36, 24, 255))
    for (a, b, c_, e) in ((pl + 30 * s, pt + 30 * s, pr - 30 * s, cy - 150 * s),
                          (pl + 30 * s, cy + 160 * s, cx - 12 * s, pb - 40 * s),
                          (cx + 12 * s, cy + 160 * s, pr - 30 * s, pb - 40 * s)):
        d.rectangle((a, b, c_, e), outline=(40, 25, 17, 255), width=int(6 * s))
    vt, vb = int(cy - 120 * s), int(cy + 130 * s)
    d.rectangle((pl + 30 * s, vt, pr - 30 * s, vb), fill=(12, 16, 26, 105))
    d.rectangle((pl + 30 * s, vt, pr - 30 * s, vb), outline=(36, 23, 16, 255), width=int(8 * s))
    d.line([(cx, vt), (cx, vb)], fill=(36, 23, 16, 255), width=int(6 * s))
    hx, hy = int(pr - 50 * s), int(cy + 175 * s)
    d.rounded_rectangle((hx - 10 * s, hy - 40 * s, hx + 10 * s, hy + 40 * s), radius=int(6 * s), fill=(150, 116, 52, 255))
    d.ellipse((hx - 7 * s, hy + 50 * s, hx + 7 * s, hy + 64 * s), fill=(176, 138, 64, 255))
    d.rounded_rectangle((pr + 60 * s, cy - 250 * s, pr + 140 * s, cy - 190 * s), radius=int(6 * s), fill=(20, 20, 24, 255))
    d.text((pr + 78 * s, cy - 250 * s), "12", font=police("bahnschrift.ttf", 44 * s), fill=(210, 190, 140, 255))
    lueur_rgba(av, cx, pt - 70 * s, int(230 * s), (255, 170, 80), 85)
    d.rectangle((cx - 28 * s, pt - 96 * s, cx + 28 * s, pt - 50 * s), fill=(30, 26, 22, 255))
    d.rectangle((cx - 20 * s, pt - 88 * s, cx + 20 * s, pt - 58 * s), fill=(255, 220, 160, 255))
    d.rectangle((pl - 220 * s, cy + 40 * s, pl - 110 * s, cy + 120 * s), fill=(24, 26, 30, 255))
    d.rectangle((pl - 220 * s, cy + 40 * s, pl - 110 * s, cy + 56 * s), fill=(34, 36, 42, 255))
    for i in range(3):
        y = pb + i * 34 * s
        d.rectangle((pl - 80 * s - i * 30 * s, y, pr + 80 * s + i * 30 * s, y + 34 * s),
                    fill=(52 + i * 6, 50 + i * 6, 54 + i * 6, 255))
    for fx, c in ((cx - 640 * s, (120, 70, 30)), (cx + 470 * s, (24, 30, 60))):
        d.rectangle((fx, cy - 260 * s, fx + 220 * s, cy + 60 * s), fill=(18, 14, 14, 255))
        d.rectangle((fx + 14 * s, cy - 246 * s, fx + 206 * s, cy + 46 * s), fill=c + (255,))
        d.line([(fx + 110 * s, cy - 246 * s), (fx + 110 * s, cy + 46 * s)], fill=(18, 14, 14, 255), width=int(8 * s))
    for k in range(9):
        a = -math.pi / 2 + (k - 4) * 0.28
        x1, y1 = pr + 190 * s, pb - 10 * s
        d.line([(x1, y1), (x1 + math.cos(a) * 110 * s, y1 + math.sin(a) * 120 * s)], fill=(28, 70, 36, 255), width=int(9 * s))
    d.rectangle((pr + 160 * s, pb - 10 * s, pr + 220 * s, pb + 60 * s), fill=(70, 40, 30, 255))
    _personnage(av, int(cx - 470 * s), H + int(160 * s), s, H)
    pluie(av, s, rnd, n=350, alpha=(18, 40))
    return fond, av, {"voies": [int(cy - 70 * s), int(cy - 20 * s)],
                      "tele": (int(cx + 484 * s), int(cy - 246 * s), int(cx + 676 * s), int(cy + 46 * s)),
                      "titre": "Porte d'entrée · 12 rue des Lilas"}


def minimap(r, s, seed=None):
    rnd = random.Random(seed)
    t = int(r * 2)
    img = Image.new("RGBA", (t, t), (0, 0, 0, 0))
    carte = Image.new("RGB", (t, t), (14, 20, 30))
    d = ImageDraw.Draw(carte)
    for _ in range(9):
        for horiz in (True, False):
            p = rnd.randint(0, t)
            w = rnd.choice([3, 4, 6, 9])
            if horiz:
                d.line([(0, p), (t, p + rnd.randint(-30, 30))], fill=(46, 58, 74), width=int(w * s))
            else:
                d.line([(p, 0), (p + rnd.randint(-30, 30), t)], fill=(46, 58, 74), width=int(w * s))
    for _ in range(14):
        x, y = rnd.randint(0, t), rnd.randint(0, t)
        d.rectangle((x, y, x + rnd.randint(14, 40), y + rnd.randint(14, 40)), fill=(22, 30, 42))
    d.line([(r, r), (r + 40 * s, r - 30 * s), (r + 90 * s, r - 32 * s), (r + 120 * s, r - 90 * s)],
           fill=(170, 80, 240), width=int(6 * s))
    masque = Image.new("L", (t, t), 0)
    ImageDraw.Draw(masque).ellipse((0, 0, t - 1, t - 1), fill=255)
    img.paste(carte, (0, 0), masque)
    d = ImageDraw.Draw(img)
    d.ellipse((1, 1, t - 2, t - 2), outline=(10, 10, 14, 255), width=int(5 * s))
    d.polygon([(r, r - 14 * s), (r + 10 * s, r + 12 * s), (r, r + 6 * s), (r - 10 * s, r + 12 * s)], fill=(245, 245, 250, 255))
    d.text((r - 7 * s, 6 * s), "N", font=police("seguisb.ttf", 18 * s), fill=(235, 235, 240, 255))
    return img
