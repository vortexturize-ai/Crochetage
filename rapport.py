import argparse
import glob
import json
import os
import statistics as stats
import sys
from collections import Counter, defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(BASE, "logs")


def charger(chemin):
    evs = []
    if chemin and os.path.exists(chemin):
        with open(chemin, encoding="utf-8") as f:
            for ligne in f:
                try:
                    evs.append(json.loads(ligne))
                except ValueError:
                    pass
    return evs


def trouver(stamp=None, jeu=False):
    if stamp:
        v = os.path.join(LOGS, f"sim_verite_{stamp}.jsonl")
        m = os.path.join(LOGS, f"sim_moteur_{stamp}.jsonl")
        if os.path.exists(v):
            return v, (m if os.path.exists(m) else None), stamp, "simulation"
        m = os.path.join(LOGS, f"moteur_{stamp}.jsonl")
        return None, (m if os.path.exists(m) else None), stamp, "jeu"
    sims = sorted(glob.glob(os.path.join(LOGS, "sim_verite_*.jsonl")), key=os.path.getmtime)
    jeux = sorted(glob.glob(os.path.join(LOGS, "moteur_*.jsonl")), key=os.path.getmtime)
    if jeu or (jeux and (not sims or os.path.getmtime(jeux[-1]) > os.path.getmtime(sims[-1]))):
        if not jeux:
            return None, None, None, "jeu"
        st = os.path.basename(jeux[-1])[len("moteur_"):-len(".jsonl")]
        return None, jeux[-1], st, "jeu"
    if not sims:
        return None, None, None, "simulation"
    st = os.path.basename(sims[-1])[len("sim_verite_"):-len(".jsonl")]
    return trouver(st)


def pct(a, b):
    return f"{100 * a / b:.1f} %" if b else "-"


def moy(xs):
    return stats.fmean(xs) if xs else float("nan")


def ecart(xs):
    return stats.pstdev(xs) if len(xs) > 1 else 0.0


def quantile(xs, q):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    i = min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))
    return xs[i]


def histo(xs, bornes, largeur=28):
    lignes = []
    comptes = Counter()
    for x in xs:
        for i, (a, b) in enumerate(bornes):
            if a <= x < b:
                comptes[i] += 1
                break
    mx = max(comptes.values()) if comptes else 1
    for i, (a, b) in enumerate(bornes):
        n = comptes[i]
        etiquette = f"{a * 100:>5.0f} → {b * 100:<4.0f}%" if abs(a) < 9 and abs(b) < 9 else (
            f"   < {b * 100:.0f} %   " if a < -9 else f"   > {a * 100:.0f} %  ")
        lignes.append(f"{etiquette} {'█' * round(largeur * n / mx):<{largeur}} {n}")
    return "\n".join(lignes)


