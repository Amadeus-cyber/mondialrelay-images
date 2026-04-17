#!/usr/bin/env python3
"""ZeuScraper — CLI interactif avec animations et navigation."""

import os
import sys
import time
import threading

try:
    from core import (scrape_index, scrape_url, scrape_bgp, scrape_ripe,
                      extract, save_files, build_lists, resolve_path)
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Missing deps. Run: pip install requests beautifulsoup4")
    sys.exit(1)

# ── ANSI ──────────────────────────────────────────────────────────────────────

R    = "\033[91m"
G    = "\033[92m"
Y    = "\033[93m"
B    = "\033[94m"
M    = "\033[95m"
C    = "\033[96m"
W    = "\033[97m"
DIM  = "\033[2m"
BOLD = "\033[1m"
RST  = "\033[0m"

def col(color, text): return f"{color}{text}{RST}"

CLR = "\033[2J\033[H"   # clear screen + cursor home

# ── Settings (modifiables via menu Paramètres) ────────────────────────────────

CFG = {
    "workers":  5,
    "timeout":  30,
    "exts":     "txt,gz",
    "limit":    0,
    "dout":     "domains.txt",
    "iout":     "ips.txt",
    "depth":    2,
    "outdir":   "",        # dossier de sortie (vide = répertoire courant)
}

# ── ASCII Banner ──────────────────────────────────────────────────────────────

BANNER_LINES = [
    f"{R} ███████╗{Y}███████╗{G}██╗   ██╗{C}███████╗{M}██████╗ {RST}",
    f"{R} ╚══███╔╝{Y}██╔════╝{G}██║   ██║{C}██╔════╝{M}██╔══██╗{RST}",
    f"{R}   ███╔╝ {Y}█████╗  {G}██║   ██║{C}███████╗{M}██████╔╝{RST}",
    f"{R}  ███╔╝  {Y}██╔══╝  {G}██║   ██║{C}╚════██║{M}██╔══██╗{RST}",
    f"{R} ███████╗{Y}███████╗{G}╚██████╔╝{C}███████║{M}██║  ██║{RST}",
    f"{R} ╚══════╝{Y}╚══════╝{G} ╚═════╝ {C}╚══════╝{M}╚═╝  ╚═╝{RST}",
    f"",
    f"{DIM}   ──────────────────────────────────────────{RST}",
    f"{BOLD}{W}        ZeuScraper  {DIM}v3.0  •  Domain & IP{RST}",
    f"{DIM}   ──────────────────────────────────────────{RST}",
]

def print_banner(animate=True):
    print(CLR, end="")
    for line in BANNER_LINES:
        print(f"  {line}")
        if animate:
            time.sleep(0.04)
    print()

# ── Spinner ───────────────────────────────────────────────────────────────────

class Spinner:
    FRAMES = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]
    def __init__(self, label=""):
        self.label   = label
        self._stop   = threading.Event()
        self._thread = threading.Thread(target=self._spin, daemon=True)

    def _spin(self):
        i = 0
        while not self._stop.is_set():
            frame = self.FRAMES[i % len(self.FRAMES)]
            print(f"\r  {C}{frame}{RST} {self.label}  ", end="", flush=True)
            time.sleep(0.08)
            i += 1

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        self._thread.join()
        print(f"\r{' '*60}\r", end="", flush=True)


# ── Barre de progression ──────────────────────────────────────────────────────

def progress_bar(done, total, width=30):
    pct  = done / total if total else 0
    fill = int(width * pct)
    bar  = f"{G}{'█' * fill}{DIM}{'░' * (width - fill)}{RST}"
    return f"[{bar}] {W}{done}/{total}{RST} ({pct*100:.0f}%)"


# ── UI helpers ────────────────────────────────────────────────────────────────

def info(msg):  print(f"  {C}›{RST} {msg}")
def ok(msg):    print(f"  {G}✔{RST} {msg}")
def err(msg):   print(f"  {R}✘{RST} {msg}")
def warn(msg):  print(f"  {Y}⚠{RST} {msg}")
def sep():      print(f"\n  {DIM}{'─'*46}{RST}\n")

BACK_CMDS = {"b", "back", "retour", "r"}

def ask(prompt, default="", secret=False):
    """Retourne None si l'utilisateur tape 'b' pour revenir en arrière."""
    hint = f" {DIM}[{default}]{RST}" if default else ""
    back = f"  {DIM}(b = retour){RST}"
    try:
        val = input(f"  {Y}›{RST} {BOLD}{prompt}{RST}{hint}{back} : ").strip()
    except (KeyboardInterrupt, EOFError):
        return None
    if val.lower() in BACK_CMDS:
        return None
    return val if val else default


