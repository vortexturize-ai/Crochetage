import ctypes
import ctypes.wintypes as wt
import math
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
import tkinter as tk
from collections import deque

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)
sys.path.insert(0, BASE)

_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\Crochetage_appli")
if ctypes.windll.kernel32.GetLastError() == 183:
    ctypes.windll.user32.MessageBoxW(None, "Crochetage tourne déjà : clique sur son icône près de l'horloge.",
                                     "Crochetage", 0x40)
    sys.exit(0)

import numpy as np
import pystray
from PIL import Image, ImageDraw, ImageTk

import autotouche as at

ROUGE, ROUGE_FONCE, ROUGE_CLAIR = "#E32344", "#B8162F", "#FF5872"
FOND, NOIR, CARTE, CARTE2, BORD = "#0E0E11", "#08080A", "#17171C", "#212128", "#2E2E37"
TEXTE, TEXTE2, TEXTE3 = "#F4F4F6", "#A2A2AE", "#6C6C78"
POLICE = "Segoe UI"
W, H = 340, 620

LOG = os.path.join(BASE, "crochetage.log")
RUN_CLE = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NOM, RUN_ANCIEN = "Crochetage", "ILikeBaylife"


def pythonw():
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        w = exe[:-10] + "pythonw.exe"
        if os.path.exists(w):
            return w
    return exe


def demarrage_windows(etat=None):
    import winreg

    def existe(k, nom):
        try:
            winreg.QueryValueEx(k, nom)
            return True
        except FileNotFoundError:
            return False

    def supprimer(k, nom):
        try:
            winreg.DeleteValue(k, nom)
        except FileNotFoundError:
            pass

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_CLE, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
        if etat is None:
            if existe(k, RUN_ANCIEN):
                supprimer(k, RUN_ANCIEN)
                etat = True
            else:
                return existe(k, RUN_NOM)
        if etat:
            winreg.SetValueEx(k, RUN_NOM, 0, winreg.REG_SZ,
                              f'"{pythonw()}" "{os.path.join(BASE, "Crochetage.pyw")}"')
        else:
            supprimer(k, RUN_NOM)
        return etat


def charger_logo():
    try:
        im = Image.open(os.path.join(BASE, "logo.png")).convert("RGBA")
    except Exception:
        return None
    bbox = im.getchannel("A").point(lambda a: 255 if a > 90 else 0).getbbox()
    if bbox:
        im = im.crop(bbox)
    s = max(im.size)
    carre = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    carre.paste(im, ((s - im.width) // 2, (s - im.height) // 2))
    a = np.array(carre)
    a[..., 3] = np.where(a[..., 3] > 90, a[..., 3], 0)
    return Image.fromarray(a)


LOGO = charger_logo()


def icone(actif, taille=64):
    if LOGO is None:
        return _icone_dessinee(actif, taille)
    im = LOGO.resize((taille, taille), Image.LANCZOS)
    if not actif:
        a = np.array(im).astype(np.float32)
        gris = a[..., :3].mean(axis=2, keepdims=True) * 0.55 + 70
        a[..., :3] = np.clip(gris, 0, 255)
        im = Image.fromarray(a.astype(np.uint8))
    return im


def badge(taille):
    s = taille * 4
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.26), fill=(10, 10, 12, 255))
    if LOGO is not None:
        m = int(s * 0.74)
        im.alpha_composite(LOGO.resize((m, m), Image.LANCZOS), ((s - m) // 2, (s - m) // 2))
    return im.resize((taille, taille), Image.LANCZOS)


def _icone_dessinee(actif, taille=64):
    s = taille * 4
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.24), fill=(10, 10, 12, 255))
    c, r, w = s / 2, s * 0.32, int(s * 0.12)
    coul = (227, 35, 68, 255) if actif else (110, 110, 122, 255)
    d.ellipse([c - r, c - r, c + r, c + r], outline=(44, 44, 54, 255), width=w)
    d.arc([c - r, c - r, c + r, c + r], start=105, end=175, fill=coul, width=w)
    a = math.radians(140)
    d.line([c + (r - w * 1.1) * math.cos(a), c + (r - w * 1.1) * math.sin(a),
            c + (r + w * 0.1) * math.cos(a), c + (r + w * 0.1) * math.sin(a)],
           fill=(255, 255, 255, 255), width=int(w * 0.45))
    b = s * 0.1
    d.rounded_rectangle([c - b, c - b, c + b, c + b], radius=int(s * 0.03), fill=coul)
    return im.resize((taille, taille), Image.LANCZOS)