def analyser(verite, moteur):
    V, M = charger(verite), charger(moteur)
    r = {"verite": bool(V), "moteur": bool(M)}
    par = defaultdict(list)
    for e in V:
        par["v_" + e["ev"]].append(e)
    for e in M:
        par["m_" + e["ev"]].append(e)
    r["par"] = par

    deb = (par["m_session_debut"] or [{}])[0]
    r["config"] = deb.get("config", {})
    r["version"] = deb.get("version")
    r["capture"] = (par["m_ecran"] or [{}])[0].get("capture") or "mss"

    touches = [e for e in par["v_touche"] if e.get("resultat") in ("ok", "trop tôt", "trop tard")]
    r["positions"] = [e["pos"] for e in touches]
    r["types_rates"] = Counter(e.get("type") for e in par["v_check_fin"] if e.get("resultat") == "raté")
    r["checks_ok"] = sum(1 for e in par["v_check_fin"] if e.get("resultat") == "réussi")
    r["checks_rates"] = sum(1 for e in par["v_check_fin"] if e.get("resultat") == "raté")
    r["cycles"] = par["v_cycle_fin"]
    diff = defaultdict(Counter)
    scene_par_cycle = {e["cycle"]: e["scene"] for e in par["v_cycle_debut"]}
    sc = defaultdict(Counter)
    for e in par["v_touche"]:
        if "diff" in e and e.get("resultat") != "hors check":
            diff[e["diff"]]["ok" if e["resultat"] == "ok" else "raté"] += 1
    for e in par["v_cycle_fin"]:
        sc[scene_par_cycle.get(e["cycle"], "?")]["ok" if e["resultat"] == "crocheté" else "raté"] += 1
    r["par_diff"], r["par_scene"] = diff, sc
    r["hors_check"] = [e for e in par["v_touche"] if e.get("resultat") == "hors check"]

    appuis = [e for e in par["m_appui"] if e.get("envoye")]
    recues = sorted((e for e in par["v_touche"] if "t_recue" in e and e.get("resultat") != "hors check"),
                    key=lambda e: e["t_recue"])
    paires, j = [], 0
    for a in sorted(appuis, key=lambda e: e["t"]):
        while j < len(recues) and recues[j]["t_recue"] < a["t"] - 0.002:
            j += 1
        if j < len(recues) and recues[j]["t_recue"] - a["t"] < 0.2:
            paires.append((a, recues[j]))
            j += 1
    cal = []
    for a, t in paires:
        if a.get("pos_prevue") is None or not t.get("largeur") or not t.get("vit"):
            continue
        err_pos = t["pos"] - a["pos_prevue"]
        cal.append({"delai_ms": (t["t_recue"] - a["t"]) * 1000, "err_pos": err_pos,
                    "err_ms": err_pos * t["largeur"] / t["vit"] * 1000, "age_ms": a.get("age_image_ms", 0.0),
                    "larg_mes": a.get("largeur"), "larg_vraie": t["largeur"], "diff": t.get("diff")})
    r["calage"] = cal
    r["appuis"] = appuis

    r["m_checks"] = par["m_check_fin"]
    r["m_cycles"] = par["m_cycle_fin"]
    pertes = Counter()
    images = images_barre = 0
    for e in par["m_check_fin"]:
        images += e.get("images", 0)
        images_barre += e.get("images_barre", 0)
        for k, v in (e.get("sans_barre") or {}).items():
            pertes[k] += v
    r["pertes"], r["images"], r["images_barre"] = pertes, images, images_barre
    r["scores_min"] = [e["score_min"] for e in par["m_check_fin"] if e.get("score_min") is not None and e.get("lettre")]
    perf = [e["images_par_s"] for e in par["m_perf"] if e.get("actif", True)]
    r["perf_moteur"] = moy(perf) if perf else None
    fin = (par["v_sim_fin"] or [{}])[0]
    r["sim_fin"] = fin

    accord = Counter()
    m_cyc = sorted(par["m_cycle_fin"], key=lambda e: e["t"])
    for v in par["v_cycle_fin"]:
        proches = [m for m in m_cyc if v["t"] - 0.3 <= m["t"] <= v["t"] + 1.5]
        if not proches:
            accord["pas vu par le moteur"] += 1
            continue
        m = proches[0]
        vrai = "crocheté" if v["resultat"] == "crocheté" else "raté"
        accord["d'accord" if m["resultat"] == vrai else f"moteur « {m['resultat']} », vrai « {vrai} »"] += 1
    r["accord"] = accord
    return r