def pause():
    try:
        input(f"\n  {DIM}[ Entrée pour continuer... ]{RST}")
    except (KeyboardInterrupt, EOFError):
        pass


def ask_outpaths(dout_default=None, iout_default=None):
    """Demande dossier + noms fichiers. Retourne (dout, iout) ou (None, None) si retour."""
    import os
    dout_default = dout_default or CFG["dout"]
    iout_default = iout_default or CFG["iout"]

    print(f"\n  {DIM}Dossier de sortie : tape un chemin ou appuie sur Entrée pour utiliser{RST}")
    print(f"  {DIM}le répertoire courant. Tape {W}b{DIM} pour annuler.{RST}")
    outdir = ask("Dossier de sortie", CFG["outdir"] or "(courant)")
    if outdir is None:
        return None, None
    if outdir in ("(courant)", ".", "./"):
        outdir = ""

    dout_name = ask("Nom fichier domaines", dout_default)
    if dout_name is None:
        return None, None

    iout_name = ask("Nom fichier IPs/CIDRs", iout_default)
    if iout_name is None:
        return None, None

    dout = resolve_path(outdir, dout_name)
    iout = resolve_path(outdir, iout_name)
    return dout, iout


def parse_exts(raw: str) -> list:
    """Retourne [] si toutes extensions, sinon liste normalisée."""
    raw = raw.strip()
    if raw in ("*", "all", "ALL", "tout", "toutes", ""):
        return []
    return [e.strip().lstrip(".").lower() for e in raw.split(",") if e.strip()]


# ── Menus ─────────────────────────────────────────────────────────────────────

MAIN_MENU = f"""
{BOLD}{W}  ╔══════════════════════════════════════════╗
  ║         {C}  Z E U S C R A P E R {W}            ║
  ╠══════════════════════════════════════════╣
  ║  {G}[1]{W}  Index Apache    {DIM}(répertoire web){W}     ║
  ║  {G}[2]{W}  URL directe     {DIM}(page / fichier){W}     ║
  ║  {G}[3]{W}  Source BGP      {DIM}(ASN → CIDRs){W}       ║
  ║  {G}[4]{W}  Source RIPE     {DIM}(org → CIDRs){W}       ║
  ║  {Y}[5]{W}  Paramètres      {DIM}(config défaut){W}      ║
  ║  {R}[0]{W}  Quitter                              ║
  ╚══════════════════════════════════════════╝{RST}
"""


def show_cfg():
    sep()
    outdir_label = CFG['outdir'] or f"{DIM}(répertoire courant){RST}"
    print(f"  {BOLD}{W}Paramètres actuels :{RST}\n")
    print(f"   {C}workers{RST}  = {W}{CFG['workers']}{RST}   {DIM}(threads parallèles){RST}")
    print(f"   {C}timeout{RST}  = {W}{CFG['timeout']}s{RST}")
    print(f"   {C}exts{RST}     = {W}{CFG['exts']}{RST}   {DIM}(* = toutes){RST}")
    print(f"   {C}limit{RST}    = {W}{CFG['limit'] or 'aucun'}{RST}")
    print(f"   {C}depth{RST}    = {W}{CFG['depth']}{RST}")
    print(f"   {C}outdir{RST}   = {W}{outdir_label}")
    print(f"   {C}dout{RST}     = {W}{CFG['dout']}{RST}   {DIM}(nom du fichier domaines){RST}")
    print(f"   {C}iout{RST}     = {W}{CFG['iout']}{RST}   {DIM}(nom du fichier IPs){RST}")
    sep()


