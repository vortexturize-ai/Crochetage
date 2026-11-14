import ctypes
import ctypes.wintypes as wt
import math
import os
import time
import traceback

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

import autotouche as at

SS = 3
WDA_EXCLUDEFROMCAPTURE = 0x11
WS_POPUP = 0x80000000
WS_EX = 0x80000 | 0x20 | 0x8 | 0x80 | 0x8000000
HWND_TOPMOST = wt.HWND(-1)
SWP_TOP = 0x1 | 0x2 | 0x10

BLANC, NOIR = "#FFFFFF", "#000000"
CYAN, ROUGE, VERT, ORANGE, GRIS = "#7FDBFF", "#FF5A5F", "#3DDC84", "#FFB347", "#9AA0AD"
FOND_PILULE = "#0D0F14"

u32 = ctypes.WinDLL("user32", use_last_error=True)
g32 = ctypes.WinDLL("gdi32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR), ("hIconSm", wt.HICON)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


u32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
u32.DefWindowProcW.restype = LRESULT
u32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
u32.RegisterClassExW.restype = wt.ATOM
u32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
u32.CreateWindowExW.restype = wt.HWND
u32.DestroyWindow.argtypes = [wt.HWND]
u32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
u32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
u32.SetWindowDisplayAffinity.argtypes = [wt.HWND, wt.DWORD]
u32.SetWindowDisplayAffinity.restype = wt.BOOL
u32.GetDC.argtypes = [wt.HWND]
u32.GetDC.restype = wt.HDC
u32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
u32.GetSystemMetrics.argtypes = [ctypes.c_int]
u32.UpdateLayeredWindow.argtypes = [wt.HWND, wt.HDC, ctypes.POINTER(wt.POINT), ctypes.POINTER(wt.SIZE), wt.HDC,
                                    ctypes.POINTER(wt.POINT), wt.COLORREF, ctypes.POINTER(BLENDFUNCTION), wt.DWORD]
u32.UpdateLayeredWindow.restype = wt.BOOL
g32.CreateCompatibleDC.argtypes = [wt.HDC]
g32.CreateCompatibleDC.restype = wt.HDC
g32.CreateDIBSection.argtypes = [wt.HDC, ctypes.POINTER(BITMAPINFOHEADER), wt.UINT, ctypes.POINTER(ctypes.c_void_p),
                                 wt.HANDLE, wt.DWORD]
g32.CreateDIBSection.restype = wt.HBITMAP
g32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
g32.SelectObject.restype = wt.HGDIOBJ
g32.DeleteObject.argtypes = [wt.HGDIOBJ]
g32.DeleteDC.argtypes = [wt.HDC]
k32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
k32.GetModuleHandleW.restype = wt.HMODULE

CLASSE = "CrochetageOverlay"
_PROC = WNDPROC(ctypes.cast(u32.DefWindowProcW, ctypes.c_void_p).value)


def _enregistrer_classe():
    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.lpfnWndProc = _PROC
    wc.hInstance = k32.GetModuleHandleW(None)
    wc.lpszClassName = CLASSE
    if not u32.RegisterClassExW(ctypes.byref(wc)):
        err = ctypes.get_last_error()
        if err != 1410:
            raise ctypes.WinError(err)


def _rgb(c):
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def pm(couleur, a=1.0):
    r, g, b = _rgb(couleur)
    a = max(0.0, min(1.0, a))
    return (b * a, g * a, r * a, 255 * a)


def _police(taille):
    for nom in ("seguisb.ttf", "segoeuib.ttf", "segoeui.ttf", "arial.ttf"):
        chemin = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", nom)
        if os.path.exists(chemin):
            return ImageFont.truetype(chemin, taille)
    return ImageFont.load_default()


class Rendu:
    def __init__(self):
        self.polices = {}
        self.textes = {}

    def police(self, taille):
        if taille not in self.polices:
            self.polices[taille] = _police(taille)
        return self.polices[taille]

    def texte(self, texte, taille):
        cle = (texte, taille)
        if cle not in self.textes:
            p = self.police(taille)
            x0, y0, x1, y1 = p.getbbox(texte)
            masque = Image.new("L", (x1 - x0 + 2, y1 - y0 + 2), 0)
            ImageDraw.Draw(masque).text((1 - x0, 1 - y0), texte, font=p, fill=255)
            if len(self.textes) > 64:
                self.textes.clear()
            self.textes[cle] = np.asarray(masque, np.float32) / 255.0
        return self.textes[cle]

    @staticmethod
    def taille(r_ext, u):
        demi = r_ext + 28 * u
        w = int(math.ceil(max(2 * demi, 300 * u)))
        h = int(math.ceil(2 * demi + 40 * u))
        return w, h

    def dessiner(self, e):
        u, r = e["u"], e["r_ext"]
        w, h = self.taille(r, u)
        cx, cy = e["cx"], e["cy"]
        img = np.zeros((h * SS, w * SS, 4), np.uint8)
        k = SS * 16

        def P(x, y):
            return int(round(x * k)), int(round(y * k))

        def cercle(rr, coul, ep, a0=0.0, a1=360.0):
            if a1 - a0 <= 0.05 or rr <= 0:
                return
            cv2.ellipse(img, P(cx, cy), (int(round(rr * k)), int(round(rr * k))), 0, a0, a1, coul,
                        max(1, int(round(ep * SS))), cv2.LINE_AA, 4)

        def point(a, rr, ray, coul):
            x, y = cx + rr * math.cos(math.radians(a)), cy + rr * math.sin(math.radians(a))
            cv2.circle(img, P(x, y), int(round(ray * k)), coul, -1, cv2.LINE_AA, 4)

        piste = r + 9 * u
        if e["anneau"]:
            teinte = e.get("teinte")
            if teinte:
                cercle(piste, pm(teinte, 0.22 * e.get("eclat", 1.0)), 9 * u)
                cercle(piste, pm(teinte, 0.95), 2.4 * u)
            else:
                cercle(piste, pm(BLANC, 0.16), 2 * u)
            if e.get("barre"):
                a0, a1 = e["barre"]
                cercle(piste, pm(CYAN, 0.95), 3.6 * u, a0, a1)
            if e.get("aiguille") is not None:
                a = e["aiguille"]
                sens = e.get("sens", 1.0)
                cercle(piste, pm(ROUGE, 0.35), 2.2 * u, *sorted((a - 16 * sens, a)))
                point(a, piste, 3.4 * u, pm(ROUGE))
            if e.get("cible") is not None:
                point(e["cible"], piste, 4.6 * u, pm(NOIR, 0.55))
                point(e["cible"], piste, 3.0 * u, pm(BLANC))
            if e.get("impulsion") is not None:
                p = e["impulsion"]
                cercle(piste + 10 * u * p, pm(BLANC, 0.7 * (1 - p)), (2.2 - p) * u)

        pil = e.get("pilule")
        if pil:
            texte, point_coul = pil
            taille = max(9, int(round(13 * u)))
            masque = self.texte(texte, taille)
            th, tw = masque.shape
            hp = 26 * u
            pad, rp = 11 * u, 3.6 * u
            lw = pad + 2 * rp + 8 * u + tw + pad
            x0, y0 = cx - lw / 2, cy + r + 20 * u
            rr = hp / 2
            fond = pm(FOND_PILULE, 0.86)
            cv2.rectangle(img, P(x0 + rr, y0), P(x0 + lw - rr, y0 + hp), fond, -1, cv2.LINE_AA, 4)
            cv2.circle(img, P(x0 + rr, y0 + rr), int(round(rr * k)), fond, -1, cv2.LINE_AA, 4)
            cv2.circle(img, P(x0 + lw - rr, y0 + rr), int(round(rr * k)), fond, -1, cv2.LINE_AA, 4)
            cv2.circle(img, P(x0 + pad + rp, y0 + rr), int(round(rp * k)), pm(point_coul), -1, cv2.LINE_AA, 4)
            tx, ty = x0 + pad + 2 * rp + 8 * u, y0 + rr - th / 2
        img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA).astype(np.float32)
        if pil:
            ix, iy = int(round(tx)), int(round(ty))
            x1, y1 = min(w, ix + tw), min(h, iy + th)
            if x1 > ix and y1 > iy and ix >= 0 and iy >= 0:
                m = masque[:y1 - iy, :x1 - ix, None] * 0.94
                zone = img[iy:y1, ix:x1]
                zone[:] = np.array(pm(BLANC), np.float32) * m + zone * (1 - m)
        return np.clip(img + 0.5, 0, 255).astype(np.uint8)