def recommandations(r):
    rec = []
    cfg = r["config"]
    lat, cible = cfg.get("latence_ms"), cfg.get("cible")
    cal = r["calage"]
    if len(cal) >= 10 and lat is not None:
        e = moy([c["err_ms"] for c in cal])
        if abs(e) >= 1.5:
            sens = "plus tard" if e > 0 else "plus tôt"
            rec.append(f"**Anticipation** : la touche arrive en moyenne {abs(e):.1f} ms {sens} que prévu → "
                       f"`latence_ms` {lat} → **{max(0, round(lat + e))}**.")
        else:
            rec.append(f"**Anticipation** : bien calée (écart moyen {e:+.1f} ms), `latence_ms` = {lat} est bon.")
    pos = r["positions"]
    if len(pos) >= 10 and cible is not None:
        m, sd = moy(pos), ecart(pos)
        tot, tard = r["types_rates"]["trop tôt"], r["types_rates"]["trop tard"]
        rec.append(f"**Où viser** : appuis à {m * 100:.0f} % de la barre en moyenne (cible {cible * 100:.0f} %), "
                   f"dispersion ± {sd * 100:.0f} %.")
        if tard > tot and m > cible + 0.05:
            rec.append(f"Plus de ratés « trop tard » ({tard}) que « trop tôt » ({tot}) : baisser `cible` à "
                       f"**{max(0.15, round(cible - 0.05, 2))}** ou monter `latence_ms`.")
        elif tot > tard and m < cible - 0.05:
            rec.append(f"Plus de ratés « trop tôt » ({tot}) que « trop tard » ({tard}) : monter `cible` à "
                       f"**{min(0.5, round(cible + 0.05, 2))}**.")
    nb_bloques = sum(1 for a in r["par"]["m_appui"] if not a.get("envoye"))
    if r["types_rates"]["aucun appui"] > nb_bloques:
        top = ", ".join(f"{k} ({v})" for k, v in r["pertes"].most_common(3)) or "aucune"
        rec.append(f"**{r['types_rates']['aucun appui']} check(s) sans appui** : le bot a perdu la barre ou l'aiguille. "
                   f"Pertes de détection les plus fréquentes : {top}. Voir les captures dans `logs/captures/`.")
    if r["types_rates"]["mauvaise touche"]:
        rec.append(f"**{r['types_rates']['mauvaise touche']} mauvaise(s) lettre(s)** : monter `seuil` "
                   f"(actuel {cfg.get('seuil')}) ou réapprendre les lettres (F8).")
    sm = r["scores_min"]
    if sm and cfg.get("seuil") is not None and min(sm) < cfg["seuil"] + 0.08:
        rec.append(f"Une lettre a été reconnue de justesse (score mini {min(sm):.2f}, seuil {cfg['seuil']}) : "
                   f"réapprendre cette lettre la rendra plus sûre.")
    if r["hors_check"]:
        rec.append(f"**{len(r['hors_check'])} appui(s) en trop** (hors d'un check) : à corriger dans le moteur.")
    bloques = [a for a in r["par"]["m_appui"] if not a.get("envoye")]
    if bloques:
        raisons = Counter(a.get("raison", "?") for a in bloques)
        rec.append(f"**{len(bloques)} appui(s) bloqué(s) par sécurité** (le jeu n'était pas au premier plan) : "
                   + ", ".join(f"{k} ×{v}" for k, v in raisons.most_common(3))
                   + f". Ce n'est pas une erreur du moteur ; vérifie `fenetre_cible` ({cfg.get('fenetre_cible')}).")
    mauvais = {k: v for k, v in r["accord"].items() if k not in ("d'accord",)}
    if mauvais:
        rec.append("Le moteur s'est trompé sur l'issue de certains crochetages : "
                   + ", ".join(f"{k} ×{v}" for k, v in mauvais.items()) + ".")
    if not rec:
        rec.append("Rien à régler : tout est bon.")
    return rec