def menu_params():
    while True:
        show_cfg()
        print(f"  {BOLD}{W}Que modifier ?{RST}\n")
        print(f"   {G}[1]{RST} Workers (threads)   {G}[2]{RST} Timeout")
        print(f"   {G}[3]{RST} Extensions          {G}[4]{RST} Limite fichiers")
        print(f"   {G}[5]{RST} Profondeur liens    {G}[6]{RST} Dossier de sortie")
        print(f"   {G}[7]{RST} Fichier domaines    {G}[8]{RST} Fichier IPs")
        print(f"   {R}[0]{RST} Retour\n")

        ch = ask("Choix", "0")
        if ch is None or ch == "0":
            return

        if ch == "1":
            v = ask("Nombre de workers", str(CFG["workers"]))
            if v:
                try:
                    CFG["workers"] = max(1, min(50, int(v)))
                    ok(f"Workers → {CFG['workers']}")
                except ValueError:
                    err("Valeur invalide")
        elif ch == "2":
            v = ask("Timeout (secondes)", str(CFG["timeout"]))
            if v:
                try:
                    CFG["timeout"] = max(5, int(v))
                    ok(f"Timeout → {CFG['timeout']}s")
                except ValueError:
                    err("Valeur invalide")
        elif ch == "3":
            print(f"   {DIM}Tape les extensions séparées par virgule.{RST}")
            print(f"   {DIM}Tape {W}*{DIM} ou {W}all{DIM} pour accepter TOUTES les extensions.{RST}")
            v = ask("Extensions (ex: txt,gz  ou  *)", CFG["exts"])
            if v:
                CFG["exts"] = v
                label = "TOUTES" if v in ("*", "all") else v
                ok(f"Extensions → {label}")
        elif ch == "4":
            v = ask("Limite (0 = aucune)", str(CFG["limit"]))
            if v is not None:
                try:
                    CFG["limit"] = max(0, int(v))
                    ok(f"Limite → {CFG['limit'] or 'aucune'}")
                except ValueError:
                    err("Valeur invalide")
        elif ch == "5":
            v = ask("Profondeur", str(CFG["depth"]))
            if v:
                try:
                    CFG["depth"] = max(1, int(v))
                    ok(f"Profondeur → {CFG['depth']}")
                except ValueError:
                    err("Valeur invalide")
        elif ch == "6":
            print(f"   {DIM}Chemin absolu ou relatif. Ex: /home/user/results  ou  ./output{RST}")
            v = ask("Dossier de sortie", CFG["outdir"] or "(courant)")
            if v and v != "(courant)":
                CFG["outdir"] = "" if v in (".", "./", "(courant)") else v
                ok(f"outdir → {CFG['outdir'] or '(répertoire courant)'}")
        elif ch == "7":
            v = ask("Nom fichier domaines", CFG["dout"])
            if v:
                CFG["dout"] = v
                ok(f"dout → {CFG['dout']}")
        elif ch == "8":
            v = ask("Nom fichier IPs", CFG["iout"])
            if v:
                CFG["iout"] = v
                ok(f"iout → {CFG['iout']}")
        time.sleep(0.3)


# ── Affichage résultat ────────────────────────────────────────────────────────

def show_results(results, dout, iout):
    nd, ni = save_files(results, dout, iout)
    sep()
    print(f"  {BOLD}{G}── Résultats ──────────────────────────────{RST}")
    print(f"  {G}✔{RST}  Domaines   → {col(C, dout):30}  {col(W, str(nd))} entrées")
    print(f"  {G}✔{RST}  IPs/CIDRs  → {col(C, iout):30}  {col(W, str(ni))} entrées")

    domains, all_ips = build_lists(results)
    if domains:
        sep()
        print(f"  {DIM}Aperçu domaines (5 premiers) :{RST}")
        for d in domains[:5]:
            print(f"    {G}·{RST} {d}")
    if all_ips:
        print(f"\n  {DIM}Aperçu IPs/CIDRs (5 premiers) :{RST}")
        for ip in all_ips[:5]:
            print(f"    {M}·{RST} {ip}")
    sep()


# ── Mode 1 : Index Apache ─────────────────────────────────────────────────────

