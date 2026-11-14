import base64
import ctypes
import ctypes.wintypes as wt
import io
import math
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from collections import deque

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)
sys.path.insert(0, BASE)

DEMANDE_OUVERTURE = os.path.join(tempfile.gettempdir(), "crochetage_ouvrir")
_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\Crochetage_appli")
if ctypes.windll.kernel32.GetLastError() == 183:
    try:
        with open(DEMANDE_OUVERTURE, "w") as _f:
            _f.write(str(time.time()))
    except OSError:
        pass
    sys.exit(0)

import cv2
import numpy as np
import pystray
import webview
from PIL import Image, ImageDraw

import autotouche as at
import overlay as ov

W, H = 320, 470
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


def url_png(im):
    tampon = io.BytesIO()
    im.save(tampon, format="PNG")
    return "data:image/png;base64," + base64.b64encode(tampon.getvalue()).decode()


def url_jpeg(bgr, qualite=82):
    ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, qualite])
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode() if ok else None


def zone_travail():
    r = wt.RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)
    return r.left, r.top, r.right, r.bottom


def echelle():
    try:
        return ctypes.windll.user32.GetDpiForSystem() / 96.0
    except Exception:
        return 1.0


def fenetres_du_processus(titre):
    pid = os.getpid()
    trouvees = []
    proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        p = wt.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid:
            n = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, n + 1)
            if buf.value == titre:
                trouvees.append(hwnd)
        return True

    ctypes.windll.user32.EnumWindows(proc(cb), 0)
    return trouvees


class Api:
    def __init__(self, app):
        self._app = app

    def infos(self):
        return {"logo": url_png(icone(True, 96))}

    def etat(self):
        return self._app.etat()

    def apercu(self):
        return self._app.apercu()

    def basculer(self):
        self._app.q.put("basculer")

    def detecter(self):
        self._app.q.put("detecter")

    def reglages(self):
        return self._app.reglages()

    def reglage(self, cle, valeur):
        self._app.q.put(("reglage", cle, valeur))

    def demarrage(self, valeur):
        self._app.q.put(("demarrage", bool(valeur)))

    def lettres(self):
        return self._app.lettres()

    def nommer(self, chemin, lettre):
        return self._app.nommer(chemin, lettre)

    def simulation(self):
        self._app.q.put("simulation")

    def dossier_lettres(self):
        os.startfile(at.TEMPLATES_DIR)

    def quitter(self):
        self._app.q.put("quitter")

    def cacher(self):
        self._app.q.put("cacher")

    def perte_focus(self):
        self._app.q.put("perte_focus")

    def focus(self):
        self._app.t_focus = time.time()


