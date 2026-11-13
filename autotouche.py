#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
I Like Baylife - le moteur du skill-check automatique (+ ligne de commande).

Cercle au milieu de l'écran : lettre au centre, barre bleue sur l'anneau, aiguille
rouge qui tourne. Quand l'aiguille arrive dans la barre bleue, le bot appuie sur la
lettre affichée. Gère les checks enchaînés (jusqu'à 6, barre de taille variable,
barre qui passe au vert quand c'est réussi).

L'appli ILikeBaylife.pyw fait tout ça avec une interface. En ligne de commande :
    python autotouche.py --detecter         trouve le cercle tout seul (lance un skill-check dans les 5 s)
    python autotouche.py --zone             ou encadre le cercle à la souris
    python autotouche.py                    lancer
    python autotouche.py --debug            lancer + infos en direct
    python autotouche.py --test image.png   tester la détection sur une capture d'écran

Raccourcis pendant que ça tourne :
    F6  activer / pause
    F7  détecter le cercle (pendant qu'un skill-check est affiché)
    F8  apprendre une lettre : F8 pendant que le cercle est affiché, puis la lettre
    F10 quitter (ligne de commande seulement)
Apprentissage auto : sur une lettre inconnue, joue-la toi-même ; si la barre passe
au vert, le bot retient la lettre.
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import time
from datetime import datetime

import cv2
import mss
import numpy as np

MSS = getattr(mss, "MSS", None) or mss.mss

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")
TEMPLATES_DIR = os.path.join(BASE, "templates")
A_RENOMMER = "a_renommer"


def set_profil(nom):
    """Profil séparé (ex : 'sim') -> config_<nom>.json + templates_<nom>/ (ne touche pas au profil du jeu)."""
    global CONFIG_PATH, TEMPLATES_DIR
    if nom:
        CONFIG_PATH = os.path.join(BASE, f"config_{nom}.json")
        TEMPLATES_DIR = os.path.join(BASE, f"templates_{nom}")


def dossier_modeles():
    return os.path.basename(TEMPLATES_DIR)


DEFAULT_CONFIG = {
    "zone": None,                 # {"left", "top", "width", "height"} - carré autour du cercle
    "attendre_barre": True,       # True = appuie quand le rouge est dans le bleu ; False = dès que la lettre apparaît
    "touche_fixe": None,          # ex "E" : ne lit pas la lettre, appuie toujours sur celle-ci
    "seuil": 0.75,                # ressemblance mini (0..1) pour reconnaître une lettre
    "appui_ms": 20,               # durée de maintien de la touche
    "latence_ms": 25,             # anticipation : appuie un peu avant pour compenser le retard écran -> jeu
    "cible": 0.35,                # où viser dans la barre : 0 = dès l'entrée, 0.5 = au milieu
    "marge_deg": 3,               # jamais à moins de X degrés du bord de la barre
    "absence_ms": 40,             # le cercle doit disparaître X ms avant de réarmer
    "taille_lettre": 0.20,        # carré central lu pour la lettre (fraction de la zone) : doit rester DANS le carré sombre
    "anneau": [0.55, 1.05],       # rayons mini/maxi (fraction du rayon de la zone) où chercher barre et aiguille
    "anneau_auto": True,          # recale l'anneau sur le cercle noir (inutile après une détection F7)
    "fenetre_cible": None,        # ex "FiveM" : n'appuie que si la fenêtre au premier plan contient ce texte
    "apprentissage_auto": True,   # lettre inconnue jouée par toi + barre verte = lettre apprise
    "bleu_hsv": [[90, 100, 100], [130, 255, 255]],
    "rouge_hsv": [[0, 100, 90], [10, 255, 255], [168, 100, 90], [179, 255, 255]],
    "vert_hsv": [[45, 90, 90], [85, 255, 255]],
    "min_pixels_bleu": 20,
    "min_pixels_rouge": 5,
    "binaire": None,              # None = lettre en niveaux de gris ; 0-255 = noir/blanc avec ce seuil
    "pause_boucle_ms": 0,         # 0 = vitesse max ; 1 = moins de CPU
    "touche_on_off": "F6",
    "touche_detecter": "F7",
    "touche_capture": "F8",
    "touche_quitter": "F10",
}

# --------------------------------------------------------------------------
# Clavier (Windows SendInput en scancode -> marche dans les jeux)
# --------------------------------------------------------------------------
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
    scan = user32.MapVirtualKeyW(vk, 0)  # selon la disposition clavier active (AZERTY ok)
    ext = KEYEVENTF_EXTENDEDKEY if vk in EXTENDED_VK else 0
    _send(scan, ext)
    if duree_ms > 0:
        time.sleep(duree_ms / 1000.0)
    _send(scan, ext | KEYEVENTF_KEYUP)


class Raccourcis:
    """Front montant d'une touche via GetAsyncKeyState."""

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


# --------------------------------------------------------------------------
# Config / images
# --------------------------------------------------------------------------
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
    # imdecode : supporte les chemins avec accents (Bureau, Téléchargements...)
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
    """Garde le centre du carré lu : le modèle est un peu plus petit que la zone lue,
    donc la lettre est retrouvée même décalée de quelques pixels."""
    h, w = gris.shape[:2]
    m = max(2, int(round(min(h, w) * 0.12)))
    return gris[m:h - m, m:w - m].copy()


def lettre_plausible(gris):
    """Carré sombre avec une lettre claire dedans (évite d'apprendre un bout de décor)."""
    return gris is not None and float(np.median(gris)) < 90 and int(gris.max()) > 150


def nom_touche_depuis_fichier(fichier):
    # "Z.png" -> Z ; "Z_2.png" -> Z (plusieurs modèles possibles pour une même lettre)
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
    """Enregistre templates/<nom>.png (ou <nom>_2.png...). Renvoie le nom de fichier."""
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
    """Range une lettre de a_renommer/ comme modèle de la touche `nom` (ou la jette si nom vide)."""
    nom = (nom or "").strip().upper()
    if nom:
        if nom not in VK:
            raise ValueError(f"touche '{nom}' inconnue")
        img = lire_image(chemin)
        if img is not None:
            sauver_modele(nom, rogner_modele(img))
    os.remove(chemin)


def meilleur_match(gris, modeles):
    """(nom, vk, score) du modèle de lettre le plus ressemblant."""
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
    """Angle ramené dans [-180, 180)."""
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


# --------------------------------------------------------------------------
# Trouver le cercle tout seul sur une capture plein écran
# --------------------------------------------------------------------------
def detecter_cercle(img, cfg, centre_recherche=None, rayon_recherche=None):
    """Cherche le skill-check (anneau + barre bleue + aiguille rouge + lettre) près du centre.
    Renvoie {"centre": (x, y), "rayon": R, "anneau_px": (r_in, r_out)} en pixels de l'image, ou None."""
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
        return None  # il faut voir la barre ET l'aiguille

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
        # 1) une lettre claire dans un carré sombre au centre
        c = max(3, int(0.2 * R))
        cxi, cyi = int(round(x)), int(round(y))
        centre = v[max(0, cyi - c):cyi + c, max(0, cxi - c):cxi + c]
        if centre.size == 0 or not (np.median(centre) < 90 and centre.max() > 150):
            continue
        # 2) la barre bleue : un arc d'un seul tenant, toujours à la même distance du centre
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
        # 3) l'aiguille rouge sur l'anneau
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

    # recentrage sur la lettre blanche (elle est pile au centre du cercle)
    blanc = (hsv[..., 2] > 180) & (hsv[..., 1] < 60)
    yw, xw = np.nonzero(blanc)
    if len(xw):
        sel = np.hypot(xw - x, yw - y) < 0.26 * R
        if np.count_nonzero(sel) >= 8:
            lx, ly = float(xw[sel].mean()), float(yw[sel].mean())
            if np.hypot(lx - x, ly - y) < 6:
                x, y = (x + lx) / 2, (y + ly) / 2

    # épaisseur de l'anneau = où sont la barre et l'aiguille (seulement leur bloc : pas le décor)
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
    """Rayons où il y a vraiment de la matière (profil radial >= 30 % du pic, d'un seul tenant autour
    du pic) : les bords flous et les bouts de décor collés ne comptent pas."""
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
    """Petits traits rouges allongés d'une image -> [(cx, cy, dx, dy, points)] (axe principal)."""
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
        if vals[0] <= 0 or vals[1] / max(vals[0], 1e-6) < 6:   # doit être allongé (un trait)
            continue
        dx, dy = vecs[:, 1]
        if len(xs) > 60:
            k = np.linspace(0, len(xs) - 1, 60).astype(int)
            xs, ys = xs[k], ys[k]
        traits.append((mx, my, dx, dy, np.c_[xs, ys]))
    return traits


def detecter_cercle_anime(prendre, cfg, duree=1.2):
    """Détection pendant qu'un skill-check tourne. À chaque image, l'aiguille rouge (qui vient
    d'apparaître à cet endroit) est un petit trait qui pointe vers le centre : on cherche le point
    où tous ces traits se croisent (vote, le décor qui bouge est éliminé).
    prendre() renvoie la zone de recherche (BGRA). Coordonnées renvoyées : dans cette zone."""
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
        # rouge apparu depuis ~60 ms (l'aiguille a bougé ; le décor fixe disparaît)
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
    N = np.array([(-a[3], a[2]) for a in traits])          # normale de chaque trait
    c_ = np.einsum("ij,ij->i", N, P)
    rng = np.random.default_rng(0)
    meilleur, m = None, len(traits)
    for _ in range(400):
        i, j = rng.choice(m, 2, replace=False)
        A = np.array([N[i], N[j]])
        if abs(np.linalg.det(A)) < 0.3:                  # traits presque parallèles
            continue
        c = np.linalg.solve(A, [c_[i], c_[j]])
        ok = np.abs(N @ c - c_) < 3.0
        if meilleur is None or ok.sum() > meilleur.sum():
            meilleur = ok
    if meilleur is None or meilleur.sum() < 6:
        return _rien("les traits ne se croisent pas")
    # affinage : point le plus proche de tous les traits retenus
    Ni, ci = N[meilleur], c_[meilleur]
    cx, cy = np.linalg.solve(Ni.T @ Ni, Ni.T @ ci)
    # l'aiguille est toujours à la même distance du centre : on écarte les traits du décor
    rc = np.hypot(P[:, 0] - cx, P[:, 1] - cy)
    med = np.median(rc[meilleur])
    meilleur &= np.abs(rc - med) < 0.15 * med
    ang = np.degrees(np.arctan2(P[meilleur][:, 1] - cy, P[meilleur][:, 0] - cx)) % 360
    if meilleur.sum() < 6 or len(np.unique((ang // 15).astype(int))) < 6:  # assez d'angles balayés
        return _rien("balayage trop court")
    pts = np.vstack([traits[k][4] for k in np.flatnonzero(meilleur)])
    ai_in, ai_out = _bande_pleine(np.hypot(pts[:, 0] - cx, pts[:, 1] - cy))
    # anneau = partie extérieure de l'aiguille, là où est la barre (jeu : aiguille 52-80 px, barre 65-80 px).
    # On ne mesure pas la barre elle-même : un décor bleu collé pourrait la fausser.
    r_in = ai_in + 0.4 * (ai_out - ai_in) - 2
    r_out = ai_out + 1
    # vérif : une lettre claire dans un carré sombre au centre
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
    """Carré autour du centre de l'écran où chercher le skill-check."""
    s = int(min(mon["width"], mon["height"]) * 0.3)
    cx, cy = mon["left"] + mon["width"] // 2, mon["top"] + mon["height"] // 2
    return {"left": cx - s, "top": cy - s, "width": 2 * s, "height": 2 * s}


def trouver_cercle(sct, cfg, mon, duree=1.2):
    """Détection complète (aiguille qui tourne, sinon image fixe). Applique le résultat à cfg."""
    zr = zone_recherche(mon)
    det = detecter_cercle_anime(lambda: np.asarray(sct.grab(zr)), cfg, duree)
    if det:
        return appliquer_detection(cfg, det, (zr["left"], zr["top"]))
    det = detecter_cercle(np.asarray(sct.grab(mon)), cfg)
    if det:
        return appliquer_detection(cfg, det, (mon["left"], mon["top"]))
    return None


def appliquer_detection(cfg, det, decalage=(0, 0)):
    """Met la zone + l'anneau trouvés dans cfg. Garde la taille de zone si elle change peu
    (les lettres apprises restent valables)."""
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


# --------------------------------------------------------------------------
# Analyse de la zone : lettre au centre, barre bleue + aiguille rouge sur l'anneau
# --------------------------------------------------------------------------
class Analyseur:
    def __init__(self, cfg, h, w):
        self.cfg = cfg
        cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        dx, dy = xx - cx, yy - cy
        r = np.sqrt(dx * dx + dy * dy) / (min(h, w) / 2.0)
        ang = np.arctan2(dy, dx)                     # précalculé une fois -> rapide
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
        self.nb_bleu = self.nb_rouge = self.nb_vert = 0

    def set_anneau(self, r_in, r_out, calibre=False):
        self.anneau_r = (float(r_in), float(r_out))
        self.anneau = (((self.r >= r_in) & (self.r <= r_out)) * 255).astype(np.uint8)
        self.nb_anneau = max(1, cv2.countNonZero(self.anneau))
        self.calibre = calibre

    def calibrer(self, bgra):
        """Trouve l'anneau NOIR du cercle (profil radial des pixels sombres).
        Renvoie [r_in, r_out] ou None si pas clair (anneau transparent, décor sombre la nuit...)."""
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
        if b - a >= 0.10:  # un peu à l'intérieur : le décor visible juste au bord ne doit pas compter
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
        """Blocs d'angles d'un seul tenant (cases de 2°), en tolérant des trous de `trou` cases.
        `pont` = angles qui bouchent les trous (l'aiguille posée sur la barre la coupe en deux).
        Renvoie [(masque, nb_pixels, epaisseur)] ; epaisseur = pixels par case de 2° occupée."""
        nb = 180
        cases = (angles * 0.5).astype(np.int32) % nb
        h = np.bincount(cases, minlength=nb)
        occ = h > 0
        if pont is not None and pont.size:
            occ = occ | (np.bincount((pont * 0.5).astype(np.int32) % nb, minlength=nb) > 0)
        vides = np.flatnonzero(~occ)
        if vides.size == 0:
            return []  # tout l'anneau est coloré : ce n'est pas une barre
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
        """Masque des pixels situés dans une direction (case de 2°) qui en contient au moins
        30 % du maximum des directions les plus remplies."""
        cases = (angles * 0.5).astype(np.int32) % 180
        h = np.bincount(cases, minlength=180)
        ref = np.percentile(h[h > 0], 90) if np.any(h) else 0
        return h[cases] >= max(2, 0.3 * ref)

    @staticmethod
    def _bloc(angles, pont=None, trou=3):
        """Le plus gros bloc (masque) ou None."""
        blocs = Analyseur._blocs(angles, pont, trou)
        return max(blocs, key=lambda b: b[1])[0] if blocs else None

    @staticmethod
    def _bloc_epais(angles, mini, pont=None):
        """Le bloc le plus épais (vraie barre / aiguille) parmi ceux d'au moins `mini` pixels.
        Un liseré de décor au bord de l'anneau est fin : il est écarté."""
        blocs = [b for b in Analyseur._blocs(angles, pont) if b[1] >= mini]
        return max(blocs, key=lambda b: b[2])[0] if blocs else None

    @staticmethod
    def _moyenne(angles):
        rad = np.radians(angles)
        return float(np.degrees(np.arctan2(np.sin(rad).mean(), np.cos(rad).mean())) % 360.0)

    def barre(self, bgra):
        """(centre_bleu, debut, fin, rouge) - debut/fin/rouge relatifs au centre bleu, en degrés.
        None si pas de barre bleue ou pas d'aiguille rouge visible."""
        mb, mr, hsv = self.masques(bgra)
        self.nb_bleu, self.nb_rouge = cv2.countNonZero(mb), cv2.countNonZero(mr)
        if self.nb_bleu < self.min_b or self.nb_rouge < self.min_r:
            return None
        if self.calibre:
            # anneau noir calibré : le cercle est-il vraiment affiché ? (anneau noir / bleu / rouge presque partout)
            sombre = np.count_nonzero((hsv[..., 2] < 70) & (self.anneau > 0))
            if sombre + self.nb_bleu + self.nb_rouge < 0.7 * self.nb_anneau:
                return None
        ab, ar = self.angle[mb > 0], self.angle[mr > 0]
        # on ne garde que les directions bien remplies : un liseré de décor au bord de l'anneau
        # (1-2 pixels par direction) disparaît, la barre et l'aiguille restent
        ab, ar = ab[self._directions_pleines(ab)], ar[self._directions_pleines(ar)]
        if len(ab) < self.min_b or len(ar) < self.min_r:
            return None
        # barre = bloc bleu le plus épais (un bout de décor bleu ailleurs, ou un liseré, est ignoré)
        bloc = self._bloc_epais(ab, self.min_b, ar)
        if bloc is None:
            return None
        ab = ab[bloc]
        centre = self._moyenne(ab)
        debut, fin = np.percentile(wrap(ab - centre), (1, 99))
        if not 3 <= fin - debut <= 170:
            return None
        # aiguille = bloc rouge le plus épais (elle traverse tout l'anneau ; un liseré de décor est fin)
        bloc = self._bloc_epais(ar, self.min_r)
        if bloc is None:
            return None
        ar = ar[bloc]
        rouge = self._moyenne(ar)
        if np.ptp(wrap(ar - rouge)) > 40:
            return None
        return centre, float(debut), float(fin), float(wrap(rouge - centre))


class Decideur:
    """Décide quand appuyer. Une seule fois par skill-check."""

    def __init__(self, cfg):
        self.attendre = bool(cfg["attendre_barre"])
        self.lat = float(cfg["latence_ms"]) / 1000.0
        self.marge = float(cfg["marge_deg"])
        self.cible = min(0.5, max(0.0, float(cfg["cible"])))
        self.absence = float(cfg["absence_ms"]) / 1000.0
        self.reset()

    def reset(self):
        self.verrou = None      # (lettre, centre_bleu) du dernier appui
        self.t_vue = 0.0
        self.prev = None        # dernier angle rouge (absolu) observé
        self.prev_t = 0.0
        self.vit = None         # vitesse de l'aiguille en degrés/s (signée)
        self.t_sans_vit = None  # depuis quand on voit l'aiguille sans connaître sa vitesse

    def etape(self, nom, barre, now):
        if nom is None:  # pas de cercle à l'écran
            self.prev = self.vit = self.t_sans_vit = None
            if self.verrou and now - self.t_vue >= self.absence:
                self.verrou = None
            return False
        self.t_vue = now

        if not self.attendre:
            if self.verrou is None or self.verrou[0] != nom:
                self.verrou = (nom, None)
                return True
            return False

        if barre is None:
            self.prev = None
            return False
        centre, debut, fin, rel = barre

        # vitesse de l'aiguille (mise à jour seulement quand elle a bougé)
        rouge = (centre + rel) % 360.0
        saut = False
        if self.prev is None:
            self.prev, self.prev_t = rouge, now
        else:
            dv, dt = wrap(rouge - self.prev), now - self.prev_t
            if abs(dv) > 60 or dt > 0.25:          # aiguille repartie de zéro -> nouveau skill-check
                saut = abs(dv) > 60
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

        # déjà appuyé pour ce skill-check ? (nouvelle lettre, barre déplacée ou aiguille repartie = nouveau)
        if self.verrou:
            meme = (not saut and self.verrou[0] == nom and self.verrou[1] is not None
                    and abs(wrap(centre - self.verrou[1])) <= 15)
            if meme:
                return False
            self.verrou = None

        # on attend de connaître la vitesse pour viser juste (max 150 ms)
        if self.vit is None and now - self.t_sans_vit < 0.15:
            return False

        # position prévue de l'aiguille au moment où le jeu recevra la touche
        # (+ âge de l'image : depuis quand cette position est affichée)
        pred = rel + (self.vit or 0.0) * (self.lat + (now - self.prev_t))
        larg = fin - debut
        m = min(self.marge, larg * 0.25)
        entree = max(m, self.cible * larg)
        if self.vit is None or abs(self.vit) < 1:
            ok = debut + m <= pred <= fin - m
        elif self.vit > 0:                       # tourne dans le sens des angles croissants
            ok = debut + entree <= pred <= fin - m
        else:
            ok = debut + m <= pred <= fin - entree
        if ok:
            self.verrou = (nom, centre)
            return True
        return False


class CollecteurInconnues:
    """Sauve les lettres pas encore apprises (une seule fois chacune) dans templates/a_renommer/."""

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
        # attend 3 images identiques d'affilée (évite les images en pleine animation)
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


# --------------------------------------------------------------------------
# Le moteur (utilisé par la ligne de commande, l'appli et la simulation)
# --------------------------------------------------------------------------
def heure():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


class Moteur:
    """Le bot. run() tourne en boucle (dans un thread pour l'appli).
    Pilotage : .actif, .apercu, .demande_detection, .arreter(). État publié : image, barre, nom, score..."""

    def __init__(self, cfg, log=print, debug=False, actif=False, touche_quitter=True):
        self.cfg, self.debug, self.actif, self.touche_quitter = cfg, debug, actif, touche_quitter
        self._log_fn = log
        self.stop = False
        self.apercu = False             # True = analyse aussi en pause (aperçu de l'appli)
        self.raccourcis = True          # False = ignore F6/F7/F8 (ex : simulation lancée à côté)
        self.demande_detection = False
        self.demande_rechargement = False
        # état publié
        self.image = self.barre = self.nom = None
        self.score, self.fps = 0.0, 0.0
        self.cercle = False
        self.nb_appuis = self.nb_reussis = 0
        self.dernier = ""
        self.version_config = 0         # +1 quand la zone change (détection) : l'appli relit
        self.an = None
        self.modeles = []
        self.nb_modeles = 0

    # -------------------------------------------------------------- outils
    def log(self, msg):
        self.dernier = msg
        self._log_fn(f"[{heure()}] {msg}")

    def arreter(self):
        self.stop = True

    def basculer(self):
        self.actif = not self.actif
        if self.an:
            self.dec.reset()
        self.log("ACTIF" if self.actif else "EN PAUSE")

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
        return band is not None

    def detecter(self):
        self.log("Recherche du cercle...")
        trouve = None
        for _ in range(3):  # ~4 s : laisse le temps à un skill-check d'apparaître
            trouve = trouver_cercle(self.sct, self.cfg, self.sct.monitors[1])
            if trouve:
                break
        if trouve is None:
            self.log("Cercle pas trouvé : fais apparaître un skill-check (aiguille qui tourne) et réessaie")
            return False
        sauver_config(self.cfg)
        self._init_zone()
        self.version_config += 1
        z = self.cfg["zone"]
        self.log(f"Cercle trouvé : centre ({z['left'] + z['width'] // 2}, {z['top'] + z['height'] // 2}), "
                 f"zone {z['width']} px")
        return True

    # -------------------------------------------------------------- boucle
    def run(self):
        cfg = self.cfg
        vk = lambda k: VK[str(cfg[k]).upper()]  # noqa: E731
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
        candidat = None          # (lettre tapée par toi, image, instant) en attente du vert
        guette_lettres = False
        t_vert = None            # attend le vert après un appui du bot (= réussi)
        taille_barre = 0.0       # nb de pixels de la barre bleue (pour reconnaître la barre devenue verte)
        nb_frames, t_fps, t_debug = 0, time.perf_counter(), 0.0
        self._init_zone()

        with MSS() as sct:
            self.sct = sct
            while not self.stop:
                # --- raccourcis / demandes de l'appli ---
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

                # --- F8 : on attend le nom de la lettre ---
                if lettre_capturee is not None:
                    for nom, v in lettres_vk:
                        if rac.appui(v):
                            f = sauver_modele(nom, rogner_modele(lettre_capturee))
                            lettre_capturee = None
                            self.recharger_modeles()
                            self.dec.reset()
                            self.log(f"Lettre apprise : {nom} ({dossier_modeles()}/{f})")
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
                    time.sleep(0.03)  # en pause : on regarde quand même (aperçu, apprentissage), moins vite

                # --- détection ---
                an = self.an
                frame = np.asarray(sct.grab(self.zone))
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
                maintenant = time.perf_counter()
                reconnu = nom if score >= seuil else None
                self.image, self.barre, self.nom, self.score = frame, barre, nom, score
                self.cercle = bool(reconnu or barre)
                # réussi = la barre devient verte : le vert REMPLACE le bleu (même taille, plus de bleu).
                # Un bout de décor vert derrière l'anneau ne compte donc pas.
                if barre is not None:
                    taille_barre = an.nb_bleu if not taille_barre else 0.8 * taille_barre + 0.2 * an.nb_bleu
                vert = bool(taille_barre and an.nb_vert >= max(8, 0.4 * taille_barre)
                            and an.nb_bleu < 0.3 * taille_barre)

                # --- appui ---
                if self.actif and self.dec.etape(reconnu, barre if self.dec.attendre else None, maintenant):
                    titre = titre_premier_plan() if cible else ""
                    if cible and cible.lower() not in titre.lower():
                        self.log(f"{nom} PAS envoyé : fenêtre active « {titre[:40]} » (attendu « {cible} »)")
                    else:
                        appuyer(vkn, appui_ms)
                        self.nb_appuis += 1
                        t_vert = maintenant
                        info = ""
                        if barre:
                            _, debut, fin, rel = barre
                            info = f" | rouge {rel:+.0f}° dans [{debut:+.0f}°, {fin:+.0f}°]"
                        self.log(f"-> {nom}  (lettre {score:.2f}{info})")

                # --- barre verte = réussi (et ce check est fini : on réarme pour le suivant) ---
                if vert:
                    self.dec.verrou = None
                if t_vert is not None:
                    if vert:
                        self.nb_reussis += 1
                        t_vert = None
                    elif maintenant - t_vert > 0.6:
                        t_vert = None

                # --- lettre inconnue ---
                inconnue = (not fixe and reconnu is None and barre is not None and lettre_plausible(gris))
                if inconnue:
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
                        if vert:
                            sauver_modele(n, rogner_modele(g))
                            self.recharger_modeles()
                            self.log(f"Lettre apprise toute seule : {n} (tu l'as jouée, barre verte)")
                            candidat = None
                        elif maintenant - t > 0.6:
                            candidat = None

                # --- stats / debug ---
                nb_frames += 1
                if maintenant - t_fps >= 1.0:
                    self.fps, nb_frames, t_fps = nb_frames / (maintenant - t_fps), 0, maintenant
                if self.debug and maintenant - t_debug >= 0.15:
                    t_debug = maintenant
                    b = (f"bleu [{barre[1]:+4.0f},{barre[2]:+4.0f}] rouge {barre[3]:+5.0f}°"
                         if barre else f"bleu {an.nb_bleu:4d}px rouge {an.nb_rouge:3d}px")
                    v = f"{self.dec.vit:+5.0f}°/s" if self.dec.vit is not None else "   -   "
                    print(f"  lettre {nom or '-':>5} {score:4.2f} | {b} | vit {v} | vert {an.nb_vert:3d} | "
                          f"{self.fps:5.0f}/s   ", end="\r", flush=True)
                if pause_s:
                    time.sleep(pause_s)
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


# --------------------------------------------------------------------------
# Choix de la zone (ligne de commande)
# --------------------------------------------------------------------------
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
        # essaie de recaler pile sur le cercle à l'intérieur de la sélection
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


# --------------------------------------------------------------------------
# Test sur une image
# --------------------------------------------------------------------------
def tester_image(cfg, chemin):
    img = lire_image(chemin, cv2.IMREAD_COLOR)
    if img is None:
        print("Image illisible :", chemin)
        return 1
    if max(img.shape[:2]) > 600:  # capture plein écran : on cherche le cercle dedans
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
    for rr in an.anneau_r:  # zone où on cherche barre + aiguille (vert)
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
    try:  # vrais pixels même avec un zoom Windows à 125/150 %
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