def mode_index():
    sep()
    print(f"  {BOLD}{C}── Index Apache ──────────────────────────{RST}\n")

    url = ask("URL de l'index")
    if url is None: return

    print(f"  {DIM}Extensions : tape {W}*{DIM} ou {W}all{DIM} pour toutes, ou ex: {W}txt,gz,csv{RST}")
    exts_str = ask("Extensions", CFG["exts"])
    if exts_str is None: return
    exts = parse_exts(exts_str)
    exts_label = col(Y, "TOUTES") if not exts else col(W, ",".join(exts))

    wrk_str = ask("Workers (threads parallèles)", str(CFG["workers"]))
    if wrk_str is None: return
    try: workers = max(1, min(50, int(wrk_str)))
    except ValueError: workers = CFG["workers"]

    lim_str = ask("Limite fichiers (0 = tous)", str(CFG["limit"]))
    if lim_str is None: return
    try: limit = max(0, int(lim_str))
    except ValueError: limit = CFG["limit"]

    dout, iout = ask_outpaths()
    if dout is None: return

    sep()
    info(f"Extensions : {exts_label}")
    info(f"Workers    : {col(W, str(workers))}")
    info(f"Sortie     : {col(C, dout)} / {col(C, iout)}")
    info(f"Connexion à l'index…")

    _done_total = [0, 0]
    _dom_total  = [0]
    _ip_total   = [0]

    def progress_cb(done, total, file_url, data):
        _done_total[0] = done
        _done_total[1] = total
        _dom_total[0] += len(data["domains"])
        _ip_total[0]  += len(data["cidrs"]) + len(data["ips"])
        fname = file_url.split("/")[-1][:32]
        bar   = progress_bar(done, total)
        print(f"\r  {bar}  {DIM}{fname:<33}{RST}  "
              f"{G}dom:{_dom_total[0]}{RST}  {M}ip:{_ip_total[0]}{RST}   ",
              end="", flush=True)

    results, total = scrape_index(url, exts, limit, workers, progress_cb)
    print()  # newline after progress bar

    if not results:
        err("Aucun résultat.")
        return

    show_results(results, dout, iout)


# ── Mode 2 : URL directe ─────────────────────────────────────────────────────

def mode_url():
    sep()
    print(f"  {BOLD}{C}── URL directe ───────────────────────────{RST}\n")

    url = ask("URL cible")
    if url is None: return

    follow_str = ask("Suivre les liens internes ? (o/n)", "n")
    if follow_str is None: return
    follow = follow_str.lower() == "o"

    depth = CFG["depth"]
    if follow:
        d_str = ask("Profondeur", str(CFG["depth"]))
        if d_str is None: return
        try: depth = max(1, int(d_str))
        except ValueError: pass

    wrk_str = ask("Workers", str(CFG["workers"]))
    if wrk_str is None: return
    try: workers = max(1, min(50, int(wrk_str)))
    except ValueError: workers = CFG["workers"]

    dout, iout = ask_outpaths()
    if dout is None: return

    sep()
    info(f"Workers : {col(W, str(workers))}  |  Sortie : {col(C, dout)} / {col(C, iout)}")
    with Spinner(f"Scraping {url[:50]}…"):
        results = scrape_url(url, follow=follow, depth=depth, workers=workers)
    print()

    if not results:
        err("Aucun résultat.")
        return

    show_results(results, dout, iout)


# ── Mode 3 : BGP ─────────────────────────────────────────────────────────────

def mode_bgp():
    sep()
    print(f"  {BOLD}{C}── BGP.HE.NET ────────────────────────────{RST}\n")

    asn = ask("Numéro ASN (ex: 15169)")
    if asn is None: return

    dout, iout = ask_outpaths()
    if dout is None: return

    sep()
    with Spinner(f"Requête BGP AS{asn.upper().replace('AS','')}…"):
        results = scrape_bgp(asn)
    print()

    if not results:
        err("Aucun résultat.")
        return

    show_results(results, dout, iout)


# ── Mode 4 : RIPE ────────────────────────────────────────────────────────────

def mode_ripe():
    sep()
    print(f"  {BOLD}{C}── RIPE NCC ──────────────────────────────{RST}\n")

    query = ask("Recherche (org / IP / réseau)")
    if query is None: return

    dout, iout = ask_outpaths()
    if dout is None: return

    sep()
    with Spinner(f"Requête RIPE : {query}…"):
        results = scrape_ripe(query)
    print()

    if not results:
        err("Aucun résultat.")
        return

    show_results(results, dout, iout)


# ── Main ──────────────────────────────────────────────────────────────────────

MODES = {"1": mode_index, "2": mode_url, "3": mode_bgp, "4": mode_ripe}

def main():
    print_banner(animate=True)

    while True:
        print(MAIN_MENU)
        try:
            choice = input(f"  {BOLD}{W}zeuscraper{RST}{DIM}@zeu ~${RST} ").strip()
        except (KeyboardInterrupt, EOFError):
            choice = "0"

        if choice == "0":
            print(f"\n  {DIM}À bientôt.{RST}\n")
            break
        elif choice == "5":
            print_banner(animate=False)
            menu_params()
            print_banner(animate=False)
            continue
        elif choice in MODES:
            print_banner(animate=False)
            MODES[choice]()
            pause()
            print_banner(animate=False)
        else:
            err("Choix invalide.")
            time.sleep(0.5)


if __name__ == "__main__":
    main()
