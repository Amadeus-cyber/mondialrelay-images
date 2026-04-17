#!/usr/bin/env python3
"""
Domain & IP Range Scraper
Usage: python scraper.py --url <url> [options]
       python scraper.py --source <bgp|ripe|arin|spamhaus> [--query <asn|org>]
"""

import re
import sys
import csv
import json
import time
import argparse
import ipaddress
from urllib.parse import urlparse, urljoin

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Missing deps. Run: pip install requests beautifulsoup4")
    sys.exit(1)

# ── Regex patterns ──────────────────────────────────────────────────────────

CIDR_PATTERN = re.compile(
    r'\b(?:\d{1,3}\.){3}\d{1,3}/\d{1,2}\b'           # IPv4 CIDR
    r'|'
    r'\b(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}/\d{1,3}\b'  # IPv6 CIDR
)

IPV4_PATTERN = re.compile(
    r'\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b'
)

DOMAIN_PATTERN = re.compile(
    r'\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)'
    r'+(?:com|net|org|io|fr|de|uk|ru|cn|info|biz|co|xyz|online|site|top|'
    r'cloud|tech|app|dev|edu|gov|mil|int|eu|us|ca|au|jp|br|in|nl|es|it|pl|'
    r'se|no|dk|fi|be|ch|at|cz|ro|hu|sk|bg|hr|si|lt|lv|ee|is|pt|gr|tr|il|'
    r'ua|by|kz|ge|am|az|md|rs|me|mk|al|ba|xk|ly|gg|je|im|ax)\b',
    re.IGNORECASE
)

# ── HTTP helpers ─────────────────────────────────────────────────────────────

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
})


