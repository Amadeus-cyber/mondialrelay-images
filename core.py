#!/usr/bin/env python3
"""Shared scraping logic — used by both scraper.py (CLI) and bot.py (Telegram)."""

import re
import gzip
import json
import time
import ipaddress
import threading
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup

# ── Regex ─────────────────────────────────────────────────────────────────────

CIDR_RE = re.compile(
    r'\b(?:\d{1,3}\.){3}\d{1,3}/\d{1,2}\b'
    r'|(?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}/\d{1,3}'
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
        except requests.RequestException:
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


# ── Scrapers ──────────────────────────────────────────────────────────────────

def scrape_index(index_url: str, exts: list, limit: int, workers: int,
                 progress_cb=None, stop_event=None) -> tuple[dict, int]:
    """Scrape un index Apache. Retourne TOUJOURS (results, total)."""
    empty = ({"cidrs": set(), "ips": set(), "domains": set()}, 0)

    resp = fetch(index_url)
    if not resp:
        return empty

    soup  = BeautifulSoup(resp.text, "html.parser")
    base  = index_url.rstrip("/") + "/"
    files = []

    all_exts = not exts or exts == ["*"] or exts == ["all"]

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("?") or href in ("../", "./", "/"):
            continue
        full = urljoin(base, href)
        if urlparse(full).netloc != urlparse(index_url).netloc:
            continue
        name = href.split("?")[0]
        if name.endswith("/"):
            continue
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if all_exts or ext in exts:
            files.append(full)

    if limit and limit < len(files):
        files = files[:limit]

    results = {"cidrs": set(), "ips": set(), "domains": set()}
    lock    = threading.Lock()
    done    = [0]
    total   = len(files)

    if total == 0:
        return results, 0

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
            if progress_cb:
                try:
                    progress_cb(done[0], total, url, data)
                except Exception:
                    pass

    sem     = threading.Semaphore(workers)
    threads = []

    def worker(url):
        with sem:
            process(url)

    for url in files:
        if stop_event and stop_event.is_set():
            break
        t = threading.Thread(target=worker, args=(url,), daemon=True)
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    return results, total


def scrape_url(url: str, follow: bool = False, depth: int = 1,
               workers: int = 3) -> dict:
    """Scrape une URL. Si follow=True, suit les liens internes jusqu'à `depth`."""
    results = {"cidrs": set(), "ips": set(), "domains": set()}
    visited = set()
    lock    = threading.Lock()
    sem     = threading.Semaphore(workers)

    queue = [(url, 1)]
    qlock = threading.Lock()

    def crawl_one(target: str, d: int):
        with lock:
            if target in visited:
                return []
            visited.add(target)

        resp = fetch(target)
        if not resp:
            return []

        text = decode_response(resp, target)
        ct   = resp.headers.get("Content-Type", "")
        new_links = []

        if "text/plain" in ct or target.endswith((".txt", ".gz", ".csv", ".lst")):
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
                        new_links.append((href, d + 1))

        with lock:
            results["cidrs"]   |= data["cidrs"]
            results["ips"]     |= data["ips"]
            results["domains"] |= data["domains"]

        return new_links

    while queue:
        with qlock:
            batch, queue = queue, []

        threads = []
        next_batch_lock = threading.Lock()
        next_batch = []

        def worker(target, d):
            with sem:
                links = crawl_one(target, d)
                with next_batch_lock:
                    next_batch.extend(links)

        for target, d in batch:
            t = threading.Thread(target=worker, args=(target, d), daemon=True)
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        with qlock:
            queue.extend(next_batch)

    return results


def scrape_bgp(asn: str) -> dict:
    asn  = asn.upper().replace("AS", "")
    url  = f"https://bgp.he.net/AS{asn}#_prefixes"
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


# ── Format output ─────────────────────────────────────────────────────────────

def sort_cidr(x):
    try:
        return ipaddress.ip_network(x, strict=False)
    except ValueError:
        return ipaddress.ip_network("0.0.0.0/32")


def build_lists(results: dict) -> tuple[list, list]:
    all_ips = (
        sorted(results.get("cidrs", []), key=sort_cidr) +
        sorted(results.get("ips",   []), key=lambda x: ipaddress.ip_address(x))
    )
    domains = sorted(results.get("domains", []))
    return domains, all_ips


def resolve_path(directory: str, filename: str) -> str:
    """Combine un dossier de sortie et un nom de fichier."""
    import os
    if directory and directory not in (".", ""):
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, filename)
    return filename


def save_files(results: dict, dout: str, iout: str) -> tuple[int, int]:
    import os
    os.makedirs(os.path.dirname(os.path.abspath(dout)), exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(iout)), exist_ok=True)
    domains, all_ips = build_lists(results)
    with open(dout, "w") as f:
        f.write("\n".join(domains) + "\n")
    with open(iout, "w") as f:
        f.write("\n".join(all_ips) + "\n")
    return len(domains), len(all_ips)
