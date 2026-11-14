import os
import queue
import tempfile
import threading
import wave
import winsound

import numpy as np

TAUX = 44100
DOSSIER = os.path.join(tempfile.gettempdir(), "crochetage_sons")
VERSION = 1


def _t(duree):
    return np.arange(int(duree * TAUX)) / TAUX


def _placer(piste, son, debut):
    i = int(debut * TAUX)
    j = min(len(piste), i + len(son))
    piste[i:j] += son[:j - i]


def _bruit(duree, graine):
    return np.random.default_rng(graine).uniform(-1.0, 1.0, int(duree * TAUX))


def _passe_bas(x, n):
    return np.convolve(x, np.ones(n) / n, mode="same")


def _passe_haut(x):
    return np.diff(x, prepend=0.0)


def _decroit(t, tau, attaque=0.0015):
    env = np.exp(-t / tau)
    if attaque > 0:
        env *= np.clip(t / attaque, 0.0, 1.0)
    return env


def _partiels(t, freqs, amps, taus, glisse=0.0):
    s = np.zeros_like(t)
    for f, a, tau in zip(freqs, amps, taus):
        phase = 2 * np.pi * f * (t - glisse * t * t / 2)
        s += a * np.sin(phase) * _decroit(t, tau)
    return s


def goupille(n=1):
    h = 1.0 + 0.045 * (min(max(n, 1), 12) - 1)
    piste = np.zeros(int(0.12 * TAUX))
    t = _t(0.004)
    _placer(piste, 0.55 * _passe_haut(_bruit(0.004, 7 + n)) * _decroit(t, 0.0012, 0), 0.0)
    t = _t(0.11)
    _placer(piste, _partiels(t, [2300 * h, 6350 * h, 12400 * h], [0.55, 0.22, 0.08], [0.026, 0.014, 0.007]), 0.0)
    _placer(piste, 0.32 * np.sin(2 * np.pi * 185 * h * t) * _decroit(t, 0.016), 0.0)
    return piste


def crochete():
    piste = np.zeros(int(1.05 * TAUX))
    t = _t(0.35)
    frequence = 55 + 22 * np.exp(-t / 0.05)
    _placer(piste, 0.95 * np.sin(2 * np.pi * np.cumsum(frequence) / TAUX) * _decroit(t, 0.075), 0.0)
    _placer(piste, 0.45 * _passe_bas(_bruit(0.06, 3), 9) * _decroit(_t(0.06), 0.012, 0), 0.0)
    t = _t(0.2)
    _placer(piste, 0.35 * _passe_haut(_bruit(0.006, 4)) * _decroit(_t(0.006), 0.002, 0), 0.045)
    _placer(piste, _partiels(t, [1420, 3150, 4780], [0.42, 0.24, 0.12], [0.032, 0.02, 0.012]), 0.045)
    for k, (f, debut) in enumerate(((1046.5, 0.13), (1318.5, 0.21), (1568.0, 0.29))):
        t = _t(0.7)
        note = (np.sin(2 * np.pi * f * t) + 0.18 * np.sin(4 * np.pi * f * t)) * _decroit(t, 0.26, 0.004)
        _placer(piste, 0.26 * note, debut)
    return piste


def rate():
    piste = np.zeros(int(0.7 * TAUX))
    t = _t(0.02)
    _placer(piste, 0.9 * _passe_haut(_bruit(0.02, 11)) * _decroit(t, 0.006, 0), 0.0)
    t = _t(0.3)
    _placer(piste, _partiels(t, [830, 1920, 3350], [0.36, 0.22, 0.12], [0.09, 0.05, 0.03], glisse=0.6), 0.004)
    t = _t(0.45)
    frequence = 70 + 150 * np.exp(-t / 0.12)
    bwoop = np.tanh(2.2 * np.sin(2 * np.pi * np.cumsum(frequence) / TAUX)) * _decroit(t, 0.2, 0.01)
    _placer(piste, 0.42 * bwoop, 0.06)
    return piste


SONS = {"crochete": crochete, "rate": rate, "silence": lambda: np.zeros(int(0.01 * TAUX))}
SONS.update({f"goupille_{n}": (lambda n=n: goupille(n)) for n in range(1, 13)})


def ecrire_wav(chemin, signal, volume):
    s = np.asarray(signal, np.float64)
    pic = float(np.max(np.abs(s))) or 1.0
    s = s / pic * 0.89 * max(0.0, min(1.0, volume))
    fondu = min(len(s), int(0.006 * TAUX))
    if fondu:
        s[-fondu:] *= np.linspace(1.0, 0.0, fondu)
    donnees = (np.clip(s, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    tmp = chemin + ".tmp"
    with wave.open(tmp, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(TAUX)
        w.writeframes(donnees)
    os.replace(tmp, chemin)


class Sons:
    def __init__(self, volume=0.7):
        self.verrou = threading.Lock()
        self.fichiers = {}
        self.volume = None
        self.preparer(volume)
        self.file = queue.Queue()
        threading.Thread(target=self._boucle, daemon=True).start()
        self.file.put(("silence", None))

    def _boucle(self):
        while True:
            nom, volume = self.file.get()
            if volume is not None and round(float(volume), 2) != self.volume:
                self.preparer(volume)
            chemin = self.fichiers.get(nom)
            if chemin and (self.volume > 0 or nom == "silence"):
                try:
                    winsound.PlaySound(chemin, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
                except RuntimeError:
                    pass

    def preparer(self, volume):
        v = round(max(0.0, min(1.0, float(volume))), 2)
        with self.verrou:
            if v == self.volume:
                return
            dossier = os.path.join(DOSSIER, f"v{VERSION}_{int(v * 100)}")
            os.makedirs(dossier, exist_ok=True)
            fichiers = {}
            for nom, fn in SONS.items():
                chemin = os.path.join(dossier, f"{nom}.wav")
                if not os.path.exists(chemin):
                    ecrire_wav(chemin, fn(), v)
                fichiers[nom] = chemin
            self.fichiers, self.volume = fichiers, v

    def jouer(self, nom, volume=None):
        self.file.put((nom, volume))

    def goupille(self, n, volume=None):
        self.jouer(f"goupille_{min(max(int(n or 1), 1), 12)}", volume)