class FenetreCouche:
    def __init__(self, exclure=True):
        _enregistrer_classe()
        self.hwnd = u32.CreateWindowExW(WS_EX, CLASSE, "Crochetage overlay", WS_POPUP, 0, 0, 1, 1,
                                        None, None, k32.GetModuleHandleW(None), None)
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self.exclu = bool(exclure) and bool(u32.SetWindowDisplayAffinity(self.hwnd, WDA_EXCLUDEFROMCAPTURE))
        self.dc_ecran = u32.GetDC(None)
        self.dc = g32.CreateCompatibleDC(self.dc_ecran)
        self.bmp = self.ancien = None
        self.bits = ctypes.c_void_p()
        self.taille = (0, 0)
        self.montree = False

    def _bitmap(self, w, h):
        if (w, h) == self.taille:
            return
        if self.bmp:
            g32.SelectObject(self.dc, self.ancien)
            g32.DeleteObject(self.bmp)
        bi = BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.biWidth, bi.biHeight, bi.biPlanes, bi.biBitCount = w, -h, 1, 32
        self.bmp = g32.CreateDIBSection(self.dc, ctypes.byref(bi), 0, ctypes.byref(self.bits), None, 0)
        self.ancien = g32.SelectObject(self.dc, self.bmp)
        self.taille = (w, h)

    def afficher(self, img, x, y, alpha):
        h, w = img.shape[:2]
        self._bitmap(w, h)
        ctypes.memmove(self.bits, np.ascontiguousarray(img).ctypes.data, w * h * 4)
        bf = BLENDFUNCTION(0, 0, max(0, min(255, int(alpha))), 1)
        ok = u32.UpdateLayeredWindow(self.hwnd, self.dc_ecran, ctypes.byref(wt.POINT(x, y)), ctypes.byref(wt.SIZE(w, h)),
                                     self.dc, ctypes.byref(wt.POINT(0, 0)), 0, ctypes.byref(bf), 2)
        if not self.montree:
            u32.ShowWindow(self.hwnd, 4)
            self.montree = True
        return bool(ok)

    def alpha(self, alpha):
        if self.taille == (0, 0):
            return
        bf = BLENDFUNCTION(0, 0, max(0, min(255, int(alpha))), 1)
        u32.UpdateLayeredWindow(self.hwnd, None, None, None, None, None, 0, ctypes.byref(bf), 2)

    def cacher(self):
        if self.montree:
            u32.ShowWindow(self.hwnd, 0)
            self.montree = False

    def devant(self):
        u32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_TOP)

    def fermer(self):
        try:
            self.cacher()
            if self.bmp:
                g32.SelectObject(self.dc, self.ancien)
                g32.DeleteObject(self.bmp)
            g32.DeleteDC(self.dc)
            u32.ReleaseDC(None, self.dc_ecran)
            u32.DestroyWindow(self.hwnd)
        except Exception:
            pass


