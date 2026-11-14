import ctypes
import ctypes.wintypes as wt
import math
import time
import tkinter as tk
import traceback

CLE = "#010101"
FOND = "#0B0C10"
VERT, VERT_OK, CYAN, ROUGE, BLANC, JAUNE, ORANGE, GRIS, GRIS2 = (
    "#00E000", "#3DDC84", "#7DE3FF", "#FF4040", "#FFFFFF", "#FFD400", "#FF9F1A", "#C4C8D0", "#8A8F9A")
POLICE = "Segoe UI"
WDA_EXCLUDEFROMCAPTURE = 0x11

u32 = ctypes.windll.user32
u32.GetParent.restype = wt.HWND
u32.GetParent.argtypes = [wt.HWND]
u32.GetForegroundWindow.restype = wt.HWND
u32.SetForegroundWindow.argtypes = [wt.HWND]
u32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
u32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_long]
u32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
u32.SetWindowDisplayAffinity.argtypes = [wt.HWND, wt.DWORD]
u32.SetWindowDisplayAffinity.restype = wt.BOOL


class OverlayAnneau:
    def __init__(self, root, moteur_fn, visible_fn, f=1.0, exclure=True, periode_ms=8):
        self.root, self.moteur_fn, self.visible_fn, self.f = root, moteur_fn, visible_fn, f
        self.periode = periode_ms
        self.mx, self.my = int(200 * f), int(100 * f)
        self.zone, self.visible, self.ferme = None, False, False
        self.t_top, self.nb_ticks, self.erreur = 0.0, 0, None
        self.cache = {}
        avant = u32.GetForegroundWindow()
        t = self.top = tk.Toplevel(root)
        t.title("Crochetage overlay")
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        t.configure(bg=CLE)
        t.attributes("-transparentcolor", CLE)
        t.attributes("-alpha", 0.0)
        t.geometry("10x10+0+0")
        self.c = tk.Canvas(t, bg=CLE, highlightthickness=0)
        self.c.pack(fill="both", expand=True)
        self._creer()
        t.update_idletasks()
        self.hwnd = u32.GetParent(t.winfo_id()) or t.winfo_id()
        ex = u32.GetWindowLongW(self.hwnd, -20)
        u32.SetWindowLongW(self.hwnd, -20, ex | 0x80000 | 0x20 | 0x80 | 0x8000000 | 0x8)
        self.exclu = bool(exclure) and self.exclusion(True)
        if avant:
            u32.SetForegroundWindow(avant)
        t.after(self.periode, self._tick)

    def exclusion(self, oui):
        return bool(u32.SetWindowDisplayAffinity(self.hwnd, WDA_EXCLUDEFROMCAPTURE if oui else 0))

    def fermer(self):
        self.ferme = True
        try:
            self.top.destroy()
        except Exception:
            pass

    def _creer(self):
        c, f = self.c, self.f
        ep = max(2, round(2 * f))
        self.ep = ep
        self.i_ext = c.create_oval(0, 0, 0, 0, outline=VERT, width=ep)
        self.i_int = c.create_oval(0, 0, 0, 0, outline=VERT, width=ep)
        self.i_flash = c.create_oval(0, 0, 0, 0, outline=BLANC, width=ep * 3)
        self.i_arc = c.create_arc(0, 0, 1, 1, style="arc", outline=CYAN, width=ep)
        self.i_fen = c.create_arc(0, 0, 1, 1, style="arc", outline=JAUNE, width=ep * 2)
        self.i_b1 = c.create_line(0, 0, 0, 0, fill=CYAN, width=ep)
        self.i_b2 = c.create_line(0, 0, 0, 0, fill=CYAN, width=ep)
        self.i_cible = c.create_line(0, 0, 0, 0, fill=JAUNE, width=ep)
        self.i_pred = c.create_line(0, 0, 0, 0, fill=BLANC, width=ep, dash=(5, 4))
        self.i_pt = c.create_oval(0, 0, 0, 0, fill=BLANC, outline="")
        self.i_aig = c.create_line(0, 0, 0, 0, fill=ROUGE, width=ep + 1)
        self.i_box_l = c.create_rectangle(0, 0, 0, 0, fill=FOND, outline=GRIS2)
        self.i_lettre = c.create_text(0, 0, text="", fill=BLANC, font=(POLICE, -round(19 * f), "bold"))
        self.i_box_e = c.create_rectangle(0, 0, 0, 0, fill=FOND, outline=GRIS2)
        self.i_etat = c.create_text(0, 0, text="", fill=BLANC, font=(POLICE, -round(16 * f), "bold"))
        self.i_detail = c.create_text(0, 0, text="", fill=GRIS, font=(POLICE, -round(12 * f)))
        self.mesures = (self.i_arc, self.i_fen, self.i_b1, self.i_b2, self.i_cible, self.i_pred, self.i_pt, self.i_aig)
        for it in (self.i_ext, self.i_int, self.i_flash, self.i_box_l, self.i_box_e, *self.mesures):
            self._etat(it, False)

    def _etat(self, it, oui):
        if self.cache.get(("s", it)) != oui:
            self.cache[("s", it)] = oui
            self.c.itemconfigure(it, state="normal" if oui else "hidden")

    def _conf(self, it, **kw):
        if self.cache.get(("c", it)) != kw:
            self.cache[("c", it)] = kw
            self.c.itemconfigure(it, **kw)

    def _placer(self, zone):
        z = (zone["left"], zone["top"], zone["width"], zone["height"])
        if z == self.zone:
            return
        self.zone = z
        w, h = z[2] + 2 * self.mx, z[3] + 2 * self.my
        self.top.geometry(f"{w}x{h}+{z[0] - self.mx}+{z[1] - self.my}")
        self.top.update_idletasks()
        self.xc, self.yc = self.mx + (z[2] - 1) / 2.0, self.my + (z[3] - 1) / 2.0
        self.R = min(z[2], z[3]) / 2.0
        f = self.f
        self.c.coords(self.i_lettre, self.xc, self.yc - self.R - 24 * f)
        self.c.coords(self.i_etat, self.xc, self.yc + self.R + 24 * f)
        self.c.coords(self.i_detail, self.xc, self.yc + self.R + 46 * f)

    def _montrer(self, oui):
        if oui != self.visible:
            self.visible = oui
            self.top.attributes("-alpha", 1.0 if oui else 0.0)
            self.t_top = 0.0

    def _pt(self, angle, r):
        a = math.radians(angle)
        return self.xc + r * math.cos(a), self.yc + r * math.sin(a)

    def _cercle(self, it, r):
        self.c.coords(it, self.xc - r, self.yc - r, self.xc + r, self.yc + r)

    def _arc(self, it, r, a, b):
        self._cercle(it, r)
        self.c.itemconfigure(it, start=-b, extent=b - a)

    def _boite(self, box, items, couleur, textes):
        cle = (textes, couleur, self.zone)
        if self.cache.get(("b", box)) == cle:
            return
        self.cache[("b", box)] = cle
        bbs = [self.c.bbox(i) for i, t in zip(items, textes) if t]
        if not bbs:
            return self._etat(box, False)
        p = 7 * self.f
        self.c.coords(box, min(b[0] for b in bbs) - p - 3, min(b[1] for b in bbs) - p / 2,
                      max(b[2] for b in bbs) + p + 3, max(b[3] for b in bbs) + p / 2)
        self._conf(box, outline=couleur)
        self._etat(box, True)

    def _tick(self):
        if self.ferme:
            return
        self.nb_ticks += 1
        try:
            self.maj()
        except Exception:
            self.erreur = traceback.format_exc()
        self.top.after(self.periode, self._tick)

    def fenetre_appui(self, dec, debut, fin):
        larg = fin - debut
        m = min(dec.marge, larg * 0.25)
        entree = max(m, dec.cible * larg)
        if dec.vit is None or abs(dec.vit) < 1:
            return debut + m, fin - m
        if dec.vit > 0:
            return debut + entree, fin - m
        return debut + m, fin - entree

    def maj(self):
        m = self.moteur_fn()
        if m is None or not self.visible_fn(m):
            return self._montrer(False)
        an = m.an
        zone = getattr(m, "zone", None) if an is not None else None
        if zone is None:
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            cote = int(round(168 * sh / 1080))
            zone = {"left": (sw - cote) // 2, "top": (sh - cote) // 2, "width": cote, "height": cote}
        self._placer(zone)
        self._montrer(True)
        now = time.perf_counter()
        if now - self.t_top > 0.5:
            self.t_top = now
            u32.SetWindowPos(self.hwnd, wt.HWND(-1), 0, 0, 0, 0, 0x1 | 0x2 | 0x10)

        f, R = self.f, self.R
        barre, dec = m.barre, (getattr(m, "dec", None) if an is not None else None)
        cercle = bool(an is not None and m.cercle)
        dessin = cercle and self.exclu and not m.detection_en_cours
        seuil = float(m.cfg.get("seuil", 0.75))
        reconnu = m.nom if (m.nom and m.score >= seuil) else None
        t_appui, vert = m.t_appui, m.vert

        for it in (self.i_ext, self.i_int):
            self._etat(it, dessin)
        r_in = r_out = 0.0
        if dessin:
            r_in, r_out = an.anneau_r[0] * R, an.anneau_r[1] * R
            self._cercle(self.i_ext, r_out)
            self._cercle(self.i_int, r_in)
        flash = dessin and (vert or now - t_appui < 0.22)
        self._etat(self.i_flash, flash)
        if flash:
            self._cercle(self.i_flash, r_out + 6 * f)
            self._conf(self.i_flash, outline=VERT_OK if vert else BLANC)

        pred = fen = None
        vit = dec.vit if dec is not None else None
        if dessin and barre is not None and not vert:
            centre, debut, fin, rel = barre
            for it in (self.i_arc, self.i_b1, self.i_b2, self.i_aig):
                self._etat(it, True)
            r0 = 0.30 * R
            self.c.coords(self.i_b1, *self._pt(centre + debut, r0), *self._pt(centre + debut, r_out + 5 * f))
            self.c.coords(self.i_b2, *self._pt(centre + fin, r0), *self._pt(centre + fin, r_out + 5 * f))
            self._arc(self.i_arc, r_out + 5 * f, centre + debut, centre + fin)
            self.c.coords(self.i_aig, *self._pt(centre + rel, r0), *self._pt(centre + rel, r_out + 16 * f))
            vise = dec is not None and m.actif and not dec.verrou
            if vise:
                fen = self.fenetre_appui(dec, debut, fin)
                if vit is not None and dec.prev is not None and now - dec.prev_t < 0.3:
                    pred = rel + vit * (dec.lat + (now - dec.prev_t))
            ok_fen = fen is not None and fen[1] > fen[0]
            self._etat(self.i_fen, ok_fen)
            self._etat(self.i_cible, ok_fen)
            if ok_fen:
                self._arc(self.i_fen, r_out + 11 * f, centre + fen[0], centre + fen[1])
                entree = fen[1] if (vit is not None and vit < 0) else fen[0]
                self.c.coords(self.i_cible, *self._pt(centre + entree, r_out + 3 * f),
                              *self._pt(centre + entree, r_out + 20 * f))
            self._etat(self.i_pred, pred is not None)
            self._etat(self.i_pt, pred is not None)
            if pred is not None:
                dedans = ok_fen and fen[0] <= pred <= fen[1]
                coul = JAUNE if dedans else BLANC
                self.c.coords(self.i_pred, *self._pt(centre + pred, r_in - 8 * f), *self._pt(centre + pred, r_out + 16 * f))
                x, y = self._pt(centre + pred, (r_in + r_out) / 2)
                rp = 4 * f
                self.c.coords(self.i_pt, x - rp, y - rp, x + rp, y + rp)
                self._conf(self.i_pred, fill=coul)
                self._conf(self.i_pt, fill=coul)
        else:
            for it in self.mesures:
                self._etat(it, False)

        if cercle and reconnu:
            lettre, coul_l = f"{reconnu}   {m.score * 100:.0f} %", BLANC
        elif cercle and m.inconnue:
            lettre, coul_l = "?   lettre inconnue", ORANGE
        else:
            lettre, coul_l = "", BLANC
        self._conf(self.i_lettre, text=lettre, fill=coul_l)
        self._boite(self.i_box_l, (self.i_lettre,), coul_l if lettre else GRIS2, (lettre,))

        su = m.suivi
        dc = su.dernier_cycle if su else None
        n_check = None
        if su and su.cycle:
            n_check = su.check["n"] if su.check else len(su.cycle["checks"]) + 1
        compteurs = f"crochetés {m.nb_crochetages}  ·  ratés {m.nb_rates}"
        detail = compteurs
        if not m.serveur_ok:
            hote = (m.serveur_url or "aucun").split("//")[-1].strip("/")
            etat, coul, detail = "EN VEILLE : PAS SUR BAYLIFE", GRIS, f"serveur : {hote}"
        elif an is None:
            etat, coul, detail = "CERCLE PAS TROUVÉ", ORANGE, "lance un crochetage puis F7"
        elif m.detection_en_cours:
            etat, coul, detail = "RECHERCHE DU CERCLE…", CYAN, "garde le crochetage affiché"
        elif now - m.t_refus < 1.5:
            etat, coul, detail = "PAS ENVOYÉ", ORANGE, m.refus
        elif dc and now - dc["t"] < 3.0 and dc["resultat"] == "crocheté" and not cercle:
            etat, coul = f"CROCHETÉ  ✓   {dc['nb_checks']} checks", VERT_OK
        elif dc and now - dc["t"] < 3.0 and dc["resultat"] == "raté" and not cercle:
            etat, coul = f"RATÉ AU CHECK {dc['nb_checks']}", ROUGE
        elif cercle and vert:
            etat, coul = "RÉUSSI  ✓", VERT_OK
        elif cercle and now - t_appui < 0.35:
            etat, coul = f"APPUI  {m.appui_nom or ''}", JAUNE
        elif cercle:
            if not m.actif:
                etat, coul = "EN PAUSE  ·  JE REGARDE", GRIS
            elif m.inconnue:
                etat, coul = "JOUE-LA POUR ME L'APPRENDRE", ORANGE
            elif barre is None:
                etat, coul = "CHERCHE LA BARRE", GRIS
            elif dec is not None and dec.verrou:
                etat, coul = "APPUYÉ  ·  ATTEND LE SUIVANT", GRIS
            elif not reconnu:
                etat, coul = "LETTRE PAS RECONNUE", ORANGE
            elif vit is None:
                etat, coul = "MESURE LA VITESSE", CYAN
            else:
                etat, coul = "VISE", BLANC
            morceaux = [f"check {n_check}"] if n_check else []
            if vit is not None:
                morceaux.append(f"{'↻' if vit > 0 else '↺'} {abs(vit):.0f}°/s")
            if pred is not None and fen is not None:
                reste = (fen[0] - pred) if vit > 0 else (pred - fen[1])
                morceaux.append("dans la fenêtre" if reste <= 0 else f"appui dans {reste:.0f}°")
            detail = "  ·  ".join(morceaux) if morceaux else compteurs
        else:
            etat, coul = "PRÊT", GRIS
            if m.fps:
                detail = f"{compteurs}  ·  {m.fps:.0f} img/s"
        self._conf(self.i_etat, text=etat, fill=coul)
        self._conf(self.i_detail, text=detail)
        self._boite(self.i_box_e, (self.i_etat, self.i_detail), coul, (etat, detail))
