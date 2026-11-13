import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import platform
import sys
import threading
import time
from collections import Counter, deque
from datetime import datetime

import cv2
import mss
import numpy as np

MSS = getattr(mss, "MSS", None) or mss.mss

VERSION = "2.0"
BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")
TEMPLATES_DIR = os.path.join(BASE, "templates")
A_RENOMMER = "a_renommer"


def set_profil(nom):
    global CONFIG_PATH, TEMPLATES_DIR
    if nom:
        CONFIG_PATH = os.path.join(BASE, f"config_{nom}.json")
        TEMPLATES_DIR = os.path.join(BASE, f"templates_{nom}")


def dossier_modeles():
    return os.path.basename(TEMPLATES_DIR)


DEFAULT_CONFIG = {
    "zone": None,
    "attendre_barre": True,
    "touche_fixe": None,
    "seuil": 0.75,
    "appui_ms": 20,
    "latence_ms": 25,
    "cible": 0.35,
    "marge_deg": 3,
    "absence_ms": 40,
    "taille_lettre": 0.20,
    "anneau": [0.55, 1.05],
    "anneau_auto": True,
    "fenetre_cible": None,
    "apprentissage_auto": True,
    "journal": True,
    "captures_echec": True,
    "bleu_hsv": [[90, 100, 100], [130, 255, 255]],
    "rouge_hsv": [[0, 100, 90], [10, 255, 255], [168, 100, 90], [179, 255, 255]],
    "vert_hsv": [[45, 90, 90], [85, 255, 255]],
    "min_pixels_bleu": 20,
    "min_pixels_rouge": 5,
    "binaire": None,
    "pause_boucle_ms": 0,
    "capture": "auto",
    "touche_on_off": "F6",
    "touche_detecter": "F7",
    "touche_capture": "F8",
    "touche_quitter": "F10",
}

VK = {chr(c): c for c in range(ord("A"), ord("Z") + 1)}
VK.update({str(d): ord(str(d)) for d in range(10)})
VK.update({
    "SPACE": 0x20, "ESPACE": 0x20, "ENTER": 0x0D, "ENTREE": 0x0D, "TAB": 0x09,
    "ESC": 0x1B, "BACKSPACE": 0x08, "SHIFT": 0x10, "CTRL": 0x11, "ALT": 0x12,
    "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28,
    "GAUCHE": 0x25, "HAUT": 0x26, "DROITE": 0x27, "BAS": 0x28,
})
VK.update({f"F{i}": 0x6F + i for i in range(1, 13)})
EXTENDED_VK = {0x25, 0x26, 0x27, 0x28}

