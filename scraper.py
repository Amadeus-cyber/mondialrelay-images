#!/usr/bin/env python3

import re
import sys
import os
import gzip
import json
import time
import ipaddress
import threading
from urllib.parse import urlparse, urljoin

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Missing deps. Run: pip install requests beautifulsoup4")
    sys.exit(1)

# ── ANSI colors ───────────────────────────────────────────────────────────────

R  = "\033[91m"
G  = "\033[92m"
Y  = "\033[93m"
B  = "\033[94m"
M  = "\033[95m"
C  = "\033[96m"
W  = "\033[97m"
DIM = "\033[2m"
RST = "\033[0m"
BOLD = "\033[1m"

def c(color, text): return f"{color}{text}{RST}"

# ── Banner ────────────────────────────────────────────────────────────────────

BANNER = f"""
{R}██████╗ {Y}██████╗ {G} ██████╗{C}██╗  ██╗{M}███████╗{RST}
{R}╚════██╗{Y}╚════██╗{G}██╔════╝{C}██║ ██╔╝{M}██╔════╝{RST}
{R} █████╔╝{Y} █████╔╝{G}╚█████╗ {C}█████╔╝ {M}█████╗  {RST}
{R}██╔═══╝ {Y}╚════██╗{G} ╚═══██╗{C}██╔═██╗ {M}██╔══╝  {RST}
{R}███████╗{Y}██████╔╝{G}██████╔╝{C}██║  ██╗{M}███████╗{RST}
{R}╚══════╝{Y}╚═════╝ {G}╚═════╝ {C}╚═╝  ╚═╝{M}╚══════╝{RST}

{DIM}        Domain & IP Range Scraper{RST}
{DIM}        by ZEU  •  v2.0{RST}
"""

MENU = f"""
{BOLD}{W}  ╔══════════════════════════════════════╗{RST}
{BOLD}{W}  ║          {C}CHOISIR UN MODE{W}             ║{RST}
{BOLD}{W}  ╠══════════════════════════════════════╣{RST}
{BOLD}{W}  ║  {G}[1]{W} Index Apache   {DIM}(répertoire){W}      ║{RST}
{BOLD}{W}  ║  {G}[2]{W} URL directe    {DIM}(page / fichier){W}  ║{RST}
{BOLD}{W}  ║  {G}[3]{W} Source BGP     {DIM}(ASN → CIDRs){W}    ║{RST}
{BOLD}{W}  ║  {G}[4]{W} Source RIPE    {DIM}(org → CIDRs){W}    ║{RST}
{BOLD}{W}  ║  {R}[0]{W} Quitter                          ║{RST}
{BOLD}{W}  ╚══════════════════════════════════════╝{RST}
"""

# ── Regex patterns ────────────────────────────────────────────────────────────

CIDR_RE = re.compile(
    r'\b(?:\d{1,3}\.){3}\d{1,3}/\d{1,2}\b'
    r'|'
    r'(?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}/\d{1,3}'
)

IPV4_RE = re.compile(
    r'\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b'
)

DOMAIN_RE = re.compile(
    r'\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)'
    r'+(?:com|net|org|io|fr|de|uk|ru|cn|info|biz|co|xyz|online|site|top|'
    r'cloud|tech|app|dev|edu|gov|mil|int|eu|us|ca|au|jp|br|in|nl|es|it|pl|'
    r'se|no|dk|fi|be|ch|at|cz|ro|hu|sk|bg|hr|si|lt|lv|ee|is|pt|gr|tr|il|'
    r'ua|by|kz|ge|am|az|md|rs|me|mk|al|ba|xk|ly|gg|je|im|ax|mobi|cc|'
    r'tv|pro|name|ws|ms|nu|pw|academy|agency|blog|club|design|email|'
    r'global|group|host|link|live|media|news|network|one|plus|shop|social|'
    r'store|studio|support|systems|today|web|works|world|zone)\b',
    re.IGNORECASE
)

# ── HTTP ──────────────────────────────────────────────────────────────────────

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
})


def fetch(url: str, retries: int = 3, timeout: int = 30) -> requests.Response | None:
    for attempt in range(retries):
        try:
            r = SESSION.get(url, timeout=timeout)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            info(f"Tentative {attempt+1}/{retries} échouée : {e}", R)
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return None


def decode_response(resp: requests.Response, url: str) -> str:
    if url.endswith(".gz") or "gzip" in resp.headers.get("Content-Type", ""):
        try:
            return gzip.decompress(resp.content).decode("utf-8", errors="replace")
        except Exception:
            pass
    return resp.text


# ── Extraction ────────────────────────────────────────────────────────────────

