#!/usr/bin/env python3
"""
Domain & IP Range Scraper
Usage: python scraper.py --url <url> [options]
       python scraper.py --index <url> [--ext txt,gz] [--limit N] [--workers N]
       python scraper.py --source <bgp|ripe|arin|spamhaus> [--query <asn|org>]
"""

import re
import sys
import gzip
import json
import time
import argparse
import ipaddress
import threading
from io import BytesIO
from urllib.parse import urlparse, urljoin

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Missing deps. Run: pip install requests beautifulsoup4")
    sys.exit(1)

# ── Regex patterns ───────────────────────────────────────────────────────────

CIDR_PATTERN = re.compile(
    r'\b(?:\d{1,3}\.){3}\d{1,3}/\d{1,2}\b'
    r'|'
    r'(?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}/\d{1,3}'
)

IPV4_PATTERN = re.compile(
    r'\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b'
)

DOMAIN_PATTERN = re.compile(
    r'\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)'
    r'+(?:com|net|org|io|fr|de|uk|ru|cn|info|biz|co|xyz|online|site|top|'
    r'cloud|tech|app|dev|edu|gov|mil|int|eu|us|ca|au|jp|br|in|nl|es|it|pl|'
    r'se|no|dk|fi|be|ch|at|cz|ro|hu|sk|bg|hr|si|lt|lv|ee|is|pt|gr|tr|il|'
    r'ua|by|kz|ge|am|az|md|rs|me|mk|al|ba|xk|ly|gg|je|im|ax|mobi|me|cc|'
    r'tv|pro|name|tel|xxx|ws|ms|nu|pw|academy|agency|blog|club|design|email|'
    r'global|group|host|link|live|media|news|network|one|plus|shop|social|'
    r'store|studio|support|systems|today|web|works|world|zone)\b',
    re.IGNORECASE
)

# ── HTTP helpers ─────────────────────────────────────────────────────────────

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
})