CAPTURABLE = [(chr(c), c) for c in range(ord("A"), ord("Z") + 1)]
CAPTURABLE += [(str(d), ord(str(d))) for d in range(10)]
CAPTURABLE += [("SPACE", 0x20), ("ENTER", 0x0D), ("TAB", 0x09),
               ("LEFT", 0x25), ("UP", 0x26), ("RIGHT", 0x27), ("DOWN", 0x28)]

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.SendInput.argtypes = (wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wt.UINT
user32.MapVirtualKeyW.argtypes = (wt.UINT, wt.UINT)
user32.MapVirtualKeyW.restype = wt.UINT
user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowTextW.argtypes = (wt.HWND, wt.LPWSTR, ctypes.c_int)


def titre_premier_plan():
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(user32.GetForegroundWindow(), buf, 256)
    return buf.value


def _send(scan, flags):
    inp = INPUT(type=INPUT_KEYBOARD,
                u=_INPUTUNION(ki=KEYBDINPUT(0, scan, flags | KEYEVENTF_SCANCODE, 0, 0)))
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def appuyer(vk, duree_ms):
    scan = user32.MapVirtualKeyW(vk, 0)
    ext = KEYEVENTF_EXTENDEDKEY if vk in EXTENDED_VK else 0
    _send(scan, ext)
    if duree_ms > 0:
        time.sleep(duree_ms / 1000.0)
    _send(scan, ext | KEYEVENTF_KEYUP)


class Raccourcis:

    def __init__(self):
        self.etat = {}

    def appui(self, vk):
        bas = bool(user32.GetAsyncKeyState(vk) & 0x8000)
        avant = self.etat.get(vk, False)
        self.etat[vk] = bas
        return bas and not avant

    def synchro(self, vks):
        for vk in vks:
            self.etat[vk] = bool(user32.GetAsyncKeyState(vk) & 0x8000)


def charger_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


def sauver_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def lire_image(chemin, flag=cv2.IMREAD_GRAYSCALE):
    return cv2.imdecode(np.fromfile(chemin, dtype=np.uint8), flag)


def ecrire_image(chemin, img):
    ok, buf = cv2.imencode(".png", img)
    if ok:
        buf.tofile(chemin)
    return ok


def pretraiter(gris, cfg):
    b = cfg.get("binaire")
    if b is not None:
        _, gris = cv2.threshold(gris, int(b), 255, cv2.THRESH_BINARY)
    return gris


def rogner_modele(gris):
    h, w = gris.shape[:2]
    m = max(2, int(round(min(h, w) * 0.12)))
    return gris[m:h - m, m:w - m].copy()


def lettre_plausible(gris):
    return gris is not None and float(np.median(gris)) < 90 and int(gris.max()) > 150


def nom_touche_depuis_fichier(fichier):
    return os.path.splitext(fichier)[0].split("_")[0].upper()


def charger_modeles(cfg, taille_max, log=print):
    os.makedirs(TEMPLATES_DIR, exist_ok=True)
    modeles = []
    for f in sorted(os.listdir(TEMPLATES_DIR)):
        if not f.lower().endswith(".png"):
            continue
        nom = nom_touche_depuis_fichier(f)
        if nom not in VK:
            log(f"  ! {f} ignoré : touche '{nom}' inconnue")
            continue
        img = lire_image(os.path.join(TEMPLATES_DIR, f))
        if img is None:
            log(f"  ! {f} illisible")
            continue
        if img.shape[0] > taille_max[0] or img.shape[1] > taille_max[1]:
            log(f"  ! {f} ignoré : plus grand que la zone lettre (zone changée ? réapprends-la)")
            continue
        modeles.append((nom, VK[nom], pretraiter(img, cfg), f))
    return modeles


def sauver_modele(nom, gris):
    os.makedirs(TEMPLATES_DIR, exist_ok=True)
    fichier, i = f"{nom}.png", 2
    while os.path.exists(os.path.join(TEMPLATES_DIR, fichier)):
        fichier, i = f"{nom}_{i}.png", i + 1
    ecrire_image(os.path.join(TEMPLATES_DIR, fichier), gris)
    return fichier


def lettres_a_nommer():
    d = os.path.join(TEMPLATES_DIR, A_RENOMMER)
    if not os.path.isdir(d):
        return []
    return sorted(os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith(".png"))


def nommer_inconnue(chemin, nom):
    nom = (nom or "").strip().upper()
    if nom:
        if nom not in VK:
            raise ValueError(f"touche '{nom}' inconnue")
        img = lire_image(chemin)
        if img is not None:
            sauver_modele(nom, rogner_modele(img))
    os.remove(chemin)


def meilleur_match(gris, modeles):
    best = (None, None, -1.0)
    for nom, vk, tpl, _ in modeles:
        res = cv2.matchTemplate(gris, tpl, cv2.TM_CCOEFF_NORMED)
        score = float(cv2.minMaxLoc(res)[1])
        if not np.isfinite(score):
            score = 0.0
        if score > best[2]:
            best = (nom, vk, score)
    return best


def wrap(a):
    return (a + 180.0) % 360.0 - 180.0


def _plages(cfg, cle):
    v = cfg[cle]
    return [(np.array(v[i], np.uint8), np.array(v[i + 1], np.uint8)) for i in range(0, len(v) - 1, 2)]


def _masque(hsv, plages):
    m = None
    for lo, hi in plages:
        x = cv2.inRange(hsv, lo, hi)
        m = x if m is None else cv2.bitwise_or(m, x)
    return m


def detecter_cercle(img, cfg, centre_recherche=None, rayon_recherche=None):
    if img.ndim == 3 and img.shape[2] == 4:
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    H, W = img.shape[:2]
    cx0, cy0 = centre_recherche or (W // 2, H // 2)
    s = int(rayon_recherche or min(H, W) * 0.3)
    x0, y0 = max(0, int(cx0 - s)), max(0, int(cy0 - s))
    crop = img[y0:int(cy0 + s), x0:int(cx0 + s)]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    bleu = _masque(hsv, _plages(cfg, "bleu_hsv")) > 0
    rouge = _masque(hsv, _plages(cfg, "rouge_hsv")) > 0
    yb, xb = np.nonzero(bleu)
    yr, xr = np.nonzero(rouge)
    if len(xb) < 15 or len(xr) < 4:
        return None

    gris = cv2.GaussianBlur(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), (5, 5), 1.5)
    rmin, rmax = int(min(H, W) * 0.025), int(min(H, W) * 0.17)
    cands = []
    for p2 in (35, 28, 22, 16):
        cs = cv2.HoughCircles(gris, cv2.HOUGH_GRADIENT, dp=1, minDist=6, param1=50, param2=p2,
                              minRadius=rmin, maxRadius=rmax)
        if cs is not None:
            cands += [tuple(map(float, c)) for c in cs[0][:20]]
    v = hsv[..., 2]
    best = None
    for x, y, R in cands:
        c = max(3, int(0.2 * R))
        cxi, cyi = int(round(x)), int(round(y))
        centre = v[max(0, cyi - c):cyi + c, max(0, cxi - c):cxi + c]
        if centre.size == 0 or not (np.median(centre) < 90 and centre.max() > 150):
            continue
        db = np.hypot(xb - x, yb - y) / R
        sel = (db > 0.6) & (db < 1.08)
        if np.count_nonzero(sel) < 15:
            continue
        ang = np.degrees(np.arctan2(yb[sel] - y, xb[sel] - x)) % 360.0
        bloc = Analyseur._bloc(ang.astype(np.float32))
        if bloc is None:
            continue
        nb = np.count_nonzero(bloc)
        etendue = np.ptp(wrap(ang[bloc] - Analyseur._moyenne(ang[bloc])))
        if nb < 15 or not 3 <= etendue <= 170 or np.std(db[sel][bloc]) > 0.08:
            continue
        dr = np.hypot(xr - x, yr - y) / R
        nr = np.count_nonzero((dr > 0.45) & (dr < 1.08))
        if nr < 4:
            continue
        score = nb + 3 * nr
        if best is None or score > best[0]:
            best = (score, x, y, R)
    if best is None:
        return None
    _, x, y, R = best

    blanc = (hsv[..., 2] > 180) & (hsv[..., 1] < 60)
    yw, xw = np.nonzero(blanc)
    if len(xw):
        sel = np.hypot(xw - x, yw - y) < 0.26 * R
        if np.count_nonzero(sel) >= 8:
            lx, ly = float(xw[sel].mean()), float(yw[sel].mean())
            if np.hypot(lx - x, ly - y) < 6:
                x, y = (x + lx) / 2, (y + ly) / 2

    def rayons(xs, ys, rmin, rmax):
        d = np.hypot(xs - x, ys - y)
        sel = (d > rmin * R) & (d < rmax * R)
        if np.count_nonzero(sel) < 3:
            return np.empty(0)
        ang = (np.degrees(np.arctan2(ys[sel] - y, xs[sel] - x)) % 360.0).astype(np.float32)
        bloc = Analyseur._bloc(ang)
        return d[sel][bloc] if bloc is not None else np.empty(0)

    db = rayons(xb, yb, 0.6, 1.08)
    dr = rayons(xr, yr, 0.45, 1.08)
    if len(db) < 10:
        return None
    r_in = float(np.percentile(db, 2)) - 1.5
    r_out = float(max(np.percentile(db, 98), np.percentile(dr, 98) if len(dr) else 0)) + 1.5
    return {"centre": (x + x0, y + y0), "rayon": float(max(R, r_out)), "anneau_px": (r_in, r_out)}


def _bande_pleine(rayons):
    h = np.bincount(np.round(rayons).astype(int))
    pic = int(np.argmax(h))
    ok = h >= 0.3 * h[pic]
    a = pic
    while a > 0 and ok[a - 1]:
        a -= 1
    b = pic
    while b + 1 < len(h) and ok[b + 1]:
        b += 1
    return float(a), float(b + 1)


def _traits_aiguille(nouveau):
    n, lab, stats, _ = cv2.connectedComponentsWithStats(nouveau, connectivity=8)
    traits = []
    for i in range(1, n):
        x, y, w, h, aire = stats[i]
        if not 10 <= aire <= 2500:
            continue
        ys, xs = np.nonzero(lab[y:y + h, x:x + w] == i)
        xs, ys = xs + x, ys + y
        mx, my = xs.mean(), ys.mean()
        cov = np.cov(np.vstack([xs - mx, ys - my]))
        vals, vecs = np.linalg.eigh(cov)
        if vals[0] <= 0 or vals[1] / max(vals[0], 1e-6) < 6:
            continue
        dx, dy = vecs[:, 1]
        if len(xs) > 60:
            k = np.linspace(0, len(xs) - 1, 60).astype(int)
            xs, ys = xs[k], ys[k]
        traits.append((mx, my, dx, dy, np.c_[xs, ys]))
    return traits


def detecter_cercle_anime(prendre, cfg, duree=1.2):
    rouge_pl, bleu_pl = _plages(cfg, "rouge_hsv"), _plages(cfg, "bleu_hsv")
    noyau = np.ones((3, 3), np.uint8)
    passe, traits, derniere, n = [], [], None, 0
    t0 = time.perf_counter()
    while True:
        t = time.perf_counter()
        if t - t0 >= duree:
            break
        hsv = cv2.cvtColor(cv2.cvtColor(prendre(), cv2.COLOR_BGRA2BGR), cv2.COLOR_BGR2HSV)
        rouge = _masque(hsv, rouge_pl)
        n += 1
        if cv2.countNonZero(_masque(hsv, bleu_pl)) > 15:
            derniere = hsv
        avant = None
        while passe and t - passe[0][0] >= 0.06:
            avant = passe.pop(0)[1]
        passe.append((t, rouge))
        if avant is not None:
            traits += _traits_aiguille(cv2.bitwise_and(rouge, cv2.bitwise_not(cv2.dilate(avant, noyau))))
    if n < 8 or derniere is None:
        return _rien("trop peu d'images ou pas de barre bleue")
    if len(traits) < 8:
        return _rien(f"pas assez de traits d'aiguille ({len(traits)})")

    P = np.array([(a[0], a[1]) for a in traits])
    N = np.array([(-a[3], a[2]) for a in traits])
    c_ = np.einsum("ij,ij->i", N, P)
    rng = np.random.default_rng(0)
    meilleur, m = None, len(traits)
    for _ in range(400):
        i, j = rng.choice(m, 2, replace=False)
        A = np.array([N[i], N[j]])
        if abs(np.linalg.det(A)) < 0.3:
            continue
        c = np.linalg.solve(A, [c_[i], c_[j]])
        ok = np.abs(N @ c - c_) < 3.0
        if meilleur is None or ok.sum() > meilleur.sum():
            meilleur = ok
    if meilleur is None or meilleur.sum() < 6:
        return _rien("les traits ne se croisent pas")
    Ni, ci = N[meilleur], c_[meilleur]
    cx, cy = np.linalg.solve(Ni.T @ Ni, Ni.T @ ci)
    rc = np.hypot(P[:, 0] - cx, P[:, 1] - cy)
    med = np.median(rc[meilleur])
    meilleur &= np.abs(rc - med) < 0.15 * med
    ang = np.degrees(np.arctan2(P[meilleur][:, 1] - cy, P[meilleur][:, 0] - cx)) % 360
    if meilleur.sum() < 6 or len(np.unique((ang // 15).astype(int))) < 6:
        return _rien("balayage trop court")
    pts = np.vstack([traits[k][4] for k in np.flatnonzero(meilleur)])
    ai_in, ai_out = _bande_pleine(np.hypot(pts[:, 0] - cx, pts[:, 1] - cy))
    r_in = ai_in + 0.4 * (ai_out - ai_in) - 2
    r_out = ai_out + 1
    v = derniere[..., 2]
    c = max(3, int(0.22 * r_out))
    centre = v[max(0, int(cy) - c):int(cy) + c, max(0, int(cx) - c):int(cx) + c]
    if centre.size == 0 or not (np.median(centre) < 90 and centre.max() > 150):
        return _rien(f"pas de lettre au centre trouvé ({cx:.0f}, {cy:.0f})")
    return {"centre": (float(cx), float(cy)), "rayon": r_out, "anneau_px": (r_in, r_out)}


DEBUG_DETECTION = []


def _rien(raison):
    DEBUG_DETECTION.append(raison)
    del DEBUG_DETECTION[:-50]
    return None


def zone_recherche(mon):
    s = int(min(mon["width"], mon["height"]) * 0.3)
    cx, cy = mon["left"] + mon["width"] // 2, mon["top"] + mon["height"] // 2
    return {"left": cx - s, "top": cy - s, "width": 2 * s, "height": 2 * s}


def trouver_cercle(sct, cfg, mon, duree=1.2):
    zr = zone_recherche(mon)
    det = detecter_cercle_anime(lambda: np.asarray(sct.grab(zr)), cfg, duree)
    if det:
        return appliquer_detection(cfg, det, (zr["left"], zr["top"]))
    det = detecter_cercle(np.asarray(sct.grab(mon)), cfg)
    if det:
        return appliquer_detection(cfg, det, (mon["left"], mon["top"]))
    return None


def appliquer_detection(cfg, det, decalage=(0, 0)):
    cx, cy = det["centre"][0] + decalage[0], det["centre"][1] + decalage[1]
    cote = int(np.ceil(2 * det["rayon"])) + 4
    ancienne = cfg.get("zone")
    if ancienne and abs(ancienne["width"] - cote) <= 12:
        cote = ancienne["width"]
    demi = cote / 2.0
    cfg["zone"] = {"left": int(round(cx - (cote - 1) / 2.0)), "top": int(round(cy - (cote - 1) / 2.0)),
                   "width": cote, "height": cote}
    r_in, r_out = det["anneau_px"]
    cfg["anneau"] = [round(max(0.2, r_in / demi), 3), round(min(1.2, r_out / demi), 3)]
    cfg["anneau_auto"] = False
    return cfg


class Analyseur:
    def __init__(self, cfg, h, w):
        self.cfg = cfg
        cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        dx, dy = xx - cx, yy - cy
        r = np.sqrt(dx * dx + dy * dy) / (min(h, w) / 2.0)
        ang = np.arctan2(dy, dx)
        self.angle = (np.degrees(ang) % 360.0).astype(np.float32)
        self.r = r
        self.rbin = np.clip((r * 50).astype(np.int32), 0, 99)
        self.set_anneau(*cfg["anneau"])
        c = max(4, int(round(min(h, w) * cfg["taille_lettre"])))
        y0, x0 = max(0, int(round(cy - c / 2))), max(0, int(round(cx - c / 2)))
        self.lettre_sl = (slice(y0, y0 + c), slice(x0, x0 + c))
        self.taille_lettre = (min(c, h - y0), min(c, w - x0))
        self.bleu = _plages(cfg, "bleu_hsv")
        self.rouge = _plages(cfg, "rouge_hsv")
        self.vert = _plages(cfg, "vert_hsv")
        self.min_b, self.min_r = int(cfg["min_pixels_bleu"]), int(cfg["min_pixels_rouge"])
        self.nb_bleu = self.nb_rouge = self.nb_vert = self.nb_barre = 0
        self.raison = None

    def set_anneau(self, r_in, r_out, calibre=False):
        self.anneau_r = (float(r_in), float(r_out))
        self.anneau = (((self.r >= r_in) & (self.r <= r_out)) * 255).astype(np.uint8)
        self.nb_anneau = max(1, cv2.countNonZero(self.anneau))
        self.calibre = calibre

    def calibrer(self, bgra):
        v = cv2.cvtColor(cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR), cv2.COLOR_BGR2HSV)[..., 2]
        ok = (self.r >= 0.35) & (self.r <= 1.15)
        tot = np.bincount(self.rbin[ok], minlength=100).astype(np.float64)
        dk = np.bincount(self.rbin[ok & (v < 70)], minlength=100)
        frac = np.where(tot >= 10, dk / np.maximum(tot, 1), 0.0)
        idx = np.flatnonzero(frac >= 0.75)
        if idx.size == 0:
            return None
        runs = np.split(idx, np.flatnonzero(np.diff(idx) != 1) + 1)
        run = max(runs, key=len)
        a, b = run[0] / 50.0, (run[-1] + 1) / 50.0
        if not (0.04 <= b - a <= 0.35 and 0.45 <= (a + b) / 2 <= 1.05):
            return None
        if b - a >= 0.10:
            a, b = a + 0.02, b - 0.02
        return [round(a, 3), round(b, 3)]

    def lettre(self, bgra):
        return cv2.cvtColor(bgra[self.lettre_sl], cv2.COLOR_BGRA2GRAY)

    def masques(self, bgra):
        hsv = cv2.cvtColor(cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR), cv2.COLOR_BGR2HSV)
        mb = cv2.bitwise_and(_masque(hsv, self.bleu), self.anneau)
        mr = cv2.bitwise_and(_masque(hsv, self.rouge), self.anneau)
        self.nb_vert = cv2.countNonZero(cv2.bitwise_and(_masque(hsv, self.vert), self.anneau))
        return mb, mr, hsv

    @staticmethod
    def _blocs(angles, pont=None, trou=3):
        nb = 180
        cases = (angles * 0.5).astype(np.int32) % nb
        h = np.bincount(cases, minlength=nb)
        occ = h > 0
        if pont is not None and pont.size:
            occ = occ | (np.bincount((pont * 0.5).astype(np.int32) % nb, minlength=nb) > 0)
        vides = np.flatnonzero(~occ)
        if vides.size == 0:
            return []
        dep = int(vides[0])
        occ_r, h_r = np.roll(occ, -dep), np.roll(h, -dep)
        runs, cur, nb_trou = [], None, 0
        for i in range(nb):
            if occ_r[i]:
                if cur is None:
                    cur = [i, i, 0, 0]
                cur[1] = i
                cur[2] += int(h_r[i])
                cur[3] += h_r[i] > 0
                nb_trou = 0
            elif cur is not None:
                nb_trou += 1
                if nb_trou > trou:
                    runs.append(cur)
                    cur, nb_trou = None, 0
        if cur is not None:
            runs.append(cur)
        pos = (cases - dep) % nb
        return [((pos >= a) & (pos <= b), n, n / max(1, k)) for a, b, n, k in runs if n > 0]

    @staticmethod
    def _directions_pleines(angles):
        cases = (angles * 0.5).astype(np.int32) % 180
        h = np.bincount(cases, minlength=180)
        return h[cases] >= max(2, 0.3 * h.max())

    @staticmethod
    def _bloc(angles, pont=None, trou=3):
        blocs = Analyseur._blocs(angles, pont, trou)
        return max(blocs, key=lambda b: b[1])[0] if blocs else None

    @staticmethod
    def _bloc_epais(angles, mini, pont=None):
        blocs = [b for b in Analyseur._blocs(angles, pont) if b[1] >= mini]
        return max(blocs, key=lambda b: b[2])[0] if blocs else None

    @staticmethod
    def _moyenne(angles):
        rad = np.radians(angles)
        return float(np.degrees(np.arctan2(np.sin(rad).mean(), np.cos(rad).mean())) % 360.0)

    def _rien(self, raison):
        self.raison = raison
        return None

    def barre(self, bgra):
        mb, mr, hsv = self.masques(bgra)
        self.nb_bleu, self.nb_rouge = cv2.countNonZero(mb), cv2.countNonZero(mr)
        self.nb_barre = 0
        self.raison = None
        if self.nb_bleu < self.min_b:
            return self._rien("pas de bleu")
        if self.nb_rouge < self.min_r:
            return self._rien("pas de rouge")
        if self.calibre:
            sombre = np.count_nonzero((hsv[..., 2] < 70) & (self.anneau > 0))
            if sombre + self.nb_bleu + self.nb_rouge < 0.7 * self.nb_anneau:
                return self._rien("anneau pas sombre")
        ab, ar = self.angle[mb > 0], self.angle[mr > 0]
        ab, ar = ab[self._directions_pleines(ab)], ar[self._directions_pleines(ar)]
        if len(ab) < self.min_b or len(ar) < self.min_r:
            return self._rien("directions pleines")
        bloc = self._bloc_epais(ab, self.min_b, ar)
        if bloc is None:
            return self._rien("pas de bloc bleu")
        ab = ab[bloc]
        if np.bincount((ab * 0.5).astype(np.int32) % 180).max() < 0.35 * self.nb_anneau / 180:
            return self._rien("barre trop fine")
        self.nb_barre = len(ab)
        centre = self._moyenne(ab)
        debut, fin = np.percentile(wrap(ab - centre), (1, 99))
        if not 3 <= fin - debut <= 170:
            return self._rien("largeur de barre")
        bloc = self._bloc_epais(ar, self.min_r)
        if bloc is None:
            return self._rien("pas de bloc rouge")
        ar = ar[bloc]
        rouge = self._moyenne(ar)
        if np.ptp(wrap(ar - rouge)) > 40:
            return self._rien("aiguille trop large")
        return centre, float(debut), float(fin), float(wrap(rouge - centre))


class Decideur:

    def __init__(self, cfg):
        self.attendre = bool(cfg["attendre_barre"])
        self.lat = float(cfg["latence_ms"]) / 1000.0
        self.marge = float(cfg["marge_deg"])
        self.cible = min(0.5, max(0.0, float(cfg["cible"])))
        self.absence = float(cfg["absence_ms"]) / 1000.0
        self.reset()

    def reset(self):
        self.verrou = None
        self.t_vue = 0.0
        self.prev = None
        self.prev_t = 0.0
        self.vit = None
        self.t_sans_vit = None
        self.saut = False
        self.info = None

    def etape(self, nom, barre, now):
        self.saut = False
        if nom is None:
            self.prev = self.vit = self.t_sans_vit = None
            if self.verrou and now - self.t_vue >= self.absence:
                self.verrou = None
            return False
        self.t_vue = now

        if not self.attendre:
            if self.verrou is None or self.verrou[0] != nom:
                self.verrou = (nom, None)
                self.info = {"mode": "sans attendre la barre"}
                return True
            return False

        if barre is None:
            self.prev = None
            return False
        centre, debut, fin, rel = barre

        rouge = (centre + rel) % 360.0
        saut = False
        if self.prev is None:
            self.prev, self.prev_t = rouge, now
        else:
            dv, dt = wrap(rouge - self.prev), now - self.prev_t
            if abs(dv) > 60 or dt > 0.25:
                saut = self.saut = abs(dv) > 60
                self.prev, self.prev_t, self.vit = rouge, now, None
            elif abs(dv) >= 0.5 and dt > 0:
                v = dv / dt
                self.vit = v if self.vit is None else 0.5 * self.vit + 0.5 * v
                self.prev, self.prev_t = rouge, now
        if self.vit is None:
            if self.t_sans_vit is None:
                self.t_sans_vit = now
        else:
            self.t_sans_vit = None

        if self.verrou:
            meme = (not saut and self.verrou[0] == nom and self.verrou[1] is not None
                    and abs(wrap(centre - self.verrou[1])) <= 15)
            if meme:
                return False
            self.verrou = None

        if self.vit is None and now - self.t_sans_vit < 0.15:
            return False

        age = now - self.prev_t
        pred = rel + (self.vit or 0.0) * (self.lat + age)
        larg = fin - debut
        m = min(self.marge, larg * 0.25)
        entree = max(m, self.cible * larg)
        if self.vit is None or abs(self.vit) < 1:
            ok = debut + m <= pred <= fin - m
        elif self.vit > 0:
            ok = debut + entree <= pred <= fin - m
        else:
            ok = debut + m <= pred <= fin - entree
        if ok:
            self.verrou = (nom, centre)
            pos = (pred - debut) / larg if (self.vit or 1) > 0 else (fin - pred) / larg
            self.info = {"rel": round(rel, 2), "debut": round(debut, 2), "fin": round(fin, 2),
                         "largeur": round(larg, 2), "pred": round(pred, 2), "pos_prevue": round(pos, 3),
                         "vit": None if self.vit is None else round(self.vit, 1),
                         "age_image_ms": round(age * 1000, 2), "latence_ms": round(self.lat * 1000, 1),
                         "cible": self.cible}
            return True
        return False


class CollecteurInconnues:

    def __init__(self):
        self.dossier = os.path.join(TEMPLATES_DIR, A_RENOMMER)
        os.makedirs(self.dossier, exist_ok=True)
        self.vues = [img for img in (lire_image(p) for p in lettres_a_nommer()) if img is not None]
        self.prec, self.stable = None, 0

    @staticmethod
    def _pareil(a, b, seuil):
        if a.shape != b.shape:
            return False
        s = float(cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED).max())
        return np.isfinite(s) and s >= seuil

    def voir(self, gris):
        if self.prec is not None and self._pareil(gris, self.prec, 0.97):
            self.stable += 1
        else:
            self.stable = 0
        self.prec = gris
        if self.stable != 3 or not lettre_plausible(gris):
            return None
        if any(self._pareil(gris, v, 0.90) for v in self.vues):
            return None
        i = 1
        while os.path.exists(os.path.join(self.dossier, f"inconnue_{i}.png")):
            i += 1
        fichier = f"inconnue_{i}.png"
        ecrire_image(os.path.join(self.dossier, fichier), gris)
        self.vues.append(gris.copy())
        return fichier


def heure():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


LOGS_DIR = os.path.join(BASE, "logs")


def _json_defaut(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.ndarray, tuple, set)):
        return list(o)
    return str(o)