def rrect(c, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


class Bouton(tk.Canvas):
    STYLES = {
        "primaire": (ROUGE, ROUGE_CLAIR, ROUGE, "white"),
        "contour": (CARTE2, "#2A1218", ROUGE, ROUGE_CLAIR),
        "secondaire": (CARTE2, BORD, BORD, TEXTE),
        "danger": (CARTE2, "#2A1218", BORD, ROUGE_CLAIR),
    }

    def __init__(self, parent, texte, commande, largeur, hauteur, f, style="primaire", bg=CARTE, taille=14):
        super().__init__(parent, width=largeur, height=hauteur, bg=bg, highlightthickness=0, cursor="hand2")
        self.commande, self.style, self.survol = commande, style, False
        self.fond = rrect(self, 1, 1, largeur - 2, hauteur - 2, hauteur * 0.24, width=max(1, int(f)))
        self.txt = self.create_text(largeur / 2, hauteur / 2, text=texte, font=(POLICE, -int(taille * f), "bold"))
        self.bind("<Enter>", lambda e: self._survol(True))
        self.bind("<Leave>", lambda e: self._survol(False))
        self.bind("<ButtonRelease-1>", lambda e: self.commande() if self.commande else None)
        self._peindre()

    def _survol(self, oui):
        self.survol = oui
        self._peindre()

    def _peindre(self):
        fond, fond_s, contour, texte = self.STYLES[self.style]
        self.itemconfigure(self.fond, fill=fond_s if self.survol else fond, outline=contour)
        self.itemconfigure(self.txt, fill=texte)

    def changer(self, texte=None, style=None):
        if texte is not None:
            self.itemconfigure(self.txt, text=texte)
        if style is not None and style != self.style:
            self.style = style
            self._peindre()


class Interrupteur(tk.Canvas):
    def __init__(self, parent, valeur, commande, f, bg=CARTE):
        self.f, self.valeur, self.commande = f, bool(valeur), commande
        w, h = int(44 * f), int(24 * f)
        super().__init__(parent, width=w, height=h, bg=bg, highlightthickness=0, cursor="hand2")
        self.piste = rrect(self, 1, 1, w - 1, h - 1, h / 2, outline="")
        self.rond = self.create_oval(0, 0, 1, 1, fill="white", outline="")
        self.bind("<ButtonRelease-1>", self._clic)
        self._peindre()

    def _clic(self, _):
        self.valeur = not self.valeur
        self._peindre()
        self.commande(self.valeur)

    def _peindre(self):
        w, h, m = int(self["width"]), int(self["height"]), 3 * self.f
        self.itemconfigure(self.piste, fill=ROUGE if self.valeur else BORD)
        x = w - h + m if self.valeur else m
        self.coords(self.rond, x, m, x + h - 2 * m, h - m)


class Curseur(tk.Canvas):
    def __init__(self, parent, mini, maxi, valeur, fmt, commande, largeur, f, bg=CARTE):
        self.mini, self.maxi, self.valeur, self.fmt, self.commande, self.f = mini, maxi, valeur, fmt, commande, f
        h = int(30 * f)
        super().__init__(parent, width=largeur, height=h, bg=bg, highlightthickness=0, cursor="hand2")
        self.x0, self.x1 = 9 * f, largeur - 62 * f
        y = h / 2
        self.piste = self.create_line(self.x0, y, self.x1, y, fill=BORD, width=4 * f, capstyle="round")
        self.plein = self.create_line(self.x0, y, self.x0, y, fill=ROUGE, width=4 * f, capstyle="round")
        self.rond = self.create_oval(0, 0, 1, 1, fill="white", outline="")
        self.txt = self.create_text(largeur - 4 * f, y, anchor="e", fill=TEXTE, font=(POLICE, -int(13 * f), "bold"))
        self.bind("<Button-1>", self._bouge)
        self.bind("<B1-Motion>", self._bouge)
        self.bind("<ButtonRelease-1>", lambda e: self.commande(self.valeur))
        self._peindre()

    def _bouge(self, e):
        t = min(1.0, max(0.0, (e.x - self.x0) / (self.x1 - self.x0)))
        self.valeur = round(self.mini + t * (self.maxi - self.mini))
        self._peindre()

    def _peindre(self):
        t = (self.valeur - self.mini) / (self.maxi - self.mini)
        x, y, r = self.x0 + t * (self.x1 - self.x0), int(self["height"]) / 2, 8 * self.f
        self.coords(self.plein, self.x0, y, x, y)
        self.coords(self.rond, x - r, y - r, x + r, y + r)
        self.itemconfigure(self.txt, text=self.fmt(self.valeur))


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.f = self.root.winfo_fpixels("1i") / 96.0
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=FOND)
        self.root.title("Crochetage")
        self.q = queue.Queue()
        self.journal = deque(maxlen=200)
        self.visible, self.t_montre, self.vue = False, 0.0, None
        self.sim = None
        self.photo = None
        self._ouvrir_log()

        at.set_profil(None)
        neuf = not os.path.exists(at.CONFIG_PATH)
        self.cfg = at.charger_config()
        if neuf:
            self.cfg["fenetre_cible"] = "FiveM"
            at.sauver_config(self.cfg)
        self.version_cfg = -1

        self.moteur = None
        self.demarrer_moteur(actif=False)
        self.construire()
        self.creer_icone()
        self.root.bind("<FocusOut>", lambda e: self.root.after(250, self._verifier_focus))
        self.root.after(60, self.boucle)
        self.root.after(300, self.montrer)

    def px(self, v):
        return int(round(v * self.f))

    def police(self, taille, gras=False):
        return (POLICE, -self.px(taille), "bold") if gras else (POLICE, -self.px(taille))

    def _ouvrir_log(self):
        try:
            if os.path.exists(LOG) and os.path.getsize(LOG) > 1_000_000:
                os.remove(LOG)
        except OSError:
            pass

    def noter(self, msg):
        self.journal.append(msg)
        try:
            with open(LOG, "a", encoding="utf-8") as fl:
                fl.write(msg + "\n")
        except OSError:
            pass

    def demarrer_moteur(self, actif=None):
        ancien = self.moteur
        if ancien:
            ancien.arreter()
            if actif is None:
                actif = ancien.actif
        m = at.Moteur(self.cfg, log=self.noter, actif=bool(actif), touche_quitter=False)
        if ancien:
            m.nb_appuis, m.nb_reussis = ancien.nb_appuis, ancien.nb_reussis
            m.nb_crochetages, m.nb_rates = ancien.nb_crochetages, ancien.nb_rates
            m.raccourcis = ancien.raccourcis
        self.moteur = m

        def run():
            try:
                m.run()
            except Exception:
                self.noter("ERREUR du bot : " + traceback.format_exc().strip().splitlines()[-1])

        threading.Thread(target=run, daemon=True).start()

    def basculer(self):
        self.moteur.basculer()
        self.maj_icone()

    def reglage(self, cle, valeur):
        self.cfg[cle] = valeur
        at.sauver_config(self.cfg)
        self.demarrer_moteur()

    def creer_icone(self):
        self.img_on, self.img_off = icone(True), icone(False)
        menu = pystray.Menu(
            pystray.MenuItem("Ouvrir Crochetage", lambda: self.q.put("ouvrir"), default=True),
            pystray.MenuItem(lambda it: "Mettre en pause  (F6)" if self.moteur.actif else "Activer  (F6)",
                             lambda: self.q.put("basculer")),
            pystray.MenuItem("Détecter le cercle  (F7)", lambda: self.q.put("detecter")),
            pystray.MenuItem("Lancer la simulation", lambda: self.q.put("simulation")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quitter", lambda: self.q.put("quitter")),
        )
        self.tray = pystray.Icon("Crochetage", self.img_off, "Crochetage - en pause", menu)
        self.etat_icone = False
        threading.Thread(target=self.tray.run, daemon=True).start()

    def maj_icone(self):
        actif = self.moteur.actif
        if actif != self.etat_icone:
            self.etat_icone = actif
            try:
                self.tray.icon = self.img_on if actif else self.img_off
                self.tray.title = "Crochetage - ACTIF" if actif else "Crochetage - en pause"
                self.tray.update_menu()
            except Exception:
                pass

    def placer(self):
        r = wt.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)
        w, h = self.px(W), self.px(H)
        self.root.geometry(f"{w}x{h}+{r.right - w - self.px(12)}+{r.bottom - h - self.px(12)}")

    def montrer(self):
        self.placer()
        self.root.deiconify()
        self.root.lift()
        self.visible, self.t_montre = True, time.time()
        self.moteur.apercu = True
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            pref = ctypes.c_int(2)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        self.root.focus_force()
        self.afficher(self.vue or "accueil")

    def cacher(self):
        self.root.withdraw()
        self.visible = False
        self.moteur.apercu = False

    def _verifier_focus(self):
        try:
            dehors = self.root.focus_get() is None
        except Exception:
            dehors = False
        if self.visible and dehors and time.time() - self.t_montre > 0.8:
            self.cacher()

    def quitter(self):
        self.moteur.arreter()
        try:
            self.tray.stop()
        except Exception:
            pass
        if self.sim and self.sim.poll() is None:
            self.sim.terminate()
        self.root.destroy()

    def construire(self):
        px = self.px
        self.entete = tk.Canvas(self.root, width=px(W), height=px(60), bg=ROUGE, highlightthickness=0)
        self.entete.pack(fill="x")
        self.logo = ImageTk.PhotoImage(badge(px(38)))
        self.it_logo = self.entete.create_image(px(16), px(30), image=self.logo, anchor="w")
        self.it_retour = self.entete.create_text(px(18), px(29), text="‹", fill="white", anchor="w",
                                                 font=self.police(30, True), state="hidden")
        self.it_titre = self.entete.create_text(px(60), px(30), text="CROCHETAGE", fill="white", anchor="w",
                                                font=self.police(19, True))
        self.it_reglages = self.entete.create_text(px(W - 50), px(30), text="⚙", fill="white",
                                                   font=self.police(21))
        self.it_cacher = self.entete.create_text(px(W - 20), px(29), text="—", fill="white",
                                                 font=self.police(15, True))
        for it, act in ((self.it_reglages, lambda: self.afficher("reglages")),
                        (self.it_cacher, self.cacher),
                        (self.it_retour, lambda: self.afficher("accueil"))):
            self.entete.tag_bind(it, "<ButtonRelease-1>", lambda e, a=act: a())
            self.entete.tag_bind(it, "<Enter>", lambda e: self.entete.configure(cursor="hand2"))
            self.entete.tag_bind(it, "<Leave>", lambda e: self.entete.configure(cursor=""))
        self.sous = tk.Canvas(self.root, width=px(W), height=px(26), bg=ROUGE_FONCE, highlightthickness=0)
        self.sous.pack(fill="x")
        self.it_sous_g = self.sous.create_text(px(16), px(13), anchor="w", fill="#FFE3E8", font=self.police(11, True))
        self.it_sous_d = self.sous.create_text(px(W - 16), px(13), anchor="e", fill="#FFE3E8",
                                               font=self.police(11, True), text="F6 · F7 · F8")
        self.corps = tk.Frame(self.root, bg=FOND)
        self.corps.pack(fill="both", expand=True)
        self.vues = {"accueil": self.construire_accueil(), "reglages": self.construire_reglages(),
                     "lettres": tk.Frame(self.corps, bg=FOND)}

    def construire_accueil(self):
        px = self.px
        v = tk.Frame(self.corps, bg=FOND)
        hA = px(250)
        self.cv = tk.Canvas(v, width=px(W), height=hA, bg=NOIR, highlightthickness=0)
        self.cv.pack(fill="x")
        cx, cy = px(W) / 2, hA / 2
        for k in range(1, 7):
            r = px(22) * k
            self.cv.create_oval(cx - r, cy - r, cx + r, cy + r, outline="#15151A")
        self.cv.create_line(0, cy, px(W), cy, fill="#121216")
        self.cv.create_line(cx, 0, cx, hA, fill="#121216")
        self.it_img = self.cv.create_image(cx, cy)
        self.it_band = [self.cv.create_oval(0, 0, 0, 0, outline=ROUGE_FONCE, dash=(2, 3)) for _ in range(2)]
        self.it_barre = self.cv.create_arc(0, 0, 0, 0, style="arc", outline="white", width=px(3), state="hidden")
        self.it_aig = self.cv.create_line(0, 0, 0, 0, fill=ROUGE_CLAIR, width=px(3), state="hidden")
        self.it_vide = self.cv.create_text(cx, cy, fill=TEXTE2, font=self.police(13), justify="center",
                                           text="Pas encore de cercle.\n\nLance un skill-check en jeu\n"
                                                "puis appuie sur F7.")
        self.it_lettre = self.cv.create_text(px(12), hA - px(8), anchor="sw", fill=TEXTE2, font=self.police(11, True))
        self.it_fps = self.cv.create_text(px(W - 12), hA - px(8), anchor="se", fill=TEXTE3, font=self.police(10))

        lc, hc = px(W - 24), px(256)
        carte = tk.Canvas(v, width=lc, height=hc, bg=FOND, highlightthickness=0)
        carte.pack(pady=(px(10), 0))
        rrect(carte, 0, 0, lc - 1, hc - 1, px(14), fill=CARTE, outline="")
        self.it_statut = carte.create_text(px(18), px(30), anchor="w", font=self.police(24, True), fill=TEXTE)
        self.it_point = carte.create_oval(0, 0, 0, 0, fill=ROUGE, outline="", state="hidden")
        self.it_info = carte.create_text(px(18), px(62), anchor="w", font=self.police(13, True), fill=TEXTE)
        self.it_stats = carte.create_text(px(18), px(86), anchor="w", font=self.police(12), fill=TEXTE2)
        self.it_journal = carte.create_text(px(18), px(108), anchor="w", font=self.police(11), fill=TEXTE3)
        self.it_lettres = carte.create_text(lc - px(18), px(30), anchor="e", font=self.police(12, True),
                                            fill=ROUGE_CLAIR, text="")
        carte.tag_bind(self.it_lettres, "<ButtonRelease-1>", lambda e: self.afficher("lettres"))
        self.b_detecter = Bouton(carte, "Détecter le cercle  (F7)", self.detecter, lc - px(36), px(40), self.f,
                                 style="secondaire", taille=13)
        carte.create_window(px(18), px(132), anchor="nw", window=self.b_detecter)
        self.b_actif = Bouton(carte, "Activer", self.basculer, lc - px(36), px(52), self.f, taille=16)
        carte.create_window(px(18), px(186), anchor="nw", window=self.b_actif)
        self.carte = carte
        return v

    def construire_reglages(self):
        px = self.px
        v = tk.Frame(self.corps, bg=FOND)
        lc = px(W - 24)

        def bloc(titre, aide, widget_fn, h=76):
            c = tk.Canvas(v, width=lc, height=px(h), bg=FOND, highlightthickness=0)
            c.pack(pady=(px(6), 0))
            rrect(c, 0, 0, lc - 1, px(h) - 1, px(12), fill=CARTE, outline="")
            c.create_text(px(16), px(17), anchor="w", text=titre, fill=TEXTE, font=self.police(13, True))
            if aide:
                c.create_text(px(16), px(35), anchor="w", text=aide, fill=TEXTE3, font=self.police(10))
            w = widget_fn(c)
            return c, w

        def curseur(cle, mini, maxi, fmt, conv=lambda x: x, inv=lambda x: x):
            def fn(c):
                cur = Curseur(c, mini, maxi, inv(self.cfg[cle]), fmt,
                              lambda val: self.reglage(cle, conv(val)), lc - px(32), self.f)
                c.create_window(px(16), px(44), anchor="nw", window=cur)
                return cur
            return fn

        bloc("Anticipation", "Trop tard dans la barre ? Monte. Trop tôt ? Baisse.",
             curseur("latence_ms", 0, 80, lambda x: f"{x} ms"))
        bloc("Où viser dans la barre", "0 % = dès l'entrée · 50 % = au milieu",
             curseur("cible", 0, 50, lambda x: f"{x} %", conv=lambda x: x / 100, inv=lambda x: round(x * 100)))
        bloc("Reconnaissance des lettres", "Lettre mal reconnue ? Monte. Pas reconnue ? Baisse.",
             curseur("seuil", 50, 95, lambda x: f"{x} %", conv=lambda x: x / 100, inv=lambda x: round(x * 100)))

        def fenetre(c):
            e = tk.Entry(c, bg=CARTE2, fg=TEXTE, insertbackground=TEXTE, relief="flat", font=self.police(13),
                         highlightthickness=1, highlightbackground=BORD, highlightcolor=ROUGE)
            e.insert(0, self.cfg.get("fenetre_cible") or "")
            c.create_window(px(16), px(46), anchor="nw", window=e, width=lc - px(32), height=px(24))
            e.bind("<Return>", lambda ev: self.reglage("fenetre_cible", e.get().strip() or None))
            e.bind("<FocusOut>", lambda ev: (e.get().strip() or None) != self.cfg.get("fenetre_cible")
                   and self.reglage("fenetre_cible", e.get().strip() or None))
            return e
        bloc("Fenêtre du jeu", "N'appuie que si cette fenêtre est devant (vide = partout)", fenetre)

        def ligne_inter(titre, valeur, cmd):
            c = tk.Canvas(v, width=lc, height=px(42), bg=FOND, highlightthickness=0)
            c.pack(pady=(px(6), 0))
            rrect(c, 0, 0, lc - 1, px(42) - 1, px(12), fill=CARTE, outline="")
            c.create_text(px(16), px(21), anchor="w", text=titre, fill=TEXTE, font=self.police(13, True))
            sw = Interrupteur(c, valeur, cmd, self.f)
            c.create_window(lc - px(16), px(21), anchor="e", window=sw)

        ligne_inter("Apprendre les lettres tout seul", self.cfg.get("apprentissage_auto", True),
                    lambda val: self.reglage("apprentissage_auto", val))
        try:
            dw = demarrage_windows()
        except Exception:
            dw = False
        ligne_inter("Démarrer avec Windows", dw, lambda val: self._demarrage(val))

        bas = tk.Frame(v, bg=FOND)
        bas.pack(pady=(px(8), 0))
        lb = (lc - px(8)) // 2
        Bouton(bas, "Dossier des lettres", lambda: os.startfile(at.TEMPLATES_DIR), lb, px(36), self.f,
               style="secondaire", bg=FOND, taille=12).grid(row=0, column=0, padx=(0, px(8)))
        Bouton(bas, "Simulation", self.simulation, lb, px(36), self.f, style="secondaire", bg=FOND,
               taille=12).grid(row=0, column=1)
        Bouton(bas, "Quitter Crochetage", self.quitter, lc, px(36), self.f, style="danger", bg=FOND,
               taille=12).grid(row=1, column=0, columnspan=2, pady=(px(6), 0))
        return v

    def construire_lettres(self):
        px = self.px
        v = self.vues["lettres"]
        for w in v.winfo_children():
            w.destroy()
        lc = px(W - 24)
        fichiers = at.lettres_a_nommer()[:5]
        tk.Label(v, text="Lettres vues mais pas encore connues", bg=FOND, fg=TEXTE, font=self.police(14, True)
                 ).pack(anchor="w", padx=px(16), pady=(px(12), 0))
        tk.Label(v, text="Tape la lettre affichée puis Entrée (vide = jeter).", bg=FOND, fg=TEXTE3,
                 font=self.police(11)).pack(anchor="w", padx=px(16), pady=(px(2), px(4)))
        self._photos_lettres = []
        if not fichiers:
            tk.Label(v, text="Rien à nommer.", bg=FOND, fg=TEXTE2, font=self.police(13)).pack(pady=px(30))
        for chemin in fichiers:
            c = tk.Canvas(v, width=lc, height=px(70), bg=FOND, highlightthickness=0)
            c.pack(pady=(px(6), 0))
            rrect(c, 0, 0, lc - 1, px(69), px(12), fill=CARTE, outline="")
            img = at.lire_image(chemin)
            if img is not None:
                ph = ImageTk.PhotoImage(Image.fromarray(img).resize((px(54), px(54)), Image.NEAREST))
                self._photos_lettres.append(ph)
                c.create_image(px(14), px(35), image=ph, anchor="w")
            e = tk.Entry(c, bg=CARTE2, fg=TEXTE, insertbackground=TEXTE, relief="flat", justify="center",
                         font=self.police(20, True), highlightthickness=1, highlightbackground=BORD,
                         highlightcolor=ROUGE)
            c.create_window(px(84), px(35), anchor="w", window=e, width=px(60), height=px(40))

            def valider(ev=None, chemin=chemin, e=e):
                try:
                    at.nommer_inconnue(chemin, e.get())
                    self.moteur.demande_rechargement = True
                except ValueError as err:
                    self.noter(f"[appli] {err}")
                self.construire_lettres()
            e.bind("<Return>", valider)
            Bouton(c, "OK", valider, px(70), px(36), self.f, taille=13).place(x=lc - px(88), y=px(17))
        Bouton(v, "Retour", lambda: self.afficher("accueil"), lc, px(40), self.f, style="secondaire", bg=FOND,
               taille=13).pack(pady=(px(14), 0))

    def afficher(self, nom):
        if nom == "lettres":
            self.construire_lettres()
        for k, fr in self.vues.items():
            fr.pack_forget()
        self.vues[nom].pack(fill="both", expand=True)
        self.vue = nom
        accueil = nom == "accueil"
        self.entete.itemconfigure(self.it_logo, state="normal" if accueil else "hidden")
        self.entete.itemconfigure(self.it_retour, state="hidden" if accueil else "normal")
        self.entete.itemconfigure(self.it_reglages, state="normal" if accueil else "hidden")
        self.entete.itemconfigure(self.it_titre, text={"accueil": "CROCHETAGE", "reglages": "RÉGLAGES",
                                                        "lettres": "LETTRES"}[nom])
        self.entete.coords(self.it_titre, self.px(60 if accueil else 44), self.px(30))

    def detecter(self):
        self.moteur.demande_detection = True

    def simulation(self):
        if self.sim and self.sim.poll() is None:
            return
        self.moteur.raccourcis = False
        self.sim = subprocess.Popen([pythonw(), os.path.join(BASE, "simulation.py")], cwd=BASE)
        self.cacher()

    def _demarrage(self, val):
        try:
            demarrage_windows(val)
        except Exception as e:
            self.noter(f"[appli] démarrage avec Windows impossible : {e}")

    def boucle(self):
        try:
            while not self.q.empty():
                a = self.q.get()
                if a == "ouvrir":
                    self.cacher() if self.visible else self.montrer()
                elif a == "basculer":
                    self.basculer()
                elif a == "detecter":
                    self.detecter()
                elif a == "simulation":
                    self.simulation()
                elif a == "quitter":
                    return self.quitter()
            if self.sim and self.sim.poll() is not None:
                self.sim = None
                self.moteur.raccourcis = True
            self.maj_icone()
            if self.visible and self.vue == "accueil":
                self.maj_accueil()
        except Exception:
            self.noter("[appli] " + traceback.format_exc().strip().splitlines()[-1])
        self.root.after(66, self.boucle)

    def maj_accueil(self):
        px, m, cfg = self.px, self.moteur, self.cfg
        z = cfg.get("zone")
        nb_inconnues = len(at.lettres_a_nommer()) if int(time.time() * 2) % 4 == 0 or not hasattr(self, "_nbi") \
            else self._nbi
        self._nbi = nb_inconnues
        self.sous.itemconfigure(self.it_sous_g, text=(f"Zone : {z['width']} px   ·   Lettres : {m.nb_modeles}"
                                                      if z else "Zone : pas encore trouvée"))
        actif = m.actif
        self.carte.itemconfigure(self.it_statut, text="ACTIF" if actif else "EN PAUSE",
                                 fill=ROUGE_CLAIR if actif else TEXTE)
        if actif:
            bb = self.carte.bbox(self.it_statut)
            r = px(5)
            cy = (bb[1] + bb[3]) / 2
            self.carte.coords(self.it_point, bb[2] + px(10), cy - r, bb[2] + px(10) + 2 * r, cy + r)
            vis = int(time.time() * 2) % 2 == 0
            self.carte.itemconfigure(self.it_point, state="normal" if vis else "hidden")
        else:
            self.carte.itemconfigure(self.it_point, state="hidden")
        if not z:
            info = "Lance un skill-check en jeu puis F7"
        elif m.cercle and m.nom and m.score >= cfg["seuil"]:
            info = f"Skill-check en cours  ·  lettre {m.nom}"
        elif m.cercle:
            info = "Skill-check en cours  ·  lettre inconnue"
        else:
            info = "En attente d'un skill-check"
        self.carte.itemconfigure(self.it_info, text=info)
        self.carte.itemconfigure(self.it_stats, text=f"Crochetés {m.nb_crochetages}   ·   Checks {m.nb_reussis}/{m.nb_appuis}")
        dernier = (m.dernier or (self.journal[-1] if self.journal else "")).split("] ", 1)[-1]
        self.carte.itemconfigure(self.it_journal, text=dernier if len(dernier) <= 46 else dernier[:45] + "…")
        self.carte.itemconfigure(self.it_lettres, text=f"{nb_inconnues} à nommer ›" if nb_inconnues else "")
        self.b_actif.changer("Mettre en pause  (F6)" if actif else "Activer  (F6)",
                             "contour" if actif else "primaire")
        self.maj_apercu()

    def maj_apercu(self):
        px, m, cv = self.px, self.moteur, self.cv
        img, an = m.image, m.an
        hA = int(cv["height"])
        cx, cy = px(W) / 2, hA / 2
        if an is None or img is None:
            cv.itemconfigure(self.it_vide, state="normal")
            for it in (self.it_img, self.it_barre, self.it_aig, *self.it_band):
                cv.itemconfigure(it, state="hidden")
            cv.itemconfigure(self.it_lettre, text="")
            cv.itemconfigure(self.it_fps, text="")
            return
        cv.itemconfigure(self.it_vide, state="hidden")
        Z = img.shape[0]
        P = px(196)
        k = P / Z
        pil = Image.fromarray(np.ascontiguousarray(img[:, :, 2::-1])).resize((P, P), Image.BILINEAR)
        self.photo = ImageTk.PhotoImage(pil)
        cv.itemconfigure(self.it_img, image=self.photo, state="normal")
        demi = Z / 2 * k
        r_in, r_out = an.anneau_r
        for it, rr in zip(self.it_band, (r_in, r_out)):
            r = rr * demi
            cv.coords(it, cx - r, cy - r, cx + r, cy + r)
            cv.itemconfigure(it, state="normal")
        if m.barre:
            centre, debut, fin, rel = m.barre
            r = (r_in + r_out) / 2 * demi
            cv.coords(self.it_barre, cx - r, cy - r, cx + r, cy + r)
            cv.itemconfigure(self.it_barre, start=-(centre + fin), extent=fin - debut, state="normal")
            a = math.radians(centre + rel)
            cv.coords(self.it_aig, cx + r_in * demi * 0.85 * math.cos(a), cy + r_in * demi * 0.85 * math.sin(a),
                      cx + r_out * demi * 1.08 * math.cos(a), cy + r_out * demi * 1.08 * math.sin(a))
            cv.itemconfigure(self.it_aig, state="normal")
        else:
            cv.itemconfigure(self.it_barre, state="hidden")
            cv.itemconfigure(self.it_aig, state="hidden")
        if m.nom and m.score >= self.cfg["seuil"]:
            cv.itemconfigure(self.it_lettre, text=f"Lettre {m.nom}  ·  {m.score * 100:.0f} %", fill=TEXTE)
        else:
            cv.itemconfigure(self.it_lettre, text="Lettre : -", fill=TEXTE3)
        cv.itemconfigure(self.it_fps, text=f"{m.fps:.0f} analyses/s" if m.fps else "")


def main():
    app = App()
    app.root.mainloop()


if __name__ == "__main__":
    main()