def fetch(url: str, retries: int = 3, timeout: int = 30, stream: bool = False) -> requests.Response | None:
    for attempt in range(retries):
        try:
            r = SESSION.get(url, timeout=timeout, stream=stream)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            print(f"  [!] Attempt {attempt+1}/{retries} failed for {url}: {e}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return None


# ── Parsers ──────────────────────────────────────────────────────────────────

def extract_from_text(text: str) -> dict:
    cidrs   = set(CIDR_PATTERN.findall(text))
    ips     = set(IPV4_PATTERN.findall(text)) - {c.split('/')[0] for c in cidrs}
    domains = set(DOMAIN_PATTERN.findall(text))

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


def decode_response(resp: requests.Response, url: str) -> str:
    """Decode response body, handling gzip content."""
    ct = resp.headers.get("Content-Type", "")
    if url.endswith(".gz") or "gzip" in ct:
        try:
            return gzip.decompress(resp.content).decode("utf-8", errors="replace")
        except Exception:
            pass
    return resp.text


# ── Apache index scraper ─────────────────────────────────────────────────────

def list_apache_index(index_url: str, exts: list[str]) -> list[str]:
    """Parse an Apache autoindex page and return all file URLs matching exts."""
    print(f"  Fetching index: {index_url}")
    resp = fetch(index_url)
    if not resp:
        return []

    soup  = BeautifulSoup(resp.text, "html.parser")
    base  = index_url.rstrip("/") + "/"
    files = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        # Skip Apache sort/parent links
        if href.startswith("?") or href.startswith("/") and href == urlparse(index_url).path:
            continue
        if href in ("../", "./", "/"):
            continue
        full = urljoin(base, href)
        # Only keep same-host files (not parent dir navigation)
        if urlparse(full).netloc != urlparse(index_url).netloc:
            continue
        ext = href.split("?")[0].rsplit(".", 1)[-1].lower()
        if ext in exts:
            files.append(full)

    print(f"  Found {len(files)} file(s) matching extensions: {exts}")
    return files


def scrape_index(index_url: str, exts: list[str], limit: int, workers: int) -> dict:
    """Download and parse all files from an Apache directory listing."""
    files = list_apache_index(index_url, exts)
    if limit:
        files = files[:limit]
        if limit < len(files):
            print(f"  Limiting to first {limit} files")

    results  = {"cidrs": set(), "ips": set(), "domains": set()}
    lock     = threading.Lock()
    done     = [0]
    total    = len(files)

    def process(url: str):
        resp = fetch(url, timeout=60)
        if not resp:
            return
        text = decode_response(resp, url)
        data = extract_from_text(text)
        with lock:
            results["cidrs"]   |= data["cidrs"]
            results["ips"]     |= data["ips"]
            results["domains"] |= data["domains"]
            done[0] += 1
            print(f"  [{done[0]}/{total}] {url.split('/')[-1]} "
                  f"— +{len(data['domains'])} domains, +{len(data['cidrs'])} CIDRs")

    threads = []
    sem     = threading.Semaphore(workers)

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


# ── Generic URL scraper ───────────────────────────────────────────────────────

def scrape_url(url: str, follow_links: bool = False, depth: int = 1) -> dict:
    results = {"cidrs": set(), "ips": set(), "domains": set()}
    visited = set()

    def _scrape(target_url: str, current_depth: int):
        if target_url in visited or current_depth > depth:
            return
        visited.add(target_url)
        print(f"  Fetching: {target_url}")
        resp = fetch(target_url)
        if not resp:
            return

        text = decode_response(resp, target_url)
        ct   = resp.headers.get("Content-Type", "")

        if "text/plain" in ct or target_url.endswith((".txt", ".gz", ".csv")):
            data = extract_from_text(text)
        else:
            soup = BeautifulSoup(text, "html.parser")
            for tag in soup(["script", "style"]):
                tag.decompose()
            data = extract_from_text(soup.get_text(separator="\n"))

            if follow_links and current_depth < depth:
                base = urlparse(target_url)
                for a in soup.find_all("a", href=True):
                    href = urljoin(target_url, a["href"])
                    parsed = urlparse(href)
                    if parsed.netloc == base.netloc:
                        _scrape(href, current_depth + 1)

        results["cidrs"]   |= data["cidrs"]
        results["ips"]     |= data["ips"]
        results["domains"] |= data["domains"]
        time.sleep(0.3)

    _scrape(url, 1)
    return results


# ── Built-in sources ─────────────────────────────────────────────────────────

def source_bgp_he(asn_or_query: str) -> dict:
    """Hurricane Electric BGP Toolkit — free, no auth required."""
    query = asn_or_query.upper().replace("AS", "")
    url   = f"https://bgp.he.net/AS{query}#_prefixes"
    print(f"  Source: BGP.HE.NET — AS{query}")
    resp  = fetch(url)
    if not resp:
        return {}
    soup  = BeautifulSoup(resp.text, "html.parser")
    cidrs = set()
    for row in soup.select("table#table_prefixes4 td a, table#table_prefixes6 td a"):
        text = row.get_text(strip=True)
        try:
            ipaddress.ip_network(text, strict=False)
            cidrs.add(text)
        except ValueError:
            pass
    return {"cidrs": cidrs, "ips": set(), "domains": set()}


def source_ripe(query: str) -> dict:
    """RIPE NCC REST API — public, no auth required."""
    print(f"  Source: RIPE NCC — query: {query}")
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
                            start, end = val.split(" - ")
                            for net in ipaddress.summarize_address_range(
                                ipaddress.ip_address(start.strip()),
                                ipaddress.ip_address(end.strip())
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
        return extract_from_text(resp.text)


def source_spamhaus(query: str) -> dict:
    """Spamhaus DROP/EDROP lists — public block lists."""
    urls = {
        "drop":    "https://www.spamhaus.org/drop/drop.txt",
        "edrop":   "https://www.spamhaus.org/drop/edrop.txt",
        "asndrop": "https://www.spamhaus.org/drop/asndrop.txt",
    }
    target = urls.get(query.lower(), urls["drop"])
    print(f"  Source: Spamhaus — {target}")
    resp = fetch(target)
    if not resp:
        return {}
    return extract_from_text(resp.text)


def source_arin(query: str) -> dict:
    """ARIN RDAP — public, no auth required."""
    print(f"  Source: ARIN RDAP — query: {query}")
    url  = f"https://rdap.arin.net/registry/entity/{query}"
    resp = fetch(url)
    if not resp:
        return {}
    try:
        return extract_from_text(json.dumps(resp.json()))
    except json.JSONDecodeError:
        return extract_from_text(resp.text)


SOURCES = {
    "bgp":      source_bgp_he,
    "ripe":     source_ripe,
    "arin":     source_arin,
    "spamhaus": source_spamhaus,
}


# ── Output ───────────────────────────────────────────────────────────────────

def save_results(results: dict, domains_out: str, ips_out: str):
    def sort_cidr(x):
        try:
            return ipaddress.ip_network(x, strict=False)
        except ValueError:
            return ipaddress.ip_network("0.0.0.0/32")

    all_ips = (
        sorted(results.get("cidrs", []), key=sort_cidr) +
        sorted(results.get("ips",   []), key=lambda x: ipaddress.ip_address(x))
    )
    domains = sorted(results.get("domains", []))

    with open(domains_out, "w") as f:
        f.write("\n".join(domains) + "\n")
    print(f"  Domains → {domains_out} ({len(domains)} entries)")

    with open(ips_out, "w") as f:
        f.write("\n".join(all_ips) + "\n")
    print(f"  IPs/CIDRs → {ips_out} ({len(all_ips)} entries)")


def print_summary(results: dict):
    cidrs   = results.get("cidrs",   set())
    ips     = results.get("ips",     set())
    domains = results.get("domains", set())
    print(f"\n{'─'*40}")
    print(f"  IP Ranges (CIDR) : {len(cidrs)}")
    print(f"  Individual IPs   : {len(ips)}")
    print(f"  Domains          : {len(domains)}")
    print(f"{'─'*40}")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Scrape domains & IP ranges from Apache indexes, URLs, or public sources.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Apache directory index (like Pulsedmedia) — main use case
  python scraper.py --index https://le6-1-103at400.pulsedmedia.com/public-indexx/latest/domainlists/public/?C=S;O=D

  # Limit to 10 files, 8 parallel workers, save as CSV
  python scraper.py --index <url> --limit 10 --workers 8 -o out.csv --format csv

  # Only download .txt files (exclude .gz)
  python scraper.py --index <url> --ext txt

  # Scrape a single URL
  python scraper.py --url https://example.com/list.txt

  # Follow internal links (depth 2)
  python scraper.py --url https://example.com --follow --depth 2

  # Built-in sources
  python scraper.py --source bgp --query 15169
  python scraper.py --source ripe --query "OVH"
  python scraper.py --source spamhaus --query drop
        """
    )

    parser.add_argument("--index",   help="Apache directory index URL to crawl all files")
    parser.add_argument("--url",     help="Single URL to scrape")
    parser.add_argument("--source",  choices=SOURCES.keys(), help="Built-in source")
    parser.add_argument("--query",   default="", help="Query for built-in sources")
    parser.add_argument("--ext",     default="txt,gz", help="File extensions to download from index (default: txt,gz)")
    parser.add_argument("--limit",   type=int, default=0, help="Max files to download from index (0 = all)")
    parser.add_argument("--workers", type=int, default=5, help="Parallel download threads (default: 5)")
    parser.add_argument("--follow",  action="store_true", help="Follow internal links (--url mode)")
    parser.add_argument("--depth",   type=int, default=2, help="Link follow depth (default: 2)")
    parser.add_argument("--domains-out", default="domains.txt", help="Output file for domains (default: domains.txt)")
    parser.add_argument("--ips-out",     default="ips.txt",     help="Output file for IPs/CIDRs (default: ips.txt)")

    args = parser.parse_args()

    if not args.index and not args.url and not args.source:
        parser.print_help()
        sys.exit(1)

    print(f"\nDomain & IP Range Scraper")
    print(f"{'─'*40}")

    results: dict = {"cidrs": set(), "ips": set(), "domains": set()}

    if args.index:
        print(f"  Mode    : Apache index crawl")
        print(f"  Target  : {args.index}")
        exts = [e.strip().lstrip(".").lower() for e in args.ext.split(",")]
        print(f"  Ext     : {exts}")
        print(f"  Workers : {args.workers}")
        data = scrape_index(args.index, exts, args.limit, args.workers)
        results["cidrs"]   |= data.get("cidrs",   set())
        results["ips"]     |= data.get("ips",     set())
        results["domains"] |= data.get("domains", set())

    if args.url:
        print(f"  Mode  : URL scraping")
        print(f"  Target: {args.url}")
        data = scrape_url(args.url, follow_links=args.follow, depth=args.depth)
        results["cidrs"]   |= data.get("cidrs",   set())
        results["ips"]     |= data.get("ips",     set())
        results["domains"] |= data.get("domains", set())

    if args.source:
        print(f"  Mode  : Built-in source [{args.source}]")
        data = SOURCES[args.source](args.query)
        results["cidrs"]   |= data.get("cidrs",   set())
        results["ips"]     |= data.get("ips",     set())
        results["domains"] |= data.get("domains", set())

    print_summary(results)
    save_results(results, args.domains_out, args.ips_out)


if __name__ == "__main__":
    main()