class Journal:

    GARDER = 80

    def __init__(self, prefixe, actif=True, horodatage=None):
        self.f, self.chemin = None, None
        self.lock = threading.Lock()
        if not actif:
            return
        try:
            os.makedirs(LOGS_DIR, exist_ok=True)
            self._nettoyer()
            stamp = horodatage or datetime.now().strftime("%Y%m%d_%H%M%S")
            self.chemin = os.path.join(LOGS_DIR, f"{prefixe}_{stamp}.jsonl")
            self.f = open(self.chemin, "a", encoding="utf-8", buffering=1)
        except OSError:
            self.f = None

    def _nettoyer(self):
        fichiers = sorted((os.path.join(LOGS_DIR, n) for n in os.listdir(LOGS_DIR)
                           if n.endswith((".jsonl", ".md"))), key=os.path.getmtime)
        for p in fichiers[:-self.GARDER] if len(fichiers) > self.GARDER else []:
            try:
                os.remove(p)
            except OSError:
                pass

    def ev(self, type_, **data):
        if self.f is None:
            return
        d = {"t": round(time.perf_counter(), 4), "h": heure(), "ev": type_}
        d.update(data)
        ligne = json.dumps(d, ensure_ascii=False, default=_json_defaut)
        with self.lock:
            try:
                self.f.write(ligne + "\n")
            except (OSError, ValueError):
                pass

    def fermer(self):
        with self.lock:
            if self.f:
                self.f.close()
                self.f = None


