import os
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw

BASE = os.path.dirname(os.path.abspath(__file__))
ICONE = os.path.join(BASE, "crochetage.ico")
APPLI = os.path.join(BASE, "Crochetage.pyw")
TAILLES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]


def logo():
    im = Image.open(os.path.join(BASE, "logo.png")).convert("RGBA")
    bbox = im.getchannel("A").point(lambda a: 255 if a > 90 else 0).getbbox()
    if bbox:
        im = im.crop(bbox)
    s = max(im.size)
    carre = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    carre.paste(im, ((s - im.width) // 2, (s - im.height) // 2))
    a = np.array(carre)
    a[..., 3] = np.where(a[..., 3] > 90, a[..., 3], 0)
    return Image.fromarray(a)


def badge(taille, s_logo):
    s = taille * 4
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.24), fill=(12, 12, 15, 255))
    m = int(s * 0.74)
    im.alpha_composite(s_logo.resize((m, m), Image.LANCZOS), ((s - m) // 2, (s - m) // 2))
    return im.resize((taille, taille), Image.LANCZOS)


def creer_icone():
    s_logo = logo()
    grande = badge(256, s_logo)
    grande.save(ICONE, format="ICO", sizes=[(t, t) for t in TAILLES])
    return ICONE


def pythonw():
    exe = sys.executable
    w = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return w if os.path.exists(w) else exe


def creer_raccourci(dossier_special):
    env = dict(os.environ, CRO_CIBLE=pythonw(), CRO_ARGS=f'"{APPLI}"', CRO_DOSSIER=BASE, CRO_ICONE=f"{ICONE},0",
               CRO_SPECIAL=dossier_special)
    script = (
        "$d = [Environment]::GetFolderPath($env:CRO_SPECIAL); "
        "$p = Join-Path $d 'Crochetage.lnk'; "
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($p); "
        "$s.TargetPath = $env:CRO_CIBLE; $s.Arguments = $env:CRO_ARGS; "
        "$s.WorkingDirectory = $env:CRO_DOSSIER; $s.IconLocation = $env:CRO_ICONE; "
        "$s.Description = 'Crochetage'; $s.Save(); Write-Output $p"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], env=env,
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "raccourci impossible")
    return r.stdout.strip()


def main():
    print("Icône :", creer_icone())
    for dossier in ("Desktop", "Programs"):
        print("Raccourci :", creer_raccourci(dossier))
    print("Pour l'avoir dans la barre des tâches : clic droit sur le raccourci du Bureau, "
          "Afficher d'autres options, Épingler à la barre des tâches.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
