import argparse
import ctypes
import os
import queue
import random
import sys
import threading
import time
import tkinter as tk
from collections import Counter
from datetime import datetime

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

import numpy as np
from PIL import ImageTk

import autotouche as at
import overlay
import scene

TITRE = "Simulation crochetage"
DIFFICULTES = {
    "facile": (45, 65, 300),
    "moyen": (30, 45, 420),
    "dur": (18, 30, 540),
}
NB_CHECKS = {"vehicule": (4, 6), "porte": (3, 5)}
BLEU, ROUGE, ANNEAU, VERT_BARRE = "#5aa6d4", "#c93a45", "#06080c", "#2A823C"
VERT_TXT, ROUGE_TXT, ACCENT = "#3ddc84", "#ff5a5a", "#E32344"
ORANGE = "#ff9f1a"

APP = None


class Simulation:
    def __init__(self, root, args, W, H):
        self.root, self.args = root, args
        self.W, self.H = W, H
        self.s = H / 1080.0
        self.cx, self.cy = W // 2, H // 2
        k = self.k = args.taille / 164.0
        self.R = 82 * k
        self.injection = queue.Queue()
        self.moteur = None
        self.overlay = None
        self.thread_bot = None
        self.stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.j = at.Journal("sim_verite", True, self.stamp)
        self.stats = Counter()
        self.par_diff = {d: Counter() for d in DIFFICULTES}
        self.historique = []
        self.etat, self.t_suivant, self.t_hud, self.t_decor = "attente", 0.0, 0.0, 0.0
        self.nb_ticks, self.dt_max, self.nb_lents = 0, 0.0, 0
        self.cycle, self.n_cycle, self.seq, self.i, self.ck = None, 0, [], 0, None
        self.alerte = 15.0
        self.toast_fin = 0.0
        self.clignote = None
        self.voitures, self.n_voiture = [], 0
        self.t_voiture = 0.0
        self.police_jusqua, self.t_police = 0.0, time.perf_counter() + random.uniform(12, 25)
        self.flash = False

        self.c = tk.Canvas(root, width=W, height=H, highlightthickness=0, bg="#05060b")
        self.c.pack(fill="both", expand=True)

        graine = random.randrange(1 << 30)
        self.scenes = {}
        for nom, fn in (("vehicule", scene.scene_vehicule), ("porte", scene.scene_porte)):
            fond, avant, info = fn(W, H, seed=graine)
            self.scenes[nom] = (ImageTk.PhotoImage(fond), ImageTk.PhotoImage(avant), info)
        self.type_scene = "vehicule" if args.scene in ("mix", "vehicule") else "porte"
        fond, avant, self.info = self.scenes[self.type_scene]
        self.it_fond = self.c.create_image(0, 0, image=fond, anchor="nw")
        self.it_avant = self.c.create_image(0, 0, image=avant, anchor="nw")
        self._overlays_scene()
        self._hud()
        self._cercle()
        self.montrer(False)

        root.bind("<KeyPress>", self.clavier)
        root.protocol("WM_DELETE_WINDOW", self.quitter)
        self.j.ev("sim_debut", args=vars(args), ecran=[W, H], k=round(k, 4), centre=[self.cx, self.cy],
                  couleurs={"bleu": BLEU, "rouge": ROUGE, "anneau": ANNEAU, "vert_barre": VERT_BARRE},
                  difficultes=DIFFICULTES, version=at.VERSION)

    def _overlays_scene(self):
        s, c = self.s, self.c
        self.it_clign = []
        for nom in ("vehicule",):
            for x, y in self.scenes[nom][2]["clignotants"]:
                self.it_clign.append(c.create_oval(x - 11 * s, y - 8 * s, x + 11 * s, y + 8 * s,
                                                   fill=ORANGE, outline="", state="hidden"))
        x0, y0, x1, y1 = self.scenes["porte"][2]["tele"]
        self.it_tele = c.create_rectangle(x0, y0, x1, y1, fill="#1c2650", outline="", state="hidden")
        self._etat_overlays()

    def _etat_overlays(self):
        porte = self.type_scene == "porte"
        self.c.itemconfigure(self.it_tele, state="normal" if porte else "hidden")
        if porte:
            for it in self.it_clign:
                self.c.itemconfigure(it, state="hidden")

    def _hud(self):
        s, c, W, H = self.s, self.c, self.W, self.H
        f = lambda taille, gras=False, fam="Segoe UI": (fam, -int(taille * s), "bold" if gras else "normal")
        self.f = f
        self.cp = tk.Canvas(self.root, width=int(478 * s), height=int(278 * s), bg="#07080c", highlightthickness=1,
                            highlightbackground="#262936")
        self.cp.place(x=int(22 * s), y=int(22 * s))
        self.cp.create_rectangle(0, 0, 480 * s, 4 * s, fill=ACCENT, outline="")
        self.cp.create_text(18 * s, 24 * s, anchor="w", text="SIMULATION · CROCHETAGE", fill=ACCENT,
                            font=f(17, True, "Bahnschrift"))
        self.it_panneau = self.cp.create_text(18 * s, 48 * s, anchor="nw", fill="#d9dbe3", font=f(14, False, "Consolas"),
                                              text="")
        c.create_text(W - 40 * s, 30 * s, anchor="ne", text="23:47", fill="white", font=f(40, True, "Bahnschrift"))
        c.create_text(W - 40 * s, 82 * s, anchor="ne", text="Pluie · 9°C · Docks", fill="#9aa0ad", font=f(15))
        c.create_text(W - 40 * s, 112 * s, anchor="ne", text="$ 2 350", fill="#59d185", font=f(30, True, "Bahnschrift"))
        c.create_text(W - 40 * s, 152 * s, anchor="ne", text="Banque  $ 41 200", fill="#b8bcc7", font=f(15))
        c.create_text(W - 40 * s, 176 * s, anchor="ne", text="Sans emploi  ·  ID 147", fill="#7c8190", font=f(14))
        c.create_text(W - 252 * s, 214 * s, anchor="e", text="ALERTE", fill="#c9ccd6", font=f(13, True, "Bahnschrift"))
        c.create_rectangle(W - 240 * s, 206 * s, W - 40 * s, 222 * s, fill="#14161d", outline="#2c2f3b")
        self.it_alerte = c.create_rectangle(W - 238 * s, 208 * s, W - 238 * s, 220 * s, fill=ACCENT, outline="")
        self.it_toast_bg = c.create_rectangle(W - 470 * s, 260 * s, W - 40 * s, 340 * s, fill="#0b0c11",
                                              outline="#2c2f3b", state="hidden")
        self.it_toast_bar = c.create_rectangle(W - 470 * s, 260 * s, W - 464 * s, 340 * s, fill=ACCENT, outline="",
                                               state="hidden")
        self.it_toast_t = c.create_text(W - 448 * s, 280 * s, anchor="w", fill="white", font=f(16, True), text="",
                                        state="hidden")
        self.it_toast_m = c.create_text(W - 448 * s, 312 * s, anchor="w", fill="#aab0bd", font=f(14), text="",
                                        state="hidden", width=400 * s)
        self.img_carte = ImageTk.PhotoImage(scene.minimap(int(118 * s), s, seed=7))
        c.create_image(34 * s, H - 34 * s, image=self.img_carte, anchor="sw")
        c.create_text(290 * s, H - 70 * s, anchor="w", text="Rue des Docks", fill="white", font=f(20, True, "Bahnschrift"))
        c.create_text(290 * s, H - 44 * s, anchor="w", text="Port Sud  ·  Zone 4", fill="#9aa0ad", font=f(14))
        for i, (sym, coul, val) in enumerate((("♥", "#e5484d", 0.82), ("◆", "#4c8dff", 0.40),
                                              ("●", "#f5a524", 0.66), ("≈", "#33c3e8", 0.54))):
            x, y, r = W - (60 + i * 70) * s, H - 60 * s, 26 * s
            c.create_oval(x - r, y - r, x + r, y + r, outline="#1d2029", width=6 * s)
            c.create_arc(x - r, y - r, x + r, y + r, start=90, extent=-359.9 * val, style="arc", outline=coul, width=6 * s)
            c.create_text(x, y, text=sym, fill=coul, font=f(18, True))
        self.it_prompt_bg = c.create_rectangle(self.cx - 330 * s, H - 150 * s, self.cx + 330 * s, H - 80 * s,
                                               fill="#08090d", outline="#2a2d39")
        self.it_prompt = c.create_text(self.cx, H - 128 * s, text="", fill="white", font=f(18, True, "Bahnschrift"))
        self.it_segments = []
        for i in range(6):
            x = self.cx - 3 * 54 * s + i * 54 * s
            self.it_segments.append(c.create_rectangle(x + 4 * s, H - 104 * s, x + 50 * s, H - 94 * s,
                                                       fill="#2a2d39", outline="", state="hidden"))
        self.it_msg = c.create_text(self.cx, self.cy - self.R - 50 * s, fill="white", font=f(26, True), text="")

    def _cercle(self):
        c, k, cx, cy = self.c, self.k, self.cx, self.cy
        r = 73 * k
        self.anneau = c.create_oval(cx - r, cy - r, cx + r, cy + r, outline=ANNEAU, width=18 * k)
        r = 72.5 * k
        self.barre = c.create_arc(cx - r, cy - r, cx + r, cy + r, start=0, extent=40, style="arc", outline=BLEU, width=15 * k)
        self.aiguille = c.create_line(0, 0, 1, 1, fill=ROUGE, width=max(3, round(5 * k)))
        b = 23 * k
        self.boite = c.create_rectangle(cx - b, cy - b, cx + b, cy + b, fill="#0e1015", outline="#151821")
        self.lettre = c.create_text(cx, cy, text="", fill="#ececec", font=("Segoe UI", -int(22 * k)))
        self.cercle = [self.anneau, self.barre, self.aiguille, self.boite, self.lettre]

    def _nouvelle_voiture(self, police=False):
        s, voies = self.s, self.info["voies"]
        voie = random.randrange(len(voies))
        y = voies[voie]
        sens = 1 if voie == 0 else -1
        lg = random.uniform(120, 160) * s
        x = -lg - 10 if sens > 0 else self.W + 10
        v = random.uniform(330, 560) * s * sens
        self.n_voiture += 1
        tag = f"v{self.n_voiture}"
        corps = "#e8e9ee" if police else random.choice(["#20232b", "#2b2f38", "#3a2f2f", "#1d2a36", "#4a4d55", "#b9bcc4"])
        P = lambda u, h: (x + u * lg, y - h * s)
        pts = [P(0, 8), P(0.06, 20), P(0.30, 23), P(0.40, 37), P(0.72, 37), P(0.84, 23), P(1, 19), P(1, 7), P(0.96, 0), P(0.04, 0)]
        if sens < 0:
            pts = [(2 * x + lg - px, py) for px, py in pts]
        it = [self.c.create_polygon(*[v_ for p in pts for v_ in p], fill=corps, outline="", tags=(tag, "trafic"))]
        cab = [P(0.40, 34), P(0.70, 34), P(0.80, 23), P(0.33, 23)]
        if sens < 0:
            cab = [(2 * x + lg - px, py) for px, py in cab]
        it.append(self.c.create_polygon(*[v_ for p in cab for v_ in p], fill="#14161c" if police else "#0b0d12", outline="",
                                        tags=(tag, "trafic")))
        for u in (0.2, 0.8):
            wx, wy = P(u, 0)
            it.append(self.c.create_oval(wx - 9 * s, wy - 9 * s, wx + 9 * s, wy + 5 * s, fill="#050506", outline="",
                                         tags=(tag, "trafic")))
        av_x, ar_x = (x + lg, x) if sens > 0 else (x, x + lg)
        ly = y - 13 * s
        it.append(self.c.create_oval(av_x - 7 * s, ly - 5 * s, av_x + 7 * s, ly + 5 * s, fill="#fff2c4", outline="",
                                     tags=(tag, "trafic")))
        it.append(self.c.create_oval(av_x + sens * 6 * s - 16 * s, ly - 9 * s, av_x + sens * 6 * s + 16 * s, ly + 9 * s,
                                     fill="#5e5844", outline="", tags=(tag, "trafic")))
        it.append(self.c.create_oval(ar_x - 5 * s, ly - 4 * s, ar_x + 5 * s, ly + 4 * s, fill="#ff2424", outline="",
                                     tags=(tag, "trafic")))
        gyro = []
        if police:
            mx = x + 0.56 * lg
            for dx, coul in ((-14, "#ff2a2a"), (14, "#2a5bff")):
                gyro.append((self.c.create_oval(mx + dx * s - 20 * s, y - 52 * s, mx + dx * s + 20 * s, y - 28 * s,
                                                fill=coul, outline="", tags=(tag, "trafic")), coul))
                gyro.append((self.c.create_rectangle(mx + dx * s - 10 * s, y - 43 * s, mx + dx * s + 10 * s, y - 37 * s,
                                                     fill=coul, outline="", tags=(tag, "trafic")), coul))
        self.c.tag_lower(tag, self.it_avant)
        self.voitures.append({"tag": tag, "x": x, "lg": lg, "v": v, "police": police, "gyro": gyro, "y": y})
        if police:
            self.j.ev("decor", quoi="police", voie=voie, sens=sens)

    def maj_decor(self, dt, maintenant):
        if maintenant >= self.t_voiture:
            self._nouvelle_voiture()
            self.t_voiture = maintenant + random.uniform(0.9, 3.2)
        if maintenant >= self.t_police:
            self.police_jusqua = maintenant + random.uniform(3, 6)
            self.t_police = maintenant + random.uniform(18, 40)
            self.toast("Patrouille à proximité", "Une voiture de police passe dans la rue.", "#4c8dff")
        if maintenant < self.police_jusqua and not any(v["police"] for v in self.voitures):
            self._nouvelle_voiture(police=True)
        self.flash = int(maintenant * 8) % 2 == 0
        for v in list(self.voitures):
            dx = v["v"] * dt
            v["x"] += dx
            self.c.move(v["tag"], dx, 0)
            if v["gyro"]:
                for i, (it, coul) in enumerate(v["gyro"]):
                    allume = self.flash if coul == "#ff2a2a" else not self.flash
                    self.c.itemconfigure(it, state="normal" if allume or i % 2 else "hidden")
            if v["x"] > self.W + 40 or v["x"] + v["lg"] < -40:
                self.c.delete(v["tag"])
                self.voitures.remove(v)
        if self.type_scene == "porte" and self.etat != "check" and random.random() < 0.05:
            self.c.itemconfigure(self.it_tele, fill=random.choice(["#1c2650", "#2a3a7a", "#36488f", "#20305f", "#3a3f6a"]))
        if self.clignote and self.type_scene == "vehicule":
            fin, periode = self.clignote
            allume = maintenant < fin and int(maintenant / periode) % 2 == 0
            for it in self.it_clign:
                self.c.itemconfigure(it, state="normal" if allume else "hidden")
            if maintenant >= fin:
                self.clignote = None
        if self.toast_fin and maintenant > self.toast_fin:
            for it in (self.it_toast_bg, self.it_toast_bar, self.it_toast_t, self.it_toast_m):
                self.c.itemconfigure(it, state="hidden")
            self.toast_fin = 0.0

    def decor_pres_du_cercle(self):
        res = []
        for v in self.voitures:
            x0, x1 = (v["x"], v["x"] + v["lg"])
            if x1 > self.cx - 1.3 * self.R and x0 < self.cx + 1.3 * self.R:
                res.append({"type": "police" if v["police"] else "voiture", "x": round(x0 - self.cx), "y": v["y"] - self.cy})
        return res

    def toast(self, titre, msg, couleur=ACCENT, duree=2.8):
        c = self.c
        c.itemconfigure(self.it_toast_bar, fill=couleur)
        c.itemconfigure(self.it_toast_t, text=titre)
        c.itemconfigure(self.it_toast_m, text=msg)
        for it in (self.it_toast_bg, self.it_toast_bar, self.it_toast_t, self.it_toast_m):
            c.itemconfigure(it, state="normal")
        self.toast_fin = time.perf_counter() + duree

    def montrer(self, oui):
        for it in self.cercle:
            self.c.itemconfigure(it, state="normal" if oui else "hidden")

    def placer_aiguille(self, p):
        a = np.radians(-90.0 + p)
        r1, r2 = 52 * self.k, 80 * self.k
        ca, sa = np.cos(a), np.sin(a)
        self.c.coords(self.aiguille, self.cx + r1 * ca, self.cy + r1 * sa, self.cx + r2 * ca, self.cy + r2 * sa)

    def placer_barre(self, debut, fin, couleur=BLEU):
        self.c.itemconfigure(self.barre, start=90 - fin, extent=fin - debut, outline=couleur)

    def message(self, texte, couleur="white"):
        self.c.itemconfigure(self.it_msg, text=texte, fill=couleur)

    def changer_scene(self, nom):
        if nom == self.type_scene:
            return
        self.type_scene = nom
        fond, avant, self.info = self.scenes[nom]
        self.c.itemconfigure(self.it_fond, image=fond)
        self.c.itemconfigure(self.it_avant, image=avant)
        for v in self.voitures:
            self.c.delete(v["tag"])
        self.voitures.clear()
        self._etat_overlays()

    def nouveau_check(self):
        diff = random.choice(list(DIFFICULTES)) if self.args.difficulte == "mix" else self.args.difficulte
        lmin, lmax, vit = DIFFICULTES[diff]
        larg = random.uniform(lmin, lmax)
        bas = 290 if random.random() < self.args.barres_tardives else 100
        debut = random.uniform(bas, 340 - larg)
        return {"diff": diff, "lettre": random.choice(self.args.lettres), "debut": debut,
                "fin": debut + larg, "vit": vit * self.args.vitesse}

    def nouveau_cycle(self):
        nom = random.choice(["vehicule", "vehicule", "porte"]) if self.args.scene == "mix" else self.args.scene
        self.changer_scene(nom)
        lo, hi = NB_CHECKS[nom]
        nb = self.args.nb or random.randint(lo, hi)
        self.n_cycle += 1
        self.seq = []
        for _ in range(nb):
            if self.seq and random.random() < self.args.meme_check:
                p = dict(self.seq[-1])
                d = random.uniform(-1.5, 1.5)
                p["debut"], p["fin"] = p["debut"] + d, p["fin"] + d
                self.seq.append(p)
            else:
                self.seq.append(self.nouveau_check())
        self.i = 0
        self.cycle = {"n": self.n_cycle, "type": nom, "t0": time.perf_counter(), "titre": self.info["titre"]}
        self.message("")
        self.j.ev("cycle_debut", cycle=self.n_cycle, scene=nom, cible=self.info["titre"], nb_checks=nb,
                  checks=[{k: (round(v, 2) if isinstance(v, float) else v) for k, v in ck.items()} for ck in self.seq])
        self.lancer_check()

    def lancer_check(self):
        ck = self.ck = self.seq[self.i]
        self.c.itemconfigure(self.anneau, outline=ANNEAU)
        self.placer_barre(ck["debut"], ck["fin"])
        self.c.itemconfigure(self.lettre, text=ck["lettre"])
        self.placer_aiguille(0)
        self.montrer(True)
        self.t0 = time.perf_counter()
        self.etat = "check"
        self.j.ev("check_debut", cycle=self.n_cycle, n=self.i + 1, lettre=ck["lettre"], diff=ck["diff"],
                  debut=round(ck["debut"], 2), fin=round(ck["fin"], 2), largeur=round(ck["fin"] - ck["debut"], 2),
                  vit=ck["vit"], t0=round(self.t0, 4))
        self.maj_progression()

    def appui(self, touche, quand=None):
        maintenant = quand or time.perf_counter()
        if self.etat != "check":
            self.stats["touches_hors_check"] += 1
            self.j.ev("touche", touche=touche, t_recue=round(maintenant, 4), etat=self.etat, resultat="hors check")
            return
        ck = self.ck
        p = (maintenant - self.t0) * ck["vit"]
        larg = ck["fin"] - ck["debut"]
        pos = (p - ck["debut"]) / larg
        base = dict(cycle=self.n_cycle, n=self.i + 1, touche=touche, attendu=ck["lettre"], t_recue=round(maintenant, 4),
                    p=round(p, 2), debut=round(ck["debut"], 2), fin=round(ck["fin"], 2), largeur=round(larg, 2),
                    pos=round(pos, 4), vit=ck["vit"], diff=ck["diff"])
        if touche != ck["lettre"]:
            self.j.ev("touche", resultat="mauvaise touche", **base)
            return self.echec(f"mauvaise touche {touche} au lieu de {ck['lettre']}", "mauvaise touche")
        if p < ck["debut"]:
            self.j.ev("touche", resultat="trop tôt", ecart_deg=round(ck["debut"] - p, 2), **base)
            return self.echec(f"{touche} trop tôt de {ck['debut'] - p:.0f}°", "trop tôt")
        if p > ck["fin"]:
            self.j.ev("touche", resultat="trop tard", ecart_deg=round(p - ck["fin"], 2), **base)
            return self.echec(f"{touche} trop tard de {p - ck['fin']:.0f}°", "trop tard")
        self.j.ev("touche", resultat="ok", **base)
        self.stats["ok"] += 1
        self.par_diff[ck["diff"]]["ok"] += 1
        self.noter(f"OK    {touche}  {ck['diff']:<6} barre {larg:2.0f}°  à {pos * 100:3.0f}% de la barre")
        self.j.ev("check_fin", cycle=self.n_cycle, n=self.i + 1, resultat="réussi", pos=round(pos, 4))
        self.i += 1
        if self.i >= len(self.seq):
            self.fin_cycle_reussi()
        else:
            self.lancer_check()

    def fin_cycle_reussi(self):
        ck = self.ck
        self.placer_barre(ck["debut"], ck["fin"], VERT_BARRE)
        self.etat, self.t_suivant = "vert", time.perf_counter() + 0.25
        self.stats["cycles_ok"] += 1
        self.stats["cycles"] += 1
        quoi = "Véhicule déverrouillé" if self.type_scene == "vehicule" else "Porte déverrouillée"
        self.message(quoi.upper(), VERT_TXT)
        self.toast(quoi, f"{self.info['titre']} · {len(self.seq)} checks", "#2A823C")
        self.alerte = max(0.0, self.alerte - 6)
        if self.type_scene == "vehicule":
            self.clignote = (time.perf_counter() + 1.0, 0.25)
        self.j.ev("cycle_fin", cycle=self.n_cycle, resultat="crocheté", nb_checks=len(self.seq),
                  duree_ms=round((time.perf_counter() - self.cycle["t0"]) * 1000, 1))
        self.maj_progression()

    def echec(self, raison, type_):
        ck = self.ck
        self.stats["rate"] += 1
        self.stats["cycles"] += 1
        self.stats[f"rate_{type_}"] += 1
        self.par_diff[ck["diff"]]["rate"] += 1
        self.noter(f"RATÉ  {raison}")
        self.message(f"RATÉ : {raison}", ROUGE_TXT)
        decor = self.decor_pres_du_cercle()
        self.j.ev("check_fin", cycle=self.n_cycle, n=self.i + 1, resultat="raté", type=type_, raison=raison,
                  decor_derriere_le_cercle=decor)
        self.j.ev("cycle_fin", cycle=self.n_cycle, resultat="raté", nb_checks=len(self.seq), rate_au_check=self.i + 1,
                  duree_ms=round((time.perf_counter() - self.cycle["t0"]) * 1000, 1))
        if self.moteur is not None and self.moteur.image is not None:
            try:
                dossier = os.path.join(at.LOGS_DIR, "captures", f"sim_{self.stamp}")
                os.makedirs(dossier, exist_ok=True)
                f = os.path.join(dossier, f"cycle{self.n_cycle:03d}_check{self.i + 1}_{type_.replace(' ', '_')}.png")
                at.ecrire_image(f, np.ascontiguousarray(self.moteur.image))
                self.j.ev("capture", fichier=os.path.relpath(f, at.BASE))
            except Exception:
                pass
        self.alerte = min(100.0, self.alerte + 22)
        quoi = "Le crochet a cassé" if self.type_scene == "porte" else "Alarme du véhicule !"
        self.toast(quoi, raison, ACCENT)
        if self.type_scene == "vehicule":
            self.clignote = (time.perf_counter() + 1.6, 0.12)
        if self.alerte >= 100:
            self.alerte = 35.0
            self.police_jusqua = time.perf_counter() + 6
            self.toast("Dispatch : tentative de vol", "Une patrouille arrive dans le secteur.", "#4c8dff", 3.5)
            self.j.ev("decor", quoi="alerte police")
        self.montrer(False)
        self.etat, self.t_suivant = "rate", time.perf_counter() + self.args.pause_fin + 0.4

    def maj_progression(self):
        c, s = self.c, self.s
        en_cycle = self.etat in ("check", "vert")
        if en_cycle:
            quoi = "VÉHICULE" if self.type_scene == "vehicule" else "PORTE"
            c.itemconfigure(self.it_prompt, text=f"CROCHETAGE · {quoi} · {self.info['titre'].split(' · ')[-1]}")
        else:
            cible = "le véhicule" if self.type_scene == "vehicule" else "la porte"
            c.itemconfigure(self.it_prompt, text=f"[E]  Crocheter {cible}")
        n = len(self.seq) if en_cycle else 0
        x0 = self.cx - n * 27 * s
        for i, it in enumerate(self.it_segments):
            if i < n:
                x = x0 + i * 54 * s
                c.coords(it, x + 4 * s, self.H - 104 * s, x + 50 * s, self.H - 94 * s)
                fait = i < self.i or self.etat == "vert"
                c.itemconfigure(it, state="normal", fill="#2A823C" if fait else (ACCENT if i == self.i else "#2a2d39"))
            else:
                c.itemconfigure(it, state="hidden")

    def noter(self, ligne):
        print(f"[sim] {ligne}")
        self.historique = ([ligne] + self.historique)[:7]

    def maj_hud(self):
        st = self.stats
        m = self.moteur
        bot = "désactivé par --sans-bot" if self.args.sans_bot else (
            "démarrage..." if m is None else ("ACTIF" if m.actif else "en pause"))
        taux = f"{100 * st['cycles_ok'] / st['cycles']:.0f} %" if st["cycles"] else "-"
        diffs = "  ".join(f"{d[:3]} {100 * c['ok'] / (c['ok'] + c['rate']):.0f}%" if c["ok"] + c["rate"] else f"{d[:3]} -"
                          for d, c in self.par_diff.items())
        lignes = [
            f"Bot          {bot}",
            f"Checks       {st['ok']} réussis · {st['rate']} ratés",
            f"Crochetages  {st['cycles_ok']} / {st['cycles']}  ·  {taux}",
            f"Par diff.    {diffs}",
            f"Fluidité     {self.fps_affiche:4.0f} img/s",
            f"Journal      logs/sim_*_{self.stamp}",
            "[F6] bot on/off   [Échap] quitter + rapport",
            "",
        ] + self.historique[:5]
        texte = "\n".join(lignes)
        if texte != getattr(self, "_texte_panneau", None):
            self._texte_panneau = texte
            self.cp.itemconfigure(self.it_panneau, text=texte)
        if round(self.alerte) != getattr(self, "_alerte_affichee", None):
            self._alerte_affichee = round(self.alerte)
            s = self.s
            self.c.coords(self.it_alerte, self.W - 238 * s, 208 * s, self.W - 238 * s + 196 * s * self.alerte / 100, 220 * s)

    def tick(self):
        maintenant = time.perf_counter()
        dt = maintenant - self.t_tick
        self.t_tick = maintenant
        self.nb_ticks += 1
        self.dt_max = max(self.dt_max, dt)
        self.nb_lents += dt > 0.025
        if maintenant - self.t_decor >= 1 / 60:
            self.maj_decor(maintenant - self.t_decor if self.t_decor else 1 / 60, maintenant)
            self.t_decor = maintenant
        while not self.injection.empty():
            touche, quand = self.injection.get()
            self.appui(touche, quand)
        if self.etat == "check":
            p = (maintenant - self.t0) * self.ck["vit"]
            if p >= 360:
                self.j.ev("touche", resultat="aucune", cycle=self.n_cycle, n=self.i + 1, attendu=self.ck["lettre"],
                          debut=round(self.ck["debut"], 2), fin=round(self.ck["fin"], 2), vit=self.ck["vit"],
                          diff=self.ck["diff"])
                self.echec("aucun appui, tour complet", "aucun appui")
            else:
                self.placer_aiguille(p)
        elif maintenant >= self.t_suivant:
            if self.etat == "vert":
                self.montrer(False)
                self.message("")
                self.etat, self.t_suivant = "attente", maintenant + self.args.pause_fin + random.uniform(0.2, 1.0)
                self.maj_progression()
            elif self.etat == "rate":
                self.message("")
                self.etat, self.t_suivant = "attente", maintenant + random.uniform(0.2, 0.8)
                self.maj_progression()
            elif self.etat == "attente":
                self.nouveau_cycle()
        if maintenant - self.t_hud > 0.25:
            if maintenant - self.t_fps >= 1.0:
                self.fps_affiche = self.nb_ticks_hud / (maintenant - self.t_fps)
                self.nb_ticks_hud, self.t_fps = 0, maintenant
            self.t_hud = maintenant
            self.maj_hud()
        self.nb_ticks_hud += 1
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
        if getattr(self, "_fini", False):
            return
        self._fini = True
        st = self.stats
        duree = time.perf_counter() - getattr(self, "t_debut", time.perf_counter())
        print(f"\n[sim] Bilan : {st['ok']} checks réussis, {st['rate']} ratés, "
              f"{st['cycles_ok']}/{st['cycles']} crochetages réussis")
        if self.moteur:
            print(f"[sim] Le bot a compté {at.pluriel(self.moteur.nb_reussis, 'check réussi', 'checks réussis')} "
                  f"sur {at.pluriel(self.moteur.nb_appuis, 'appui')}, "
                  f"{at.pluriel(self.moteur.nb_crochetages, 'crochetage')}")
            self.moteur.arreter()
            if self.thread_bot:
                self.thread_bot.join(timeout=2.0)
        if self.nb_ticks:
            print(f"[sim] Fluidité : {self.nb_ticks / max(duree, 1e-6):.0f} images/s, pire saccade {self.dt_max * 1000:.0f} ms, "
                  f"{at.pluriel(self.nb_lents, 'saccade')} > 25 ms")
        self.j.ev("sim_fin", duree_s=round(duree, 1), stats=dict(st),
                  par_difficulte={d: dict(c) for d, c in self.par_diff.items()},
                  images_par_s=round(self.nb_ticks / max(duree, 1e-6), 1), pire_saccade_ms=round(self.dt_max * 1000, 1))
        self.j.fermer()
        try:
            import rapport
            chemin, resume = rapport.generer(self.stamp)
            print(resume)
            print(f"[sim] Rapport : {os.path.relpath(chemin, at.BASE)}")
        except Exception as e:
            print(f"[sim] Rapport impossible : {e}")
        if self.overlay:
            self.overlay.fermer()
        self.root.destroy()

    def preparer_bot(self):
        self.message("Préparation : je cherche le cercle et j'apprends les lettres...")
        at.set_profil("sim")
        nouveau = not os.path.exists(at.CONFIG_PATH)
        cfg = at.charger_config()
        if nouveau:
            cfg["latence_ms"] = 8
        cfg["fenetre_cible"] = TITRE
        cfg["serveur_cible"] = None

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
            sz = int(round(2 * self.R)) + 4
            cfg["zone"] = {"left": self.c.winfo_rootx() + self.cx - (sz - 1) // 2,
                           "top": self.c.winfo_rooty() + self.cy - (sz - 1) // 2, "width": sz, "height": sz}
            print("[sim] ATTENTION : détection auto ratée, zone calculée directement")
        self.j.ev("detection", trouve=bool(det), zone=cfg["zone"], anneau=cfg["anneau"])
        at.sauver_config(cfg)

        zone = cfg["zone"]
        an = at.Analyseur(cfg, zone["height"], zone["width"])
        attendu = at.rogner_modele(np.zeros(an.taille_lettre, np.uint8)).shape
        os.makedirs(at.TEMPLATES_DIR, exist_ok=True)
        a_faire = []
        for lt in self.args.lettres:
            p = os.path.join(at.TEMPLATES_DIR, f"{lt}.png")
            img = at.lire_image(p) if os.path.exists(p) else None
            if img is None or img.shape != attendu:
                a_faire.append(lt)
        if a_faire:
            for it in (self.barre, self.aiguille):
                self.c.itemconfigure(it, state="hidden")
            with at.MSS() as sct:
                for lt in a_faire:
                    self.c.itemconfigure(self.lettre, text=lt)
                    self.root.update()
                    time.sleep(0.06)
                    self.root.update()
                    crop = an.lettre(np.asarray(sct.grab(zone)))
                    at.ecrire_image(os.path.join(at.TEMPLATES_DIR, f"{lt}.png"), at.rogner_modele(crop))
        self.montrer(False)
        self.message("")
        print(f"[sim] {at.pluriel(len(a_faire), 'lettre apprise', 'lettres apprises')} dans {at.dossier_modeles()}/")
        return cfg

    def demarrer_bot(self, cfg):
        self.moteur = at.Moteur(cfg, debug=self.args.debug, actif=not self.args.bot_pause, touche_quitter=False,
                                journal_prefixe="sim_moteur", journal_horodatage=self.stamp)
        self.thread_bot = threading.Thread(target=self.moteur.run, daemon=True)
        self.thread_bot.start()

    def premier_plan(self):
        try:
            u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
            hwnd = u32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            fg = u32.GetForegroundWindow()
            tid_fg, tid_moi = u32.GetWindowThreadProcessId(fg, None), k32.GetCurrentThreadId()
            attache = tid_fg and tid_fg != tid_moi and u32.AttachThreadInput(tid_moi, tid_fg, True)
            u32.BringWindowToTop(hwnd)
            u32.SetForegroundWindow(hwnd)
            if attache:
                u32.AttachThreadInput(tid_moi, tid_fg, False)
        except Exception:
            pass
        self.root.focus_force()
        self.j.ev("premier_plan", titre=at.titre_premier_plan())

    def demarrer(self):
        if not self.args.sans_bot:
            self.demarrer_bot(self.preparer_bot())
            if not self.args.sans_overlay:
                self.overlay = overlay.OverlayAnneau(self.root, lambda: self.moteur, lambda m: m.actif,
                                                     f=self.root.winfo_fpixels("1i") / 96.0)
        self.premier_plan()
        self.t_tick = self.t_debut = time.perf_counter()
        self.t_suivant = self.t_debut + 1.5
        self.t_voiture = self.t_debut + 0.3
        self.fps_affiche, self.nb_ticks_hud, self.t_fps = 0.0, 0, self.t_debut
        self.message("C'est parti !")
        self.maj_progression()
        self.tick()


def main(argv=None):
    global APP
    p = argparse.ArgumentParser(description="Simulation de crochetage façon serveur RP, pour entraîner et tester le bot.")
    p.add_argument("--fenetre", action="store_true", help="dans une fenêtre au lieu du plein écran")
    p.add_argument("--sans-bot", action="store_true", help="ne lance pas le bot, pour jouer toi-même")
    p.add_argument("--bot-pause", action="store_true", help="le bot démarre en pause, F6 pour l'activer")
    p.add_argument("--sans-overlay", action="store_true", help="n'affiche pas l'overlay du bot sur l'anneau")
    p.add_argument("--debug", action="store_true", help="infos de détection du bot en direct dans la console")
    p.add_argument("--scene", default="mix", choices=["mix", "vehicule", "porte"], help="ce qu'on crochète")
    p.add_argument("--difficulte", default="mix", choices=list(DIFFICULTES) + ["mix"])
    p.add_argument("--vitesse", type=float, default=1.0, help="multiplie la vitesse de l'aiguille")
    p.add_argument("--taille", type=int, default=0, help="diamètre du cercle en pixels, par défaut celui du jeu")
    p.add_argument("--lettres", default="AZERTYQSDFGHWXCVBNKLMPUIOJ", help="lettres possibles")
    p.add_argument("--barres-tardives", type=float, default=0.0,
                   help="probabilité d'une barre en fin de tour, après 290°")
    p.add_argument("--meme-check", type=float, default=0.0,
                   help="probabilité qu'un check reprenne la lettre et la barre du précédent, cas piège")
    p.add_argument("--nb", type=int, default=0, help="checks par cycle, 0 pour 4 à 6 sur un véhicule et 3 à 5 sur une porte")
    p.add_argument("--pause-fin", type=float, default=1.2, help="pause entre 2 crochetages, en secondes")
    p.add_argument("--duree", type=float, default=0, help="quitte tout seul après N secondes")
    args = p.parse_args(argv)
    args.lettres = "".join(dict.fromkeys(c for c in args.lettres.upper() if c.isalnum()))

    sys.setswitchinterval(0.001)
    root = tk.Tk()
    root.title(TITRE)
    root.configure(bg="black")
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    if not args.taille:
        args.taille = int(round(164 * sh / 1080))
    if args.fenetre:
        w, h = min(1280, sw - 40), min(720, sh - 120)
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