class Suivi:

    DISPARU_S = 0.35

    def __init__(self, journal, log=print):
        self.j, self.log = journal, log
        self.cycle = self.check = None
        self.n_cycle = 0
        self.t_vu = 0.0
        self.apres_vert = False
        self.fins = []
        self.stats = Counter()

    def image(self, now, nom, score, barre, an, dec, vert):
        self.fins = []
        visible = barre is not None or nom is not None or vert
        if not visible:
            if (self.cycle or self.apres_vert) and now - self.t_vu > self.DISPARU_S:
                self._disparu(now)
            return
        self.t_vu = now
        if vert:
            if self.cycle:
                if self.check:
                    self._fin_check(now, "réussi" if self.check["appui"] else "vert sans appui", "anneau vert")
                self._fin_cycle(now, "crocheté")
            self.apres_vert = True
            return
        if self.apres_vert:
            if barre is None:
                return
            self.apres_vert = False
        c = self.check
        if barre is None and c is None:
            return
        if self.cycle is None:
            self._debut_cycle(now)
        cause = None
        if c is None:
            cause = "début"
        elif nom and c["lettre"] and nom != c["lettre"]:
            cause = "nouvelle lettre"
        elif barre and c["centre"] is not None and abs(wrap(barre[0] - c["centre"])) > 15:
            cause = "barre déplacée"
        elif dec is not None and dec.saut:
            cause = "aiguille repartie de zéro"
        if cause:
            if c:
                self._fin_check(now, "réussi" if c["appui"] else "changé sans appui", cause)
            self._debut_check(now, nom, score, barre)
            c = self.check
        c["images"] += 1
        if nom:
            if not c["lettre"]:
                c["lettre"] = nom
            if nom == c["lettre"]:
                c["score_min"] = min(c["score_min"], score)
        if barre is not None:
            c["images_barre"] += 1
            if c["centre"] is None:
                c["centre"] = barre[0]
            c["largeur"] = round(barre[2] - barre[1], 2)
        else:
            c["sans_barre"][an.raison or "?"] += 1
        if dec is not None and dec.vit is not None:
            c["vit"] = round(dec.vit, 1)

    def appui(self, now, nom, score, envoye, info, raison=None):
        c = self.check
        if c is None:
            return
        a = {"t": round(now, 4), "lettre": nom, "score": round(score, 3), "envoye": envoye,
             "depuis_debut_ms": round((now - c["t0"]) * 1000, 1)}
        if info:
            a.update(info)
        if raison:
            a["raison"] = raison
        if envoye:
            if c["appui"]:
                c["appuis_en_plus"] += 1
            else:
                c["appui"] = a
        self.j.ev("appui", cycle=self.n_cycle, n=c["n"], **a)

    def _debut_cycle(self, now):
        self.n_cycle += 1
        self.cycle = {"n": self.n_cycle, "t0": now, "checks": []}
        self.check = None
        self.j.ev("cycle_debut", cycle=self.n_cycle)

    def _debut_check(self, now, nom, score, barre):
        n = len(self.cycle["checks"]) + 1
        self.check = {"n": n, "t0": now, "lettre": nom, "score_min": score if nom else 1.0,
                      "centre": barre[0] if barre else None,
                      "largeur": round(barre[2] - barre[1], 2) if barre else None,
                      "vit": None, "images": 0, "images_barre": 0, "sans_barre": Counter(),
                      "appui": None, "appuis_en_plus": 0}
        self.j.ev("check_debut", cycle=self.n_cycle, n=n, lettre=nom, score=round(score, 3),
                  largeur=self.check["largeur"])

    def _fin_check(self, now, resultat, cause):
        c = self.check
        self.check = None
        if c is None:
            return
        a = c["appui"]
        self.cycle["checks"].append(resultat)
        if resultat.startswith("réussi"):
            self.stats["checks_reussis"] += 1
        elif resultat.startswith("raté"):
            self.stats["checks_rates"] += 1
        else:
            self.stats["checks_sans_appui"] += 1
        self.j.ev("check_fin", cycle=self.n_cycle, n=c["n"], lettre=c["lettre"], resultat=resultat,
                  cause=cause, duree_ms=round((now - c["t0"]) * 1000, 1),
                  delai_apres_appui_ms=round((now - a["t"]) * 1000, 1) if a else None,
                  images=c["images"], images_barre=c["images_barre"], sans_barre=dict(c["sans_barre"]),
                  score_min=round(c["score_min"], 3), largeur=c["largeur"], vit=c["vit"],
                  appui=a, appuis_en_plus=c["appuis_en_plus"])
        self.fins.append((c, resultat))

    def _fin_cycle(self, now, resultat):
        cy = self.cycle
        self.cycle = None
        if cy is None or not cy["checks"]:
            return
        self.stats["crochetages_ok" if resultat == "crocheté" else
                   "crochetages_rates" if resultat == "raté" else "cycles_abandonnes"] += 1
        duree = now - cy["t0"]
        self.j.ev("cycle_fin", cycle=cy["n"], resultat=resultat, nb_checks=len(cy["checks"]),
                  checks=cy["checks"], duree_ms=round(duree * 1000, 1))
        if resultat == "crocheté":
            self.log(f"CROCHETÉ : {len(cy['checks'])} check(s) en {duree:.1f} s")
        elif resultat == "raté":
            self.log(f"RATÉ au check {len(cy['checks'])} (cercle disparu sans vert)")

    def _disparu(self, now):
        self.apres_vert = False
        if self.cycle is None:
            return
        c = self.check
        if c:
            self._fin_check(now, "raté (cercle disparu)" if c["appui"] else "disparu sans appui", "cercle disparu")
        rate = c is not None and c["appui"] is not None
        self._fin_cycle(now, "raté" if rate else "abandonné")