def generer(stamp=None, jeu=False):
    verite, moteur, stamp, mode = trouver(stamp, jeu)
    if not (verite or moteur):
        raise FileNotFoundError("aucun journal dans logs/ : lance la simulation ou le bot d'abord")
    r = analyser(verite, moteur)
    L = []
    titre = "simulation" if mode == "simulation" else "en jeu"
    L.append(f"# Rapport Crochetage · {titre} · {stamp}")
    L.append("")
    L.append(f"Journaux : `{os.path.relpath(verite, BASE) if verite else '-'}` · "
             f"`{os.path.relpath(moteur, BASE) if moteur else '-'}`  ")
    cfg = r["config"]
    if cfg:
        L.append(f"Moteur v{r['version']} · latence_ms **{cfg.get('latence_ms')}** · cible **{cfg.get('cible')}** · "
                 f"marge_deg {cfg.get('marge_deg')} · seuil {cfg.get('seuil')} · zone {cfg.get('zone', {}).get('width')} px · "
                 f"capture **{r['capture']}**")
    L.append("")

    L.append("## Résultats")
    L.append("")
    if r["verite"]:
        cyc = r["cycles"]
        ok = sum(1 for e in cyc if e["resultat"] == "crocheté")
        nchk = r["checks_ok"] + r["checks_rates"]
        L.append("| | |")
        L.append("|---|---|")
        L.append(f"| Crochetages réussis | **{ok} / {len(cyc)}** ({pct(ok, len(cyc))}) |")
        L.append(f"| Checks réussis | **{r['checks_ok']} / {nchk}** ({pct(r['checks_ok'], nchk)}) |")
        for t, n in r["types_rates"].most_common():
            L.append(f"| Ratés « {t} » | {n} |")
        L.append(f"| Appuis en trop (hors check) | {len(r['hors_check'])} |")
        fin = r["sim_fin"]
        if fin:
            L.append(f"| Durée | {fin.get('duree_s')} s |")
            L.append(f"| Fluidité de la simulation | {fin.get('images_par_s')} img/s, pire saccade {fin.get('pire_saccade_ms')} ms |")
        if r["perf_moteur"]:
            L.append(f"| Analyses du bot | {r['perf_moteur']:.0f} images/s |")
        L.append("")
        L.append("| Difficulté | Checks réussis |")
        L.append("|---|---|")
        for d in ("facile", "moyen", "dur"):
            c = r["par_diff"].get(d, Counter())
            L.append(f"| {d} | {c['ok']} / {c['ok'] + c['raté']} ({pct(c['ok'], c['ok'] + c['raté'])}) |")
        L.append("")
        L.append("| Scène | Crochetages réussis |")
        L.append("|---|---|")
        for sc_, c in sorted(r["par_scene"].items()):
            L.append(f"| {sc_} | {c['ok']} / {c['ok'] + c['raté']} ({pct(c['ok'], c['ok'] + c['raté'])}) |")
        L.append("")
    else:
        mc = r["m_cycles"]
        res = Counter(e["resultat"] for e in mc)
        L.append("| | |")
        L.append("|---|---|")
        for k in ("crocheté", "raté", "abandonné", "interrompu"):
            if res[k]:
                L.append(f"| Crochetages « {k} » | {res[k]} |")
        rc = Counter(e["resultat"] for e in r["m_checks"])
        for k, v in rc.most_common():
            L.append(f"| Checks « {k} » | {v} |")
        L.append(f"| Appuis envoyés | {len(r['appuis'])} |")
        if r["perf_moteur"]:
            L.append(f"| Analyses du bot | {r['perf_moteur']:.0f} images/s |")
        L.append("")

    pos = r["positions"]
    if pos:
        L.append("## Précision : où tombe l'appui dans la barre")
        L.append("")
        L.append(f"Position réelle de l'aiguille quand le jeu reçoit la touche (0 % = entrée de la barre, 100 % = sortie). "
                 f"Moyenne **{moy(pos) * 100:.1f} %**, écart-type {ecart(pos) * 100:.1f} %, "
                 f"5 % des appuis sous {quantile(pos, 0.05) * 100:.0f} %, 5 % au-dessus de {quantile(pos, 0.95) * 100:.0f} %.")
        L.append("")
        L.append("```")
        bornes = [(-99, 0)] + [(i / 10, (i + 1) / 10) for i in range(10)] + [(1.0, 99)]
        L.append(histo(pos, bornes))
        L.append("```")
        L.append("")
    elif r["appuis"]:
        pp = [a["pos_prevue"] for a in r["appuis"] if a.get("pos_prevue") is not None]
        if pp:
            L.append("## Où le bot vise")
            L.append("")
            L.append(f"Position prévue au moment où le jeu reçoit la touche : moyenne {moy(pp) * 100:.1f} %, "
                     f"écart-type {ecart(pp) * 100:.1f} %. Âge moyen de l'image : "
                     f"{moy([a.get('age_image_ms', 0) for a in r['appuis']]):.1f} ms.")
            L.append("")

    cal = r["calage"]
    if cal:
        L.append("## Calage : prévu par le bot / réel")
        L.append("")
        L.append("| | moyenne | écart-type | mini | maxi |")
        L.append("|---|---|---|---|---|")
        for nom, cle, f in (("Délai décision → touche reçue (ms)", "delai_ms", 1), ("Écart position réelle − prévue (% barre)", "err_pos", 100),
                            ("Même écart en temps (ms)", "err_ms", 1), ("Âge de l'image au moment de décider (ms)", "age_ms", 1)):
            xs = [c[cle] * f for c in cal]
            L.append(f"| {nom} | {moy(xs):+.1f} | {ecart(xs):.1f} | {min(xs):+.1f} | {max(xs):+.1f} |")
        dl = [c["larg_mes"] - c["larg_vraie"] for c in cal if c["larg_mes"] is not None]
        if dl:
            L.append(f"| Largeur de barre mesurée − vraie (°) | {moy(dl):+.1f} | {ecart(dl):.1f} | {min(dl):+.1f} | {max(dl):+.1f} |")
        L.append("")

    if r["images"]:
        L.append("## Détection")
        L.append("")
        L.append(f"Pendant les checks : {r['images']} images analysées, barre + aiguille trouvées sur "
                 f"**{pct(r['images_barre'], r['images'])}**.")
        if r["pertes"]:
            L.append("")
            L.append("| Image sans barre / aiguille : raison | images |")
            L.append("|---|---|")
            for k, v in r["pertes"].most_common():
                L.append(f"| {k} | {v} |")
        if r["scores_min"]:
            L.append("")
            L.append(f"Reconnaissance des lettres : score le plus bas {min(r['scores_min']):.2f} "
                     f"(moyenne des minimums {moy(r['scores_min']):.2f}).")
        L.append("")

    if r["accord"]:
        L.append("## Le bot sait-il s'il a réussi ?")
        L.append("")
        L.append("Le bot déduit l'issue tout seul (check suivant apparu, barre verte, cercle disparu). "
                 "Comparaison avec la vérité, crochetage par crochetage :")
        L.append("")
        for k, v in r["accord"].most_common():
            L.append(f"- {k} : {v}")
        L.append("")

    rates = [e for e in r["par"]["v_check_fin"] if e.get("resultat") == "raté"]
    if rates:
        L.append("## Chaque raté")
        L.append("")
        L.append("| Cycle | Check | Type | Raison | Derrière le cercle | Le bot a vu |")
        L.append("|---|---|---|---|---|---|")
        mcf = r["m_checks"]
        for e in rates[:60]:
            vu = [m for m in mcf if abs(m["t"] - e["t"]) < 1.0]
            vu_txt = "-"
            if vu:
                m = vu[0]
                sb = ", ".join(f"{k} ×{v}" for k, v in (m.get("sans_barre") or {}).items()) or "barre toujours vue"
                vu_txt = f"{m.get('resultat')} · {m.get('images_barre')}/{m.get('images')} img · {sb}"
            derriere = ", ".join(d["type"] for d in e.get("decor_derriere_le_cercle") or []) or "-"
            L.append(f"| {e['cycle']} | {e['n']} | {e.get('type')} | {e.get('raison')} | {derriere} | {vu_txt} |")
        L.append("")
    elif not r["verite"]:
        rm = [e for e in r["m_checks"] if str(e.get("resultat", "")).startswith("raté")]
        if rm:
            L.append("## Chaque raté (vu par le bot)")
            L.append("")
            L.append("| Cycle | Check | Lettre | Barre (°) | Vitesse (°/s) | Visé à | Images avec barre | Pertes |")
            L.append("|---|---|---|---|---|---|---|---|")
            for e in rm[:60]:
                a = e.get("appui") or {}
                pp = f"{a['pos_prevue'] * 100:.0f} %" if a.get("pos_prevue") is not None else "-"
                sb = ", ".join(f"{k} ×{v}" for k, v in (e.get("sans_barre") or {}).items()) or "-"
                L.append(f"| {e['cycle']} | {e['n']} | {e.get('lettre')} | {e.get('largeur')} | {e.get('vit')} | {pp} | "
                         f"{e.get('images_barre')}/{e.get('images')} | {sb} |")
            L.append("")

    caps = r["par"]["v_capture"] + r["par"]["m_captures"]
    if caps:
        L.append(f"Images des ratés : `logs/captures/` ({len(caps)} raté(s) capturé(s)).")
        L.append("")

    L.append("## Recommandations")
    L.append("")
    rec = recommandations(r)
    for x in rec:
        L.append(f"- {x}")
    L.append("")

    os.makedirs(LOGS, exist_ok=True)
    chemin = os.path.join(LOGS, f"rapport_{stamp}.md")
    with open(chemin, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    res = ["", "[rapport] ---------------------------------------------"]
    if r["verite"]:
        cyc = r["cycles"]
        ok = sum(1 for e in cyc if e["resultat"] == "crocheté")
        res.append(f"[rapport] Crochetages {ok}/{len(cyc)} · checks {r['checks_ok']}/{r['checks_ok'] + r['checks_rates']}"
                   + (" · ratés : " + ", ".join(f"{k} {v}" for k, v in r["types_rates"].items()) if r["types_rates"] else ""))
    if pos:
        res.append(f"[rapport] Appui à {moy(pos) * 100:.0f} % de la barre en moyenne (± {ecart(pos) * 100:.0f} %)")
    for x in rec:
        res.append("[rapport] " + x.replace("**", "").replace("`", ""))
    return chemin, "\n".join(res)


def main(argv=None):
    p = argparse.ArgumentParser(description="Rapport d'analyse des journaux de Crochetage.")
    p.add_argument("stamp", nargs="?", help="horodatage AAAAMMJJ_HHMMSS (défaut : la dernière session)")
    p.add_argument("--jeu", action="store_true", help="dernière session en jeu (pas la simulation)")
    a = p.parse_args(argv)
    try:
        chemin, resume = generer(a.stamp, a.jeu)
    except FileNotFoundError as e:
        print(e)
        return 1
    print(resume)
    print(f"Rapport complet : {os.path.relpath(chemin, BASE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