def extract(text: str) -> dict:
    cidrs   = set(CIDR_RE.findall(text))
    ips     = set(IPV4_RE.findall(text)) - {c.split('/')[0] for c in cidrs}
    domains = set(DOMAIN_RE.findall(text))

    valid_cidrs = set()
    for cidr in cidrs:
        try:
            ipaddress.ip_network(cidr, strict=False)
            valid_cidrs.add(cidr)
        except ValueError:
            pass

    valid_ips = set()
    for ip in ips:
        try:
            ipaddress.ip_address(ip)
            valid_ips.add(ip)
        except ValueError:
            pass

    return {"cidrs": valid_cidrs, "ips": valid_ips, "domains": domains}


# ── UI helpers ────────────────────────────────────────────────────────────────

def info(msg, color=C):
    print(f"  {color}>{RST} {msg}")

def ok(msg):
    print(f"  {G}✔{RST} {msg}")

def err(msg):
    print(f"  {R}✘{RST} {msg}")

def ask(prompt, default=""):
    val = input(f"  {Y}?{RST} {prompt}{DIM}{'['+default+'] ' if default else ''}{RST}: ").strip()
    return val if val else default

def separator():
    print(f"\n  {DIM}{'─'*44}{RST}\n")


# ── Modes ─────────────────────────────────────────────────────────────────────

def scrape_index(index_url: str, exts: list, limit: int, workers: int) -> dict:
    info(f"Récupération de l'index : {c(C, index_url)}")
    resp = fetch(index_url)
    if not resp:
        err("Impossible d'accéder à l'index.")
        return {}

    soup  = BeautifulSoup(resp.text, "html.parser")
    base  = index_url.rstrip("/") + "/"
    files = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("?") or href in ("../", "./", "/"):
            continue
        full = urljoin(base, href)
        if urlparse(full).netloc != urlparse(index_url).netloc:
            continue
        ext = href.split("?")[0].rsplit(".", 1)[-1].lower()
        if ext in exts:
            files.append(full)

    ok(f"{len(files)} fichier(s) trouvé(s) avec extensions {exts}")

    if limit and limit < len(files):
        files = files[:limit]
        info(f"Limité à {limit} fichiers")

    results = {"cidrs": set(), "ips": set(), "domains": set()}
    lock    = threading.Lock()
    done    = [0]
    total   = len(files)

    def process(url):
        r = fetch(url, timeout=60)
        if not r:
            return
        data = extract(decode_response(r, url))
        with lock:
            results["cidrs"]   |= data["cidrs"]
            results["ips"]     |= data["ips"]
            results["domains"] |= data["domains"]
            done[0] += 1
            fname = url.split("/")[-1][:35]
            print(f"  {G}[{done[0]:>3}/{total}]{RST} {fname:<36} "
                  f"{C}+{len(data['domains'])} dom{RST}  "
                  f"{M}+{len(data['cidrs'])} cidr{RST}")

    sem     = threading.Semaphore(workers)
    threads = []

    def worker(url):
        with sem:
            process(url)

    for url in files:
        t = threading.Thread(target=worker, args=(url,), daemon=True)
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    return results


def scrape_url(url: str, follow: bool, depth: int) -> dict:
    results = {"cidrs": set(), "ips": set(), "domains": set()}
    visited = set()

    def _scrape(target: str, d: int):
        if target in visited or d > depth:
            return
        visited.add(target)
        info(f"Fetch : {c(C, target)}")
        resp = fetch(target)
        if not resp:
            return
        text = decode_response(resp, target)
        ct   = resp.headers.get("Content-Type", "")
        if "text/plain" in ct or target.endswith((".txt", ".gz", ".csv")):
            data = extract(text)
        else:
            soup = BeautifulSoup(text, "html.parser")
            for tag in soup(["script", "style"]):
                tag.decompose()
            data = extract(soup.get_text(separator="\n"))
            if follow and d < depth:
                base = urlparse(target)
                for a in soup.find_all("a", href=True):
                    href = urljoin(target, a["href"])
                    if urlparse(href).netloc == base.netloc:
                        _scrape(href, d + 1)
        results["cidrs"]   |= data["cidrs"]
        results["ips"]     |= data["ips"]
        results["domains"] |= data["domains"]
        time.sleep(0.3)

    _scrape(url, 1)
    return results


def scrape_bgp(asn: str) -> dict:
    asn  = asn.upper().replace("AS", "")
    url  = f"https://bgp.he.net/AS{asn}#_prefixes"
    info(f"BGP.HE.NET — AS{asn}")
    resp = fetch(url)
    if not resp:
        return {}
    soup  = BeautifulSoup(resp.text, "html.parser")
    cidrs = set()
    for row in soup.select("table#table_prefixes4 td a, table#table_prefixes6 td a"):
        t = row.get_text(strip=True)
        try:
            ipaddress.ip_network(t, strict=False)
            cidrs.add(t)
        except ValueError:
            pass
    return {"cidrs": cidrs, "ips": set(), "domains": set()}