def etat_moteur(m, now):
    an = m.an
    zone = getattr(m, "zone", None) if an is not None else None
    if zone:
        cx = zone["left"] + (zone["width"] - 1) / 2.0
        cy = zone["top"] + (zone["height"] - 1) / 2.0
        r_ext = an.anneau_r[1] * min(zone["width"], zone["height"]) / 2.0
    else:
        mon = {"left": 0, "top": 0, "width": u32.GetSystemMetrics(0), "height": u32.GetSystemMetrics(1)}
        g = at.geometrie_jeu(mon)
        (cx, cy), r_ext = g["centre"], g["rayon"]
    e = {"centre": (cx, cy), "r_ext": r_ext, "u": max(0.6, r_ext / 82.2), "anneau": False}

    cercle = bool(an is not None and m.cercle)
    barre, dec = (m.barre if cercle else None), getattr(m, "dec", None)
    seuil = float(m.cfg.get("seuil", 0.75))
    reconnu = m.nom if (cercle and m.nom and m.score >= seuil) else None
    su = getattr(m, "suivi", None)
    dc = su.dernier_cycle if su else None
    fin = dc if (dc and now - dc["t"] < 1.8) else None
    if fin and not cercle:
        e["anneau"] = True
        e["teinte"] = VERT if fin["resultat"] == "crocheté" else ROUGE
        e["eclat"] = 1.0 - (now - fin["t"]) / 1.8
    elif cercle:
        e["anneau"] = True
        if m.vert:
            e["teinte"] = VERT
    if cercle and barre is not None and not m.vert:
        centre, debut, fin_b, rel = barre
        e["barre"] = (centre + debut, centre + fin_b)
        e["aiguille"] = centre + rel
        vit = dec.vit if dec is not None else None
        e["sens"] = -1.0 if (vit is not None and vit < 0) else 1.0
        if dec is not None and m.actif and not dec.verrou and reconnu:
            lo, hi = dec.fenetre(debut, fin_b)
            if hi > lo:
                e["cible"] = centre + (hi if e["sens"] < 0 else lo)
    if cercle and now - m.t_appui < 0.3:
        e["impulsion"] = (now - m.t_appui) / 0.3

    n_check = None
    cycle, check = (su.cycle, su.check) if su else (None, None)
    if cycle:
        n_check = check["n"] if check else len(cycle["checks"]) + 1
    if not m.serveur_ok:
        pil = ("Hors Baylife", GRIS)
    elif m.detection_en_cours:
        pil = ("Recherche de l'anneau", CYAN)
    elif now - m.t_refus < 1.5:
        pil = (f"Appui bloqué · {m.refus}", ORANGE)
    elif fin and not cercle and fin["resultat"] == "crocheté":
        pil = (f"Crocheté · {fin['nb_checks']}/{fin['nb_checks']}" if fin.get("bot", True)
               else "Crocheté à la main", VERT)
    elif fin and not cercle and fin["resultat"] == "raté":
        pil = (f"Raté au check {fin['nb_checks']}", ROUGE)
    elif cercle and m.vert:
        pil = ("Réussi", VERT)
    elif cercle and now - m.t_appui < 0.35 and m.appui_nom:
        pil = (f"Appui {m.appui_nom}", BLANC)
    elif cercle and m.inconnue:
        pil = ("Lettre inconnue", ORANGE)
    elif cercle and reconnu:
        pil = (f"{reconnu} · check {n_check}" if n_check else reconnu, CYAN)
    elif cercle:
        pil = ("Lecture", GRIS)
    else:
        pil = ("Prêt", VERT)
        e["repos"] = True
    e["pilule"] = pil
    return e