class Capture:
    def __init__(self, sct, mode="auto"):
        self.sct, self.mode, self.raison, self.cam = sct, "mss", None, None
        self.derniere, self.ecran = None, None
        if mode not in ("auto", "dxgi", "mss"):
            self.raison = f"mode de capture inconnu : {mode}"
        if mode in ("auto", "dxgi"):
            try:
                import dxcam
                self.cam = dxcam.create(output_color="BGRA")
                mon = sct.monitors[1]
                self.ecran = (mon["left"], mon["top"], mon["width"], mon["height"])
                self.mode = "dxgi"
            except Exception as e:
                self.cam, self.raison = None, f"DXGI indisponible : {e}"

    def _region(self, z):
        l, t, w, h = self.ecran
        x0, y0 = z["left"] - l, z["top"] - t
        if x0 < 0 or y0 < 0 or x0 + z["width"] > w or y0 + z["height"] > h:
            return None
        return (x0, y0, x0 + z["width"], y0 + z["height"])

    def image(self, zone):
        if self.cam is not None:
            r = self._region(zone)
            if r is not None:
                try:
                    f = self.cam.grab(region=r)
                except Exception as e:
                    self.raison = f"DXGI en erreur, repli sur mss : {e}"
                    self.fermer()
                    f, r = None, None
                if f is not None:
                    self.derniere = f
                    return f, True
                if r is not None and self.derniere is not None and self.derniere.shape[:2] == (zone["height"], zone["width"]):
                    return self.derniere, False
        f = np.asarray(self.sct.grab(zone))
        self.derniere = f
        return f, True

    def fermer(self):
        if self.cam is not None:
            try:
                self.cam.release()
            except Exception:
                pass
            self.cam, self.mode = None, "mss"