def scrape_ripe(query: str) -> dict:
    info(f"RIPE NCC — {query}")
    url  = (f"https://rest.db.ripe.net/search.json?query-string={query}"
            f"&type-filter=inetnum,inet6num&flags=no-filtering")
    resp = fetch(url)
    if not resp:
        return {}
    try:
        data  = resp.json()
        cidrs = set()
        for obj in data.get("objects", {}).get("object", []):
            for attr in obj.get("attributes", {}).get("attribute", []):
                if attr.get("name") in ("inetnum", "inet6num"):
                    val = attr.get("value", "")
                    if " - " in val:
                        try:
                            s, e = val.split(" - ")
                            for net in ipaddress.summarize_address_range(
                                ipaddress.ip_address(s.strip()),
                                ipaddress.ip_address(e.strip())
                            ):
                                cidrs.add(str(net))
                        except Exception:
                            pass
                    else:
                        try:
                            ipaddress.ip_network(val, strict=False)
                            cidrs.add(val)
                        except ValueError:
                            pass
        return {"cidrs": cidrs, "ips": set(), "domains": set()}
    except (json.JSONDecodeError, KeyError):
        return extract(resp.text)


# ── Save ──────────────────────────────────────────────────────────────────────

def save(results: dict, dout: str, iout: str):
    def s_cidr(x):
        try:
            return ipaddress.ip_network(x, strict=False)
        except ValueError:
            return ipaddress.ip_network("0.0.0.0/32")

    all_ips = (
        sorted(results.get("cidrs", []), key=s_cidr) +
        sorted(results.get("ips",   []), key=lambda x: ipaddress.ip_address(x))
    )
    domains = sorted(results.get("domains", []))

    with open(dout, "w") as f:
        f.write("\n".join(domains) + "\n")
    with open(iout, "w") as f:
        f.write("\n".join(all_ips) + "\n")

    separator()
    ok(f"Domaines  → {c(G, dout)}  ({c(W, str(len(domains)))} entrées)")
    ok(f"IPs/CIDRs → {c(G, iout)}  ({c(W, str(len(all_ips)))} entrées)")


# ── Main interactive loop ─────────────────────────────────────────────────────

def main():
    os.system("clear" if os.name != "nt" else "cls")
    print(BANNER)

    while True:
        print(MENU)
        choice = input(f"  {BOLD}{W}zeu@scraper{RST}{DIM}~${RST} ").strip()

        if choice == "0":
            print(f"\n  {DIM}Bye.{RST}\n")
            break

        separator()

        # ── Mode 1 : Apache index ──────────────────────────────────────────
        if choice == "1":
            url     = ask("URL de l'index Apache")
            exts    = ask("Extensions à télécharger", "txt,gz")
            limit_s = ask("Limite de fichiers (0 = tous)", "0")
            workers = ask("Workers parallèles", "5")
            dout    = ask("Fichier de sortie domaines", "domains.txt")
            iout    = ask("Fichier de sortie IPs/CIDRs", "ips.txt")

            if not url:
                err("URL vide.")
                continue

            exts_list = [e.strip().lstrip(".").lower() for e in exts.split(",")]
            results = scrape_index(url, exts_list, int(limit_s), int(workers))
            save(results, dout, iout)

        # ── Mode 2 : URL directe ──────────────────────────────────────────
        elif choice == "2":
            url    = ask("URL cible")
            follow = ask("Suivre les liens internes ? (o/n)", "n").lower() == "o"
            depth  = int(ask("Profondeur", "2")) if follow else 1
            dout   = ask("Fichier de sortie domaines", "domains.txt")
            iout   = ask("Fichier de sortie IPs/CIDRs", "ips.txt")

            if not url:
                err("URL vide.")
                continue

            results = scrape_url(url, follow, depth)
            save(results, dout, iout)

        # ── Mode 3 : BGP ──────────────────────────────────────────────────
        elif choice == "3":
            asn  = ask("Numéro ASN (ex: 15169 ou AS15169)")
            dout = ask("Fichier de sortie domaines", "domains.txt")
            iout = ask("Fichier de sortie IPs/CIDRs", "ips.txt")

            if not asn:
                err("ASN vide.")
                continue

            results = scrape_bgp(asn)
            save(results, dout, iout)

        # ── Mode 4 : RIPE ─────────────────────────────────────────────────
        elif choice == "4":
            query = ask("Recherche RIPE (org / IP / réseau)")
            dout  = ask("Fichier de sortie domaines", "domains.txt")
            iout  = ask("Fichier de sortie IPs/CIDRs", "ips.txt")

            if not query:
                err("Requête vide.")
                continue

            results = scrape_ripe(query)
            save(results, dout, iout)

        else:
            err("Choix invalide.")

        input(f"\n  {DIM}[Entrée pour continuer...]{RST}")
        os.system("clear" if os.name != "nt" else "cls")
        print(BANNER)


if __name__ == "__main__":
    main()