class App:
    REDEMARRE = {"latence_ms", "cible", "seuil", "fenetre_cible", "apprentissage_auto"}

    def __init__(self):
        self.q = queue.Queue()
        self.journal = deque(maxlen=200)
        self.visible, self.t_montre, self.t_focus = True, time.time(), time.time()
        self.sim = None
        self.stop = False
        self._a_nommer, self._t_a_nommer = 0, 0.0
        self._ouvrir_log()

        at.set_profil(None)
        neuf = not os.path.exists(at.CONFIG_PATH)
        self.cfg = at.charger_config()
        if neuf:
            self.cfg["fenetre_cible"] = "FiveM"
            at.sauver_config(self.cfg)

        self.moteur = None
        self.demarrer_moteur(actif=False)
        self.api = Api(self)
        x, y = self.position()
        with open(os.path.join(BASE, "interface", "index.html"), encoding="utf-8") as f:
            html = f.read()
        self.fenetre = webview.create_window(
            "Crochetage", html=html, js_api=self.api, width=W, height=H, x=x, y=y, frameless=True,
            on_top=True, resizable=False, background_color="#0B0B0E", easy_drag=False, shadow=True)
        self.animateur = ov.Animateur([
            lambda: ov.OverlayAnneau(None, lambda: self.moteur, self._overlay_visible),
            lambda: ov.LogoAnime(lambda: self.moteur, self._logo_visible, os.path.join(BASE, "logo.png"),
                                 position=self.cfg.get("logo_position", "gauche")),
        ])

    def _overlay_visible(self, m):
        return bool(self.cfg.get("overlay", True) and m.actif and m.jeu_devant)

    def _logo_visible(self, m):
        return bool(self.cfg.get("logo_anime", True) and m.actif and m.jeu_devant and m.serveur_ok)

    def position(self):
        g, h_, d, b = zone_travail()
        k = echelle()
        return int((d - 12 * k) / k - W), int((b - 12 * k) / k - H)

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
            fil = getattr(self, "fil_moteur", None)
            if fil is not None:
                fil.join(timeout=2.0)
        m = at.Moteur(self.cfg, log=self.noter, actif=bool(actif), touche_quitter=False)
        if ancien:
            m.nb_appuis, m.nb_reussis = ancien.nb_appuis, ancien.nb_reussis
            m.nb_crochetages, m.nb_rates = ancien.nb_crochetages, ancien.nb_rates
            m.raccourcis = ancien.raccourcis
            if ancien.suivi is not None:
                m.historique_initial = list(ancien.suivi.historique)
                m.stats_initiales = dict(ancien.suivi.stats)
        self.moteur = m

        def run():
            try:
                m.run()
            except Exception:
                self.noter("ERREUR du bot : " + traceback.format_exc().strip().splitlines()[-1])

        self.fil_moteur = threading.Thread(target=run, daemon=True)
        self.fil_moteur.start()

    def basculer(self):
        self.moteur.basculer()
        self.maj_icone()

    def etat_jeu(self):
        m = self.moteur
        if not m.fivem_lance:
            return "FiveM fermé"
        if not m.serveur_ok:
            hote = (m.serveur_url or "aucun").split("//")[-1].strip("/")
            return f"FiveM · autre serveur : {hote}"
        return "FiveM · Baylife ✓"

    def etat(self):
        m = self.moteur
        su = m.suivi
        now = time.perf_counter()
        if not m.fivem_lance:
            jeu = {"texte": "FiveM fermé", "niveau": ""}
        elif not m.serveur_ok:
            jeu = {"texte": "Autre serveur", "niveau": "attention"}
        else:
            jeu = {"texte": "Baylife", "niveau": "ok"}
        cycle, check = (su.cycle, su.check) if su else (None, None)
        dc = su.dernier_cycle if su else None
        if not m.actif:
            sous = "Clique ou appuie sur F6" if m.fivem_lance else "FiveM fermé · clique pour activer"
        elif not m.fivem_lance:
            sous = "En attente de FiveM"
        elif not m.serveur_ok:
            sous = "En veille hors de Baylife"
        elif cycle:
            n = check["n"] if check else len(cycle["checks"]) + 1
            lettre = m.nom if (m.nom and m.score >= self.cfg.get("seuil", 0.75)) else None
            sous = f"Crochetage · check {n}" + (f" · {lettre}" if lettre else "")
        elif dc and now - dc["t"] < 4 and dc["bot"]:
            sous = (f"Crocheté · {dc['nb_checks']}/{dc['nb_checks']}" if dc["resultat"] == "crocheté"
                    else f"Raté au check {dc['nb_checks']}" if dc["resultat"] == "raté" else "Prêt")
        else:
            sous = "Prêt, en attente d'un crochetage"
        histo = list(su.historique) if su else []
        durees = [c["duree"] for c in histo if c["bot"] and c["resultat"] == "crocheté"]
        maintenant = time.time()
        if maintenant - self._t_a_nommer > 2.0:
            self._t_a_nommer = maintenant
            try:
                self._a_nommer = len(at.lettres_a_nommer())
            except Exception:
                self._a_nommer = 0
        return {
            "actif": bool(m.actif), "jeu": jeu, "sous": sous,
            "stats": {"crochetes": m.nb_crochetages,
                      "taux": round(100 * m.nb_reussis / m.nb_appuis) if m.nb_appuis else None,
                      "duree": round(sum(durees) / len(durees), 1) if durees else None},
            "historique": [dict(c, age=maintenant - c["heure"]) for c in reversed(histo[-15:])],
            "a_nommer": self._a_nommer,
        }

    def apercu(self):
        m = self.moteur
        an, img = m.an, m.image
        if an is None or img is None or not (m.cercle and m.serveur_ok):
            return {"image": None, "legende": ""}
        bgr = np.ascontiguousarray(img[:, :, :3])
        out = {"image": url_jpeg(bgr), "r_ext": float(an.anneau_r[1]), "barre": None, "aiguille": None, "cible": None}
        barre, dec = m.barre, getattr(m, "dec", None)
        morceaux = []
        if m.nom and m.score >= self.cfg.get("seuil", 0.75):
            morceaux.append(f"Lettre {m.nom}")
        if barre:
            centre, debut, fin, rel = barre
            out["barre"] = [centre + debut, centre + fin]
            out["aiguille"] = centre + rel
            if dec is not None and dec.vit is not None:
                morceaux.append(f"{abs(dec.vit):.0f}°/s")
                lo, hi = dec.fenetre(debut, fin)
                if hi > lo and not dec.verrou:
                    out["cible"] = centre + (hi if dec.vit < 0 else lo)
        out["legende"] = "  ·  ".join(morceaux)
        return out

    def reglages(self):
        r = {k: self.cfg.get(k) for k in ("cible", "latence_ms", "seuil", "sons", "overlay", "logo_anime",
                                           "apprentissage_auto", "fenetre_cible")}
        try:
            r["demarrage"] = bool(demarrage_windows())
        except Exception:
            r["demarrage"] = False
        return r

    def lettres(self):
        inconnues = []
        for chemin in at.lettres_a_nommer()[:12]:
            img = at.lire_image(chemin)
            if img is not None:
                inconnues.append({"chemin": chemin, "image": url_png(Image.fromarray(img))})
        connues = sorted({at.nom_touche_depuis_fichier(f) for f in os.listdir(at.TEMPLATES_DIR)
                          if f.lower().endswith(".png")} | {at.nom_touche_depuis_fichier(f) for f in
                                                             os.listdir(at.LETTRES_BASE) if f.lower().endswith(".png")})
        return {"inconnues": inconnues, "connues": connues}

    def nommer(self, chemin, lettre):
        try:
            at.nommer_inconnue(chemin, lettre)
            self.moteur.demande_rechargement = True
            return True
        except ValueError as err:
            self.noter(f"[appli] {err}")
            return False

    def creer_icone(self):
        self.img_on, self.img_off = icone(True), icone(False)
        menu = pystray.Menu(
            pystray.MenuItem("Ouvrir Crochetage", lambda: self.q.put("ouvrir"), default=True),
            pystray.MenuItem(lambda it: "Mettre en pause  ·  F6" if self.moteur.actif else "Activer  ·  F6",
                             lambda: self.q.put("basculer")),
            pystray.MenuItem("Détecter le cercle  ·  F7", lambda: self.q.put("detecter")),
            pystray.MenuItem("Lancer la simulation", lambda: self.q.put("simulation")),
            pystray.MenuItem("Bruitages", lambda: self.q.put("sons"), checked=lambda it: bool(self.cfg.get("sons", True))),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quitter", lambda: self.q.put("quitter")),
        )
        self.tray = pystray.Icon("Crochetage", self.img_off, "Crochetage · en pause", menu)
        self.etat_icone, self.titre_icone = False, None
        threading.Thread(target=self.tray.run, daemon=True).start()

    def maj_icone(self):
        actif = self.moteur.actif
        titre = f"Crochetage · {'ACTIF' if actif else 'en pause'} · {self.etat_jeu()}"
        if actif != self.etat_icone or titre != self.titre_icone:
            self.etat_icone, self.titre_icone = actif, titre
            try:
                self.tray.icon = self.img_on if actif else self.img_off
                self.tray.title = titre
                self.tray.update_menu()
            except Exception:
                pass

    def _hwnd(self):
        h = fenetres_du_processus("Crochetage")
        return h[0] if h else None

    def _style_fenetre(self):
        hwnd = self._hwnd()
        if not hwnd:
            return
        try:
            pref = ctypes.c_int(2)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(wt.HWND(hwnd), 33, ctypes.byref(pref), 4)
        except Exception:
            pass

    def montrer(self):
        x, y = self.position()
        try:
            self.fenetre.move(x, y)
            self.fenetre.show()
        except Exception:
            pass
        self.visible, self.t_montre = True, time.time()
        self.moteur.apercu = True
        hwnd = self._hwnd()
        if hwnd:
            ctypes.windll.user32.SetForegroundWindow(wt.HWND(hwnd))

    def cacher(self):
        try:
            self.fenetre.hide()
        except Exception:
            pass
        self.visible = False
        self.moteur.apercu = False

    def simulation(self):
        if self.sim and self.sim.poll() is None:
            return
        self.moteur.raccourcis = False
        self.sim = subprocess.Popen([pythonw(), os.path.join(BASE, "simulation.py")], cwd=BASE)
        self.cacher()

    def quitter(self):
        self.stop = True
        self.moteur.arreter()
        self.animateur.arreter()
        try:
            self.tray.stop()
        except Exception:
            pass
        if self.sim and self.sim.poll() is None:
            self.sim.terminate()
        try:
            self.fenetre.destroy()
        except Exception:
            pass

    def traiter(self, a):
        if a == "ouvrir":
            self.cacher() if self.visible else self.montrer()
        elif a == "cacher":
            self.cacher()
        elif a == "perte_focus":
            self.t_focus = 0.0
            threading.Timer(0.3, self._verifier_focus).start()
        elif a == "basculer":
            self.basculer()
        elif a == "detecter":
            self.moteur.demande_detection = True
        elif a == "simulation":
            self.simulation()
        elif a == "sons":
            self.cfg["sons"] = not self.cfg.get("sons", True)
            at.sauver_config(self.cfg)
            try:
                self.tray.update_menu()
            except Exception:
                pass
        elif a == "quitter":
            self.quitter()
        elif isinstance(a, tuple) and a[0] == "reglage":
            _, cle, valeur = a
            if self.cfg.get(cle) != valeur:
                self.cfg[cle] = valeur
                at.sauver_config(self.cfg)
                if cle in self.REDEMARRE:
                    self.demarrer_moteur()
        elif isinstance(a, tuple) and a[0] == "demarrage":
            try:
                demarrage_windows(a[1])
            except Exception as e:
                self.noter(f"[appli] démarrage avec Windows impossible : {e}")

    def _verifier_focus(self):
        if self.visible and self.t_focus == 0.0 and time.time() - self.t_montre > 0.8:
            self.q.put("cacher")

    def boucle(self):
        self.animateur.start()
        self.creer_icone()
        try:
            self.fenetre.resize(W, H)
            self.fenetre.move(*self.position())
        except Exception:
            pass
        self._style_fenetre()
        while not self.stop:
            try:
                if os.path.exists(DEMANDE_OUVERTURE):
                    try:
                        os.remove(DEMANDE_OUVERTURE)
                    except OSError:
                        pass
                    self.montrer()
                while not self.q.empty():
                    self.traiter(self.q.get())
                    if self.stop:
                        return
                if self.sim and self.sim.poll() is not None:
                    self.sim = None
                    self.moteur.raccourcis = True
                self.maj_icone()
            except Exception:
                self.noter("[appli] " + traceback.format_exc().strip().splitlines()[-1])
            time.sleep(0.05)


def main():
    app = App()
    webview.start(app.boucle, gui="edgechromium")


if __name__ == "__main__":
    main()