class Moteur:

    def __init__(self, cfg, log=print, debug=False, actif=False, touche_quitter=True,
                 journal_prefixe="moteur", journal_horodatage=None):
        self.cfg, self.debug, self.actif, self.touche_quitter = cfg, debug, actif, touche_quitter
        self._log_fn = log
        self.journal_prefixe, self.journal_horodatage = journal_prefixe, journal_horodatage
        self.j = None
        self.suivi = None
        self.stop = False
        self.apercu = False
        self.raccourcis = True
        self.demande_detection = False
        self.demande_rechargement = False
        self.image = self.barre = self.nom = None
        self.score, self.fps = 0.0, 0.0
        self.cercle = False
        self.nb_appuis = self.nb_reussis = 0
        self.nb_crochetages = self.nb_rates = 0
        self.dernier = ""
        self.version_config = 0
        self.an = None
        self.modeles = []
        self.nb_modeles = 0

    def log(self, msg):
        self.dernier = msg
        self._log_fn(f"[{heure()}] {msg}")
        self.ev("log", msg=msg)

    def ev(self, type_, **data):
        if self.j:
            self.j.ev(type_, **data)

    def arreter(self):
        self.stop = True

    def basculer(self):
        self.actif = not self.actif
        if self.an:
            self.dec.reset()
        self.log("ACTIF" if self.actif else "EN PAUSE")
        self.ev("etat", actif=self.actif)

    def _init_zone(self):
        zone = self.cfg.get("zone")
        self.an = None
        if not zone:
            return
        self.zone = zone
        self.an = Analyseur(self.cfg, zone["height"], zone["width"])
        self.dec = Decideur(self.cfg)
        self.collecteur = CollecteurInconnues()
        self.calibre = not self.cfg.get("anneau_auto", True)
        self.recharger_modeles()

    def recharger_modeles(self):
        if self.an:
            self.modeles = charger_modeles(self.cfg, self.an.taille_lettre, log=self.log)
            self.nb_modeles = len(self.modeles)

    def _calibrer(self, img):
        band = self.an.calibrer(img)
        if band:
            self.an.set_anneau(*band, calibre=True)
            self.log(f"Anneau calibré sur le cercle noir : {band}")
            self.ev("calibrage", anneau=band)
        return band is not None

    def detecter(self):
        self.log("Recherche du cercle...")
        trouve = None
        for _ in range(3):
            trouve = trouver_cercle(self.sct, self.cfg, self.sct.monitors[1])
            if trouve:
                break
        if trouve is None:
            self.log("Cercle pas trouvé : fais apparaître un skill-check (aiguille qui tourne) et réessaie")
            self.ev("detection", trouve=False)
            return False
        sauver_config(self.cfg)
        self._init_zone()
        self.version_config += 1
        z = self.cfg["zone"]
        self.log(f"Cercle trouvé : centre ({z['left'] + z['width'] // 2}, {z['top'] + z['height'] // 2}), "
                 f"zone {z['width']} px")
        self.ev("detection", trouve=True, zone=z, anneau=self.cfg.get("anneau"))
        return True

    def _capturer(self, check, hist, nb=8):
        imgs = [(t, f) for t, f in hist if t >= check["t0"] - 0.05]
        if not imgs:
            return
        if len(imgs) > nb:
            imgs = [imgs[round(i * (len(imgs) - 1) / (nb - 1))] for i in range(nb)]
        nom = os.path.splitext(os.path.basename(self.j.chemin))[0]
        dossier = os.path.join(LOGS_DIR, "captures", nom)
        try:
            os.makedirs(dossier, exist_ok=True)
            fichiers = []
            for i, (t, f) in enumerate(imgs):
                p = os.path.join(dossier, f"cycle{self.suivi.n_cycle:03d}_check{check['n']}_{i + 1}"
                                          f"_{(t - check['t0']) * 1000:+05.0f}ms.png")
                ecrire_image(p, np.ascontiguousarray(f))
                fichiers.append(os.path.relpath(p, BASE))
            self.ev("captures", cycle=self.suivi.n_cycle, n=check["n"], fichiers=fichiers)
        except Exception as e:
            self.ev("erreur", ou="captures", msg=str(e))

    def run(self):
        self.j = Journal(self.journal_prefixe, bool(self.cfg.get("journal", True)), self.journal_horodatage)
        self.suivi = Suivi(self.j, log=self.log)
        self.ev("session_debut", version=VERSION, python=platform.python_version(), os=platform.platform(),
                profil=os.path.basename(CONFIG_PATH), config=self.cfg, actif=self.actif)
        code = 1
        try:
            code = self._boucle()
            return code
        except Exception:
            import traceback
            self.ev("erreur", trace=traceback.format_exc())
            raise
        finally:
            if self.suivi.cycle:
                self.suivi._fin_cycle(time.perf_counter(), "interrompu")
            self.ev("session_fin", code=code, appuis=self.nb_appuis, checks_reussis=self.nb_reussis,
                    stats=dict(self.suivi.stats))
            self.j.fermer()

    def _boucle(self):
        cfg = self.cfg
        vk = lambda k: VK[str(cfg[k]).upper()]
        vk_onoff, vk_det, vk_cap, vk_quit = (vk("touche_on_off"), vk("touche_detecter"),
                                             vk("touche_capture"), vk("touche_quitter"))
        speciales = {vk_onoff, vk_det, vk_cap, vk_quit}
        lettres_vk = [(n, v) for n, v in CAPTURABLE if v not in speciales]
        fixe = (cfg.get("touche_fixe") or "").upper() or None
        if fixe and fixe not in VK:
            self.log(f"touche_fixe '{fixe}' inconnue")
            return 1
        cible = cfg.get("fenetre_cible")
        seuil = float(cfg["seuil"])
        appui_ms = float(cfg["appui_ms"])
        pause_s = float(cfg["pause_boucle_ms"]) / 1000.0
        auto = bool(cfg.get("apprentissage_auto", True)) and not fixe

        rac = Raccourcis()
        rac.synchro(list(speciales))
        lettre_capturee = None
        candidat = None
        guette_lettres = False
        taille_barre = 0.0
        nb_frames, t_fps, t_debug = 0, time.perf_counter(), 0.0
        t_perf, n_perf = time.perf_counter(), 0
        hist, t_hist = deque(maxlen=60), 0.0
        captures = bool(cfg.get("captures_echec", True)) and self.j.f is not None
        self._init_zone()

        with MSS() as sct:
            self.sct = sct
            cap = Capture(sct, str(cfg.get("capture") or "auto").lower())
            self.capture_mode = cap.mode
            if cap.raison:
                self.log(cap.raison)
            self.ev("ecran", moniteurs=sct.monitors, zone=cfg.get("zone"), anneau=cfg.get("anneau"),
                    modeles=self.nb_modeles, capture=cap.mode, capture_raison=cap.raison)
            dernier, an_prec, inconnue = None, None, False
            try:
                while not self.stop:
                    rc = self.raccourcis
                    if rc and self.touche_quitter and rac.appui(vk_quit):
                        self.log("Bye.")
                        return 0
                    if rc and rac.appui(vk_onoff):
                        self.basculer()
                    if (rc and rac.appui(vk_det)) or self.demande_detection:
                        self.demande_detection = False
                        self.detecter()
                    if self.demande_rechargement:
                        self.demande_rechargement = False
                        self.recharger_modeles()
                    if rc and rac.appui(vk_cap) and self.an:
                        if lettre_capturee is None:
                            lettre_capturee = self.an.lettre(np.asarray(sct.grab(self.zone)))
                            rac.synchro([v for _, v in lettres_vk])
                            self.log(f"Image capturée : appuie sur la lettre affichée ({cfg['touche_capture']} = annuler)")
                        else:
                            lettre_capturee = None
                            self.log("Capture annulée")

                    if lettre_capturee is not None:
                        for nom, v in lettres_vk:
                            if rac.appui(v):
                                f = sauver_modele(nom, rogner_modele(lettre_capturee))
                                lettre_capturee = None
                                self.recharger_modeles()
                                self.dec.reset()
                                self.log(f"Lettre apprise : {nom} ({dossier_modeles()}/{f})")
                                self.ev("lettre_apprise", lettre=nom, comment="F8")
                                break
                        time.sleep(0.005)
                        continue

                    if self.an is None:
                        self.cercle = False
                        time.sleep(0.05)
                        continue
                    if not self.actif:
                        if not (self.apercu or auto):
                            self.cercle = False
                            time.sleep(0.02)
                            continue
                        time.sleep(0.03)

                    an = self.an
                    if an is not an_prec:
                        dernier, an_prec = None, an
                    frame, nouvelle = cap.image(self.zone)
                    if nouvelle or dernier is None:
                        nouvelle = True
                        gris = None
                        if fixe:
                            barre = an.barre(frame)
                            if barre and not self.calibre and self._calibrer(frame):
                                self.calibre, barre = True, an.barre(frame)
                            nom, vkn, score = (fixe, VK[fixe], 1.0) if barre else (None, None, 0.0)
                        else:
                            gris = an.lettre(frame)
                            nom, vkn, score = meilleur_match(pretraiter(gris, cfg), self.modeles)
                            if not self.calibre and score >= max(seuil, 0.85):
                                self.calibre = self._calibrer(frame)
                            barre = an.barre(frame)
                        dernier = (frame, gris, nom, vkn, score, barre)
                    else:
                        frame, gris, nom, vkn, score, barre = dernier
                    maintenant = time.perf_counter()
                    reconnu = nom if score >= seuil else None
                    self.image, self.barre, self.nom, self.score = frame, barre, nom, score
                    self.cercle = bool(reconnu or barre)
                    vert_anneau = an.nb_vert >= 0.45 * an.nb_anneau
                    vert_barre = bool(taille_barre and an.nb_vert >= max(8, 0.4 * taille_barre)
                                      and an.nb_barre < 0.3 * taille_barre)
                    vert = vert_anneau or vert_barre
                    if nouvelle and barre is not None and not vert:
                        taille_barre = an.nb_barre if not taille_barre else 0.8 * taille_barre + 0.2 * an.nb_barre

                    barre_dec = barre if self.dec.attendre and not vert else None
                    decide = self.actif and self.dec.etape(reconnu, barre_dec, maintenant) and not vert
                    if nouvelle:
                        self.suivi.image(maintenant, reconnu, score, barre, an, self.dec if self.actif else None, vert)
                    fins = self.suivi.fins if nouvelle else []
                    if decide:
                        titre = titre_premier_plan() if cible else ""
                        if cible and cible.lower() not in titre.lower():
                            self.log(f"{nom} PAS envoyé : fenêtre active « {titre[:40]} » (attendu « {cible} »)")
                            self.suivi.appui(maintenant, nom, score, False, self.dec.info,
                                             raison=f"fenêtre active « {titre[:60]} »")
                        else:
                            appuyer(vkn, appui_ms)
                            self.nb_appuis += 1
                            self.suivi.appui(maintenant, nom, score, True, self.dec.info)
                            info = ""
                            if barre:
                                _, debut, fin, rel = barre
                                info = f" | rouge {rel:+.0f}° dans [{debut:+.0f}°, {fin:+.0f}°]"
                            self.log(f"-> {nom}  (lettre {score:.2f}{info})")

                    if vert:
                        self.dec.verrou = None
                    if captures and nouvelle and maintenant - t_hist >= 0.025:
                        hist.append((maintenant, frame))
                        t_hist = maintenant
                    for c, res in fins:
                        if res.startswith("réussi"):
                            self.nb_reussis += 1
                        elif res.startswith("raté") and captures:
                            self._capturer(c, hist)
                    self.nb_crochetages = self.suivi.stats["crochetages_ok"]
                    self.nb_rates = self.suivi.stats["crochetages_rates"]

                    if nouvelle:
                        inconnue = (not fixe and reconnu is None and barre is not None and lettre_plausible(gris))
                    if inconnue and nouvelle:
                        f = self.collecteur.voir(gris)
                        if f:
                            self.log(f"Lettre inconnue vue ({dossier_modeles()}/{A_RENOMMER}/{f}) : "
                                     f"joue-la toi-même pour me l'apprendre, ou nomme-la dans l'appli")
                    if auto:
                        if inconnue and not guette_lettres:
                            rac.synchro([v for _, v in lettres_vk])
                        guette_lettres = inconnue
                        if inconnue and candidat is None:
                            for n, v in lettres_vk:
                                if rac.appui(v):
                                    candidat = (n, gris.copy(), maintenant)
                                    break
                        if candidat is not None:
                            n, g, t = candidat
                            for c, res in fins:
                                if c["t0"] <= t:
                                    if not res.startswith(("raté", "disparu")):
                                        sauver_modele(n, rogner_modele(g))
                                        self.recharger_modeles()
                                        self.log(f"Lettre apprise toute seule : {n} (tu l'as jouée, check réussi)")
                                        self.ev("lettre_apprise", lettre=n, comment="jouée à la main")
                                    candidat = None
                                    break
                            if candidat is not None and maintenant - t > 3.0:
                                candidat = None

                    nb_frames += nouvelle
                    n_perf += nouvelle
                    if maintenant - t_fps >= 1.0:
                        self.fps, nb_frames, t_fps = nb_frames / (maintenant - t_fps), 0, maintenant
                    if maintenant - t_perf >= 10.0:
                        self.ev("perf", images_par_s=round(n_perf / (maintenant - t_perf), 1), actif=self.actif)
                        t_perf, n_perf = maintenant, 0
                    if self.debug and maintenant - t_debug >= 0.15:
                        t_debug = maintenant
                        b = (f"bleu [{barre[1]:+4.0f},{barre[2]:+4.0f}] rouge {barre[3]:+5.0f}°"
                             if barre else f"bleu {an.nb_bleu:4d}px rouge {an.nb_rouge:3d}px ({an.raison or '-'})")
                        v = f"{self.dec.vit:+5.0f}°/s" if self.dec.vit is not None else "   -   "
                        print(f"  lettre {nom or '-':>5} {score:4.2f} | {b} | vit {v} | vert {an.nb_vert:3d} | "
                              f"{self.fps:5.0f}/s   ", end="\r", flush=True)
                    if pause_s:
                        time.sleep(pause_s)
                    elif not nouvelle:
                        time.sleep(0.0005)
            finally:
                cap.fermer()
        return 0