def fetch(url: str, retries: int = 3, timeout: int = 15) -> requests.Response | None:
    for attempt in range(retries):
        try:
            r = SESSION.get(url, timeout=timeout)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            print(f"  [!] Attempt {attempt+1}/{retries} failed for {url}: {e}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return None


# ── Parsers ──────────────────────────────────────────────────────────────────

def extract_from_text(text: str) -> dict:
    cidrs = set(CIDR_PATTERN.findall(text))
    ips   = set(IPV4_PATTERN.findall(text)) - {ip for cidr in cidrs for ip in [cidr.split('/')[0]]}
    domains = set(DOMAIN_PATTERN.findall(text))

    # Validate CIDRs
    valid_cidrs = set()
    for cidr in cidrs:
        try:
            ipaddress.ip_network(cidr, strict=False)
            valid_cidrs.add(cidr)
        except ValueError:
            pass

    # Validate IPs
    valid_ips = set()
    for ip in ips:
        try:
            ipaddress.ip_address(ip)
            valid_ips.add(ip)
        except ValueError:
            pass

    return {"cidrs": valid_cidrs, "ips": valid_ips, "domains": domains}


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

        content_type = resp.headers.get("Content-Type", "")

        if "text/plain" in content_type or target_url.endswith(".txt"):
            data = extract_from_text(resp.text)
        else:
            soup = BeautifulSoup(resp.text, "html.parser")
            # Remove script/style noise
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
        time.sleep(0.5)

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
    url  = f"https://rest.db.ripe.net/search.json?query-string={query}&type-filter=inetnum,inet6num&flags=no-filtering"
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
                    # Convert inetnum range to CIDR
                    if " - " in val:
                        try:
                            start, end = val.split(" - ")
                            nets = list(ipaddress.summarize_address_range(
                                ipaddress.ip_address(start.strip()),
                                ipaddress.ip_address(end.strip())
                            ))
                            for net in nets:
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
        "drop":  "https://www.spamhaus.org/drop/drop.txt",
        "edrop": "https://www.spamhaus.org/drop/edrop.txt",
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
        data  = resp.json()
        return extract_from_text(json.dumps(data))
    except json.JSONDecodeError:
        return extract_from_text(resp.text)


SOURCES = {
    "bgp":      source_bgp_he,
    "ripe":     source_ripe,
    "arin":     source_arin,
    "spamhaus": source_spamhaus,
}


# ── Output ───────────────────────────────────────────────────────────────────

def save_results(results: dict, output: str, fmt: str):
    cidrs   = sorted(results.get("cidrs",   []), key=lambda x: ipaddress.ip_network(x, strict=False))
    ips     = sorted(results.get("ips",     []), key=lambda x: ipaddress.ip_address(x))
    domains = sorted(results.get("domains", []))

    if fmt == "txt":
        with open(output, "w") as f:
            if cidrs:
                f.write("# IP RANGES (CIDR)\n")
                f.write("\n".join(cidrs) + "\n\n")
            if ips:
                f.write("# INDIVIDUAL IPs\n")
                f.write("\n".join(ips) + "\n\n")
            if domains:
                f.write("# DOMAINS\n")
                f.write("\n".join(domains) + "\n")

    elif fmt == "csv":
        with open(output, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["type", "value"])
            for c in cidrs:
                w.writerow(["cidr", c])
            for i in ips:
                w.writerow(["ip", i])
            for d in domains:
                w.writerow(["domain", d])

    elif fmt == "json":
        with open(output, "w") as f:
            json.dump({
                "cidrs":   cidrs,
                "ips":     ips,
                "domains": domains,
            }, f, indent=2)

    print(f"\n  Saved to: {output}")


def print_summary(results: dict):
    cidrs   = results.get("cidrs",   set())
    ips     = results.get("ips",     set())
    domains = results.get("domains", set())
    print(f"\n{'─'*40}")
    print(f"  IP Ranges (CIDR) : {len(cidrs)}")
    print(f"  Individual IPs   : {len(ips)}")
    print(f"  Domains          : {len(domains)}")
    print(f"{'─'*40}")
    if cidrs:
        print("\n[CIDR]")
        for c in sorted(cidrs, key=lambda x: ipaddress.ip_network(x, strict=False))[:20]:
            print(f"  {c}")
        if len(cidrs) > 20:
            print(f"  ... ({len(cidrs)-20} more)")
    if domains:
        print("\n[Domains]")
        for d in sorted(domains)[:20]:
            print(f"  {d}")
        if len(domains) > 20:
            print(f"  ... ({len(domains)-20} more)")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Scrape domains and IP ranges from URLs or public sources.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Scrape a specific URL
  python scraper.py --url https://example.com/ip-list.txt

  # Scrape and follow internal links (depth 2)
  python scraper.py --url https://example.com --follow --depth 2

  # Use Hurricane Electric BGP Toolkit for an ASN
  python scraper.py --source bgp --query 15169

  # RIPE NCC query
  python scraper.py --source ripe --query "Cloudflare"

  # Spamhaus DROP list
  python scraper.py --source spamhaus --query drop

  # Save as JSON
  python scraper.py --url https://example.com -o results.json --format json
        """
    )
    parser.add_argument("--url",    help="Target URL to scrape")
    parser.add_argument("--source", choices=SOURCES.keys(), help="Built-in source")
    parser.add_argument("--query",  default="", help="Query string for built-in sources (ASN, org name, list name)")
    parser.add_argument("--follow", action="store_true", help="Follow internal links")
    parser.add_argument("--depth",  type=int, default=2, help="Link follow depth (default: 2)")
    parser.add_argument("-o", "--output", default="results.txt", help="Output file (default: results.txt)")
    parser.add_argument("--format", choices=["txt", "csv", "json"], default="txt", help="Output format")

    args = parser.parse_args()

    if not args.url and not args.source:
        parser.print_help()
        sys.exit(1)

    print(f"\nDomain & IP Range Scraper")
    print(f"{'─'*40}")

    results: dict = {"cidrs": set(), "ips": set(), "domains": set()}

    if args.url:
        print(f"  Mode  : URL scraping")
        print(f"  Target: {args.url}")
        data = scrape_url(args.url, follow_links=args.follow, depth=args.depth)
        results["cidrs"]   |= data.get("cidrs",   set())
        results["ips"]     |= data.get("ips",     set())
        results["domains"] |= data.get("domains", set())

    if args.source:
        print(f"  Mode  : Built-in source [{args.source}]")
        fn   = SOURCES[args.source]
        data = fn(args.query)
        results["cidrs"]   |= data.get("cidrs",   set())
        results["ips"]     |= data.get("ips",     set())
        results["domains"] |= data.get("domains", set())

    print_summary(results)
    save_results(results, args.output, args.format)


if __name__ == "__main__":
    main()