class OverlayAnneau:
    FONDU_S = 0.14

    def __init__(self, root, moteur_fn, visible_fn, f=1.0, exclure=True, periode_ms=16):
        self.root, self.moteur_fn, self.visible_fn = root, moteur_fn, visible_fn
        self.periode = periode_ms
        self.fenetre = FenetreCouche(exclure)
        self.exclu = self.fenetre.exclu
        self.rendu = Rendu()
        self.ferme = False
        self.alpha, self.t_prec, self.t_top = 0.0, time.perf_counter(), 0.0
        self.signature, self.pos = None, None
        self.nb_ticks, self.nb_rendus, self.erreur = 0, 0, None
        root.after(self.periode, self._tick)

    def exclusion(self, oui):
        return bool(u32.SetWindowDisplayAffinity(self.fenetre.hwnd, WDA_EXCLUDEFROMCAPTURE if oui else 0))

    def fermer(self):
        self.ferme = True
        self.fenetre.fermer()

    def _tick(self):
        if self.ferme:
            return
        self.nb_ticks += 1
        try:
            self.maj()
        except Exception:
            erreur = traceback.format_exc()
            if erreur != self.erreur:
                self.erreur = erreur
                m = self.moteur_fn()
                if m is not None and hasattr(m, "ev"):
                    m.ev("erreur", ou="overlay", trace=erreur)
        self.root.after(self.periode, self._tick)

    def maj(self):
        now = time.perf_counter()
        dt, self.t_prec = now - self.t_prec, now
        m = self.moteur_fn()
        visible = m is not None and self.visible_fn(m)
        e = etat_moteur(m, now) if visible else None
        if e is not None and not self.exclu:
            e = dict(e, anneau=False)
        cible = 0.0 if e is None else (0.78 if e.get("repos") else 1.0)
        pas = dt / self.FONDU_S
        self.alpha = min(cible, self.alpha + pas) if self.alpha < cible else max(cible, self.alpha - pas)
        if self.alpha <= 0.0:
            self.fenetre.cacher()
            self.signature = None
            return
        if e is not None:
            self._dessiner(e)
        self.fenetre.alpha(255 * self.alpha)
        if now - self.t_top > 0.5:
            self.t_top = now
            self.fenetre.devant()

    def _dessiner(self, e):
        cx, cy = e["centre"]
        w, h = Rendu.taille(e["r_ext"], e["u"])
        demi = e["r_ext"] + 28 * e["u"]
        x0, y0 = int(math.floor(cx - w / 2.0)), int(math.floor(cy - demi))
        e = dict(e, cx=cx - x0, cy=cy - y0)
        sig = tuple(sorted((k, round(v, 1) if isinstance(v, float) else
                            tuple(round(x, 1) for x in v) if isinstance(v, tuple) and all(isinstance(x, float) for x in v)
                            else v) for k, v in e.items()))
        if sig == self.signature and (x0, y0) == self.pos:
            return
        self.signature, self.pos = sig, (x0, y0)
        img = self.rendu.dessiner(e)
        self.nb_rendus += 1
        self.fenetre.afficher(img, x0, y0, 255 * self.alpha)
