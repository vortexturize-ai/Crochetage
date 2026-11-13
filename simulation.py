#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Simulation de skill-check pour tester I Like Baylife sans le jeu.

Copie du vrai : cercle au milieu de l'écran, lettre au centre, barre bleue de taille
variable, aiguille rouge qui tourne vite. 3 à 6 checks d'affilée, lettre différente à
chaque fois, la barre devient verte quand c'est réussi. Le décor bouge derrière.

    python simulation.py                    plein écran + bot intégré (déjà actif)
    python simulation.py --fenetre          dans une fenêtre
    python simulation.py --sans-bot         juste le mini-jeu (pour jouer toi-même)
    python simulation.py --difficulte dur   facile | moyen | dur | mix
    python simulation.py --vitesse 1.5      aiguille 1.5x plus rapide

Touches : F6 = bot ON/OFF (tu peux jouer toi-même quand il est OFF), Échap = quitter.
Le bot utilise un profil à part (config_sim.json + templates_sim/) : les réglages
du vrai jeu (config.json + templates/) ne sont pas touchés.
"""
import argparse
import ctypes
import os
import queue
import random
import sys
import threading
import time
import tkinter as tk

try:  # vrais pixels (sinon Windows agrandit la fenêtre et les coordonnées sont fausses)
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass
try:  # minuteries à 1 ms -> animation fluide
    ctypes.windll.winmm.timeBeginPeriod(1)
except Exception:
    pass

import numpy as np

import autotouche as at

TITRE = "Simulation skill-check"
DIFFICULTES = {  # largeur de barre min/max (°), vitesse de l'aiguille (°/s)
    "facile": (45, 65, 300),
    "moyen": (30, 45, 420),
    "dur": (18, 30, 540),
}
# couleurs relevées sur la vraie capture du jeu
BLEU, VERT, ROUGE, ANNEAU = "#5aa6d4", "#45d16b", "#c93a45", "#06080c"
VERT_TXT, ROUGE_TXT = "#3ddc84", "#ff5a5a"

APP = None  # instance courante (utilisée par les tests automatiques)


class Simulation:
    def __init__(self, root, args, W, H):
        self.root, self.args = root, args
        self.injection = queue.Queue()  # appuis injectés par les tests automatiques
        self.moteur = None
        self.stats = {"ok": 0, "rate": 0, "seq_ok": 0, "seq": 0}
        self.historique = []
        self.etat, self.t_suivant, self.t_hud = "fin", 0.0, 0.0
        self.nb_ticks, self.dt_max, self.nb_lents = 0, 0.0, 0

        self.c = tk.Canvas(root, width=W, height=H, highlightthickness=0, bg="#1d2733")
        self.c.pack(fill="both", expand=True)
        self.W, self.H = W, H
        self.cx, self.cy = W // 2, H // 2
        k = self.k = args.taille / 164.0   # proportions du vrai cercle (164 px de diamètre en 1080p)
        self.R = 82 * k

        self.dessiner_decor()
        cx, cy = self.cx, self.cy
        r = 73 * k  # milieu de l'anneau (64 -> 82 px)
        self.anneau = self.c.create_oval(cx - r, cy - r, cx + r, cy + r, outline=ANNEAU, width=18 * k)
        r = 72.5 * k
        self.barre = self.c.create_arc(cx - r, cy - r, cx + r, cy + r, start=0, extent=40,
                                       style="arc", outline=BLEU, width=15 * k)
        self.aiguille = self.c.create_line(0, 0, 1, 1, fill=ROUGE, width=max(3, round(5 * k)))
        b = 23 * k
        self.boite = self.c.create_rectangle(cx - b, cy - b, cx + b, cy + b, fill="#0e1015", outline="#151821")
        self.lettre = self.c.create_text(cx, cy, text="", fill="#ececec", font=("Segoe UI", -int(22 * k)))
        self.cercle = [self.anneau, self.barre, self.aiguille, self.boite, self.lettre]

        self.hud = self.c.create_text(20, 16, anchor="nw", fill="#f0f0f0", font=("Consolas", -15), text="")
        self.msg = self.c.create_text(cx, cy - self.R - 45, fill="white", font=("Arial", -26, "bold"), text="")
        self.montrer(False)

        root.bind("<KeyPress>", self.clavier)
        root.protocol("WM_DELETE_WINDOW", self.quitter)

    # ---------------------------------------------------------------- décor
    def dessiner_decor(self):
        W, H = self.W, self.H
        self.c.create_rectangle(0, 0, W, H * 0.55, fill="#7ea3c4", width=0)   # ciel
        self.c.create_rectangle(0, H * 0.55, W, H, fill="#c9a04a", width=0)   # sol
        self.mobiles = []
        if self.args.decor_fixe:
            return
        # voitures / bâtiments qui passent derrière le cercle (dont du bleu et du rouge, pour piéger le bot)
        couleurs = ["#3a3f47", "#5c6b3a", "#d8d8d8", "#b8322c", "#2f6fb0", "#8a5a2b", "#e0e0e0", "#c03030"]
        for coul in couleurs:
            w, h = random.randint(80, 240), random.randint(40, 170)
            x = random.uniform(0, W)
            y = self.cy + random.uniform(-self.R * 1.2, self.R * 0.6)
            item = self.c.create_rectangle(x, y, x + w, y + h, fill=coul, width=0)
            self.mobiles.append((item, random.uniform(40, 170) * random.choice([-1, 1]), w))

    def bouger_decor(self, dt):
        for item, v, w in self.mobiles:
            self.c.move(item, v * dt, 0)
            x1 = self.c.coords(item)[0]
            if v > 0 and x1 > self.W:
                self.c.move(item, -(self.W + w + 10), 0)
            elif v < 0 and x1 + w < 0:
                self.c.move(item, self.W + w + 10, 0)

    # ---------------------------------------------------------------- cercle
    def montrer(self, oui):
        for it in self.cercle:
            self.c.itemconfigure(it, state="normal" if oui else "hidden")

    def placer_aiguille(self, p):
        a = np.radians(-90.0 + p)  # 0 = en haut, sens des aiguilles d'une montre
        r1, r2 = 52 * self.k, 80 * self.k
        ca, sa = np.cos(a), np.sin(a)
        self.c.coords(self.aiguille, self.cx + r1 * ca, self.cy + r1 * sa, self.cx + r2 * ca, self.cy + r2 * sa)

    def placer_barre(self, debut, fin, couleur=BLEU):
        self.c.itemconfigure(self.barre, start=90 - fin, extent=fin - debut, outline=couleur)

    def message(self, texte, couleur="white"):
        self.c.itemconfigure(self.msg, text=texte, fill=couleur)

    # ---------------------------------------------------------------- partie
    def nouveau_check(self):
        diff = random.choice(list(DIFFICULTES)) if self.args.difficulte == "mix" else self.args.difficulte
        lmin, lmax, vit = DIFFICULTES[diff]
        larg = random.uniform(lmin, lmax)                 # la barre change de taille à chaque check
        debut = random.uniform(100, 340 - larg)           # jamais sur le point de départ de l'aiguille
        return {"diff": diff, "lettre": random.choice(self.args.lettres), "debut": debut,
                "fin": debut + larg, "vit": vit * self.args.vitesse}

    def nouvelle_sequence(self):
        nb = self.args.nb or random.randint(3, 6)
        self.seq = [self.nouveau_check() for _ in range(nb)]
        self.i = 0
        self.message("")
        self.lancer_check()

    def lancer_check(self):
        ck = self.ck = self.seq[self.i]
        self.placer_barre(ck["debut"], ck["fin"])
        self.c.itemconfigure(self.lettre, text=ck["lettre"])
        self.placer_aiguille(0)
        self.montrer(True)
        self.t0 = time.perf_counter()
        self.etat = "check"

    def appui(self, touche, quand=None):
        if self.etat != "check":
            return
        maintenant = quand or time.perf_counter()
        ck = self.ck
        p = (maintenant - self.t0) * ck["vit"]
        if touche != ck["lettre"]:
            return self.echec(f"mauvaise touche {touche} (c'était {ck['lettre']})")
        if p < ck["debut"]:
            return self.echec(f"{touche} trop tôt de {ck['debut'] - p:.0f}°")
        if p > ck["fin"]:
            return self.echec(f"{touche} trop tard de {p - ck['fin']:.0f}°")
        pos = (p - ck["debut"]) / (ck["fin"] - ck["debut"]) * 100
        self.stats["ok"] += 1
        self.noter(f"OK    {touche}  {ck['diff']:<6} barre {ck['fin'] - ck['debut']:2.0f}°  à {pos:3.0f}% de la barre")
        self.i += 1
        # comme en jeu : la barre devient verte un instant
        self.placer_barre(ck["debut"], ck["fin"], VERT)
        self.etat, self.t_suivant = "vert", time.perf_counter() + 0.18
        if self.i >= len(self.seq):
            self.stats["seq_ok"] += 1
            self.stats["seq"] += 1
            self.message(f"RÉUSSI  {len(self.seq)}/{len(self.seq)}", VERT_TXT)
        else:
            self.message(f"{self.i}/{len(self.seq)}", VERT_TXT)

    def echec(self, raison):
        self.stats["rate"] += 1
        self.stats["seq"] += 1
        self.noter(f"RATÉ  {raison}")
        self.message(f"RATÉ : {raison}", ROUGE_TXT)
        self.terminer()

    def terminer(self):
        self.montrer(False)
        self.etat, self.t_suivant = "fin", time.perf_counter() + self.args.pause_fin

    def noter(self, ligne):
        print(f"[sim] {ligne}")
        self.historique = ([ligne] + self.historique)[:8]

    def maj_hud(self):
        s = self.stats
        taux = f"{100 * s['seq_ok'] / s['seq']:.0f}%" if s["seq"] else "-"
        m = self.moteur
        bot = "désactivé (--sans-bot)" if self.args.sans_bot else (
            "démarrage..." if m is None else ("ACTIF" if m.actif else "en pause"))
        lignes = [
            f"{TITRE}   difficulté {self.args.difficulte}   vitesse x{self.args.vitesse}",
            f"Bot : {bot}   [F6] bot on/off   [Échap] quitter",
            f"Checks réussis {s['ok']}   ratés {s['rate']}   séquences {s['seq_ok']}/{s['seq']} ({taux})",
            "",
        ] + self.historique
        self.c.itemconfigure(self.hud, text="\n".join(lignes))

    # ---------------------------------------------------------------- boucle
    def tick(self):
        maintenant = time.perf_counter()
        dt = maintenant - self.t_tick
        self.bouger_decor(dt)
        self.t_tick = maintenant
        self.nb_ticks += 1
        self.dt_max = max(self.dt_max, dt)
        self.nb_lents += dt > 0.025
        while not self.injection.empty():
            touche, quand = self.injection.get()
            self.appui(touche, quand)
        if self.etat == "check":
            p = (maintenant - self.t0) * self.ck["vit"]
            if p >= 360:
                self.echec("aucun appui (tour complet)")
            else:
                self.placer_aiguille(p)
        elif maintenant >= self.t_suivant:
            if self.etat == "vert":
                if self.i >= len(self.seq):
                    self.terminer()
                elif self.args.pause_entre <= 0:
                    self.lancer_check()
                else:
                    self.montrer(False)
                    self.etat, self.t_suivant = "entre", maintenant + self.args.pause_entre
            elif self.etat == "entre":
                self.lancer_check()
            elif self.etat == "fin":
                self.nouvelle_sequence()
        if maintenant - self.t_hud > 0.1:
            self.t_hud = maintenant
            self.maj_hud()
        if self.args.duree and maintenant - self.t_debut > self.args.duree:
            return self.quitter()
        self.root.after(1, self.tick)

    def clavier(self, ev):
        if ev.keysym == "Escape":
            return self.quitter()
        k = ev.keysym.upper()
        if len(k) == 1 and k.isalnum():
            self.appui(k)
        elif k == "SPACE":
            self.appui("SPACE")

    def quitter(self):
        s = self.stats
        print(f"\n[sim] Bilan : {s['ok']} checks réussis, {s['rate']} ratés, "
              f"{s['seq_ok']}/{s['seq']} séquences complètes")
        if self.moteur:
            print(f"[sim] Le bot a compté {self.moteur.nb_reussis} réussite(s) (barre verte) "
                  f"sur {self.moteur.nb_appuis} appui(s)")
            self.moteur.arreter()
        if self.nb_ticks:
            duree = time.perf_counter() - self.t_debut
            print(f"[sim] Fluidité : {self.nb_ticks / duree:.0f} images/s, pire saccade {self.dt_max * 1000:.0f} ms, "
                  f"{self.nb_lents} saccade(s) > 25 ms")
        self.root.destroy()

    # ---------------------------------------------------------------- bot
    def preparer_bot(self):
        """Trouve le cercle avec la détection auto (comme F7 en jeu) et apprend les lettres."""
        self.message("Préparation : je cherche le cercle et j'apprends les lettres...")
        at.set_profil("sim")
        nouveau = not os.path.exists(at.CONFIG_PATH)
        cfg = at.charger_config()
        if nouveau:
            cfg["latence_ms"] = 8  # ici la touche arrive en ~1 ms (dans le vrai jeu c'est plus, d'où 25 par défaut)
        cfg["fenetre_cible"] = TITRE  # le bot n'appuie que si la simulation est au premier plan

        # 1) un skill-check qui tourne -> détection automatique (exactement comme F7 en jeu)
        self.c.itemconfigure(self.lettre, text="Z")
        self.placer_barre(200, 245)
        self.montrer(True)
        self.root.update()
        time.sleep(0.3)
        t0 = time.perf_counter()
        with at.MSS() as sct:
            mon = sct.monitors[1]
            zr = at.zone_recherche(mon)

            def prendre():
                self.placer_aiguille(((time.perf_counter() - t0) * 420) % 360)
                self.bouger_decor(0.004)
                self.root.update()
                return np.asarray(sct.grab(zr))

            det = None
            for _ in range(3):
                det = at.detecter_cercle_anime(prendre, cfg, 1.2)
                if det:
                    break
        if det:
            at.appliquer_detection(cfg, det, (zr["left"], zr["top"]))
            print(f"[sim] Cercle trouvé par la détection auto : zone {cfg['zone']} anneau {cfg['anneau']}")
        else:
            s = int(round(2 * self.R)) + 4
            cfg["zone"] = {"left": self.c.winfo_rootx() + self.cx - (s - 1) // 2,
                           "top": self.c.winfo_rooty() + self.cy - (s - 1) // 2, "width": s, "height": s}
            print("[sim] ATTENTION : détection auto ratée, zone calculée directement")
        at.sauver_config(cfg)

        # 2) les lettres : capturées à l'écran comme le ferait F8
        zone = cfg["zone"]
        an = at.Analyseur(cfg, zone["height"], zone["width"])
        attendu = at.rogner_modele(np.zeros(an.taille_lettre, np.uint8)).shape
        os.makedirs(at.TEMPLATES_DIR, exist_ok=True)
        a_faire = []
        for l in self.args.lettres:
            p = os.path.join(at.TEMPLATES_DIR, f"{l}.png")
            img = at.lire_image(p) if os.path.exists(p) else None
            if img is None or img.shape != attendu:
                a_faire.append(l)
        if a_faire:
            for it in (self.barre, self.aiguille):
                self.c.itemconfigure(it, state="hidden")
            with at.MSS() as sct:
                for l in a_faire:
                    self.c.itemconfigure(self.lettre, text=l)
                    self.root.update()
                    time.sleep(0.06)
                    self.root.update()
                    crop = an.lettre(np.asarray(sct.grab(zone)))
                    at.ecrire_image(os.path.join(at.TEMPLATES_DIR, f"{l}.png"), at.rogner_modele(crop))
        self.montrer(False)
        self.message("")
        print(f"[sim] {len(a_faire)} lettre(s) apprise(s) dans {at.dossier_modeles()}/")
        return cfg

    def demarrer_bot(self, cfg):
        self.moteur = at.Moteur(cfg, debug=self.args.debug, actif=not self.args.bot_pause, touche_quitter=False)
        threading.Thread(target=self.moteur.run, daemon=True).start()

    def premier_plan(self):
        """Met la simulation au premier plan (sinon les touches du bot iraient ailleurs - il les bloque alors)."""
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        self.root.focus_force()

    def demarrer(self):
        if not self.args.sans_bot:
            self.demarrer_bot(self.preparer_bot())
        self.premier_plan()
        self.t_tick = self.t_debut = time.perf_counter()
        self.t_suivant = self.t_debut + 1.0
        self.message("C'est parti !")
        self.tick()


def main(argv=None):
    global APP
    p = argparse.ArgumentParser(description="Simulation de skill-check pour tester I Like Baylife.")
    p.add_argument("--fenetre", action="store_true", help="dans une fenêtre au lieu du plein écran")
    p.add_argument("--sans-bot", action="store_true", help="ne lance pas le bot (pour jouer toi-même)")
    p.add_argument("--bot-pause", action="store_true", help="le bot démarre en pause (F6 pour l'activer)")
    p.add_argument("--debug", action="store_true", help="infos de détection du bot en direct dans la console")
    p.add_argument("--difficulte", default="mix", choices=list(DIFFICULTES) + ["mix"])
    p.add_argument("--vitesse", type=float, default=1.0, help="multiplie la vitesse de l'aiguille")
    p.add_argument("--taille", type=int, default=0, help="diamètre du cercle en pixels (défaut : comme le jeu)")
    p.add_argument("--lettres", default="AZERTYQSDFGHWXCVBNKLMPUIOJ", help="lettres possibles")
    p.add_argument("--nb", type=int, default=0, help="checks par séquence (0 = au hasard entre 3 et 6)")
    p.add_argument("--pause-entre", type=float, default=0.0, help="pause (cercle caché) entre 2 checks, en s")
    p.add_argument("--pause-fin", type=float, default=1.2, help="pause entre 2 séquences (s)")
    p.add_argument("--decor-fixe", action="store_true", help="décor immobile derrière le cercle")
    p.add_argument("--duree", type=float, default=0, help="quitte tout seul après N secondes")
    args = p.parse_args(argv)
    args.lettres = "".join(dict.fromkeys(c for c in args.lettres.upper() if c.isalnum()))

    sys.setswitchinterval(0.001)  # bot + animation dans le même programme : partage plus fin du CPU
    root = tk.Tk()
    root.title(TITRE)
    root.configure(bg="black")
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    if not args.taille:
        args.taille = int(round(164 * sh / 1080))
    if args.fenetre:
        w, h = min(1100, sw - 40), min(760, sh - 120)
        root.geometry(f"{w}x{h}+{(sw - w) // 2}+{max(0, (sh - h) // 2 - 20)}")
    else:
        w, h = sw, sh
        root.attributes("-fullscreen", True)
    root.attributes("-topmost", True)
    APP = Simulation(root, args, w, h)
    root.after(200, APP.demarrer)
    root.mainloop()


if __name__ == "__main__":
    main()