def lancer(cfg, debug, actif=False):
    m = Moteur(cfg, debug=debug, actif=actif)
    print(f"Zone : {cfg.get('zone') or 'aucune -> F7 pendant un skill-check pour trouver le cercle'}")
    print(f"[{cfg['touche_on_off']}] activer/pause   [{cfg['touche_detecter']}] trouver le cercle   "
          f"[{cfg['touche_capture']}] apprendre une lettre   [{cfg['touche_quitter']}] quitter")
    if cfg.get("fenetre_cible"):
        print(f"Appuie seulement si la fenêtre active contient : '{cfg['fenetre_cible']}'")
    print("ACTIF" if actif else f"EN PAUSE - {cfg['touche_on_off']} pour activer")
    return m.run()


def detecter_cli(cfg, ecran):
    print("Lance un skill-check dans le jeu (barre bleue + aiguille visibles)...")
    with MSS() as sct:
        mon = sct.monitors[ecran]
        for essai in range(10):
            print(f"  recherche du cercle... {essai + 1}/10 ", end="\r", flush=True)
            if trouver_cercle(sct, cfg, mon):
                sauver_config(cfg)
                ecrire_image(os.path.join(BASE, "zone_apercu.png"),
                             cv2.cvtColor(np.asarray(sct.grab(cfg["zone"])), cv2.COLOR_BGRA2BGR))
                print(f"\nCercle trouvé : zone {cfg['zone']} | anneau {cfg['anneau']} (aperçu : zone_apercu.png)")
                return 0
            time.sleep(1)
    print("\nCercle pas trouvé.")
    return 1


def choisir_zone(cfg, valeurs, ecran, centre=None):
    with MSS() as sct:
        mon = sct.monitors[ecran]
    mx, my = mon["left"] + mon["width"] // 2, mon["top"] + mon["height"] // 2
    shot = None
    if centre:
        x, y, w, h = mx - centre // 2, my - centre // 2, centre, centre
    elif valeurs and len(valeurs) == 4:
        x, y, w, h = valeurs
    else:
        print("Va sur le jeu et fais apparaître le cercle...")
        for i in range(5, 0, -1):
            print(f"  capture de l'écran dans {i}s ", end="\r", flush=True)
            time.sleep(1)
        with MSS() as sct:
            shot = np.asarray(sct.grab(mon))
        img = cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR)
        print("\nEncadre le CERCLE ENTIER à la souris, puis ENTREE ou ESPACE (C = annuler).")
        win = "Encadre le cercle - ENTREE pour valider"
        cv2.namedWindow(win, cv2.WINDOW_NORMAL)
        cv2.setWindowProperty(win, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
        x, y, w, h = cv2.selectROI(win, img, showCrosshair=True, fromCenter=False)
        cv2.destroyAllWindows()
        if w == 0 or h == 0:
            print("Annulé.")
            return 1
        det = detecter_cercle(img, cfg, centre_recherche=(x + w // 2, y + h // 2),
                              rayon_recherche=max(w, h) * 0.75)
        if det:
            appliquer_detection(cfg, det, (mon["left"], mon["top"]))
            print("Cercle recalé automatiquement dans ta sélection.")
            w = 0
        else:
            x += mon["left"]
            y += mon["top"]
    if w:
        s = max(w, h)
        cx, cy = x + w // 2, y + h // 2
        cfg["zone"] = {"left": int(cx - s // 2), "top": int(cy - s // 2), "width": int(s), "height": int(s)}
    sauver_config(cfg)
    with MSS() as sct:
        ecrire_image(os.path.join(BASE, "zone_apercu.png"),
                     cv2.cvtColor(np.asarray(sct.grab(cfg["zone"])), cv2.COLOR_BGRA2BGR))
    print(f"Zone enregistrée : {cfg['zone']}  (aperçu : zone_apercu.png)")
    return 0


def tester_image(cfg, chemin):
    img = lire_image(chemin, cv2.IMREAD_COLOR)
    if img is None:
        print("Image illisible :", chemin)
        return 1
    if max(img.shape[:2]) > 600:
        det = detecter_cercle(img, cfg)
        if det is None:
            print("Cercle pas trouvé dans l'image.")
            return 1
        cfg = json.loads(json.dumps(cfg))
        appliquer_detection(cfg, det)
        z = cfg["zone"]
        print(f"Cercle trouvé : centre ({det['centre'][0]:.1f}, {det['centre'][1]:.1f}) rayon {det['rayon']:.1f} "
              f"| zone {z} | anneau {cfg['anneau']}")
        img = img[z["top"]:z["top"] + z["height"], z["left"]:z["left"] + z["width"]].copy()
    bgra = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    h, w = img.shape[:2]
    an = Analyseur(cfg, h, w)
    band = an.calibrer(bgra) if cfg.get("anneau_auto", True) else None
    if band:
        an.set_anneau(*band, calibre=True)
    print(f"Anneau : {list(an.anneau_r)} ({'calibré sur anneau noir' if band else 'valeur de config'})")
    res = an.barre(bgra)
    print(f"Zone {w}x{h} | pixels bleus : {an.nb_bleu} | rouges : {an.nb_rouge} | verts : {an.nb_vert}")
    modeles = charger_modeles(cfg, an.taille_lettre)
    gris = an.lettre(bgra)
    print(f"Carré lettre : {'plausible' if lettre_plausible(gris) else 'PAS plausible'} "
          f"(médiane {np.median(gris):.0f}, max {gris.max()})")
    if modeles:
        nom, _, score = meilleur_match(pretraiter(gris, cfg), modeles)
        print(f"Lettre : {nom} (score {score:.2f}, seuil {cfg['seuil']})")
    out = img.copy()
    cy, cx, R = (h - 1) / 2, (w - 1) / 2, min(h, w) / 2
    ys, xs = an.lettre_sl
    cv2.rectangle(out, (xs.start, ys.start), (xs.stop - 1, ys.stop - 1), (255, 255, 255), 1)
    for rr in an.anneau_r:
        cv2.circle(out, (int(round(cx)), int(round(cy))), int(round(rr * R)), (0, 255, 0), 1)
    if res is None:
        print("Pas de barre bleue + aiguille rouge détectées.")
    else:
        centre, debut, fin, rel = res
        dedans = debut <= rel <= fin
        print(f"Barre bleue : {debut:+.1f}° -> {fin:+.1f}° (centre {centre:.1f}°) | "
              f"aiguille rouge : {rel:+.1f}° -> {'DEDANS : appui' if dedans else 'dehors'}")
        for a, col in ((centre + debut, (255, 255, 0)), (centre + fin, (255, 255, 0)), (centre + rel, (0, 0, 255))):
            t = np.radians(a)
            cv2.line(out, (int(cx), int(cy)), (int(cx + R * np.cos(t)), int(cy + R * np.sin(t))), col, 1)
    sortie = os.path.join(BASE, "test_resultat.png")
    ecrire_image(sortie, cv2.resize(out, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST))
    print("Image annotée (x3) :", sortie)
    return 0


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

    p = argparse.ArgumentParser(description="Skill-check automatique : appuie sur la lettre quand le rouge est dans le bleu.")
    p.add_argument("--detecter", action="store_true", help="trouve le cercle tout seul (skill-check affiché)")
    p.add_argument("--zone", nargs="*", type=int, metavar="N",
                   help="choisir la zone à la souris, ou donner X Y LARGEUR HAUTEUR")
    p.add_argument("--centre", type=int, metavar="TAILLE",
                   help="zone carrée de TAILLE pixels pile au milieu de l'écran")
    p.add_argument("--ecran", type=int, default=1, help="numéro d'écran (1 = principal)")
    p.add_argument("--debug", action="store_true", help="affiche la détection en direct")
    p.add_argument("--actif", action="store_true", help="démarre directement actif (sans appuyer sur F6)")
    p.add_argument("--profil", metavar="NOM", help="profil séparé : config_NOM.json + templates_NOM/ (ex : sim)")
    p.add_argument("--test", metavar="IMAGE", help="teste la détection sur une capture (plein écran ou recadrée)")
    args = p.parse_args()

    set_profil(args.profil)
    cfg = charger_config()
    if not os.path.exists(CONFIG_PATH):
        sauver_config(cfg)

    if args.test:
        return tester_image(cfg, args.test)
    if args.detecter:
        return detecter_cli(cfg, args.ecran)
    if args.centre:
        return choisir_zone(cfg, None, args.ecran, centre=args.centre)
    if args.zone is not None:
        return choisir_zone(cfg, args.zone, args.ecran)
    return lancer(cfg, args.debug, actif=args.actif)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nBye.")
