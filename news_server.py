#!/usr/bin/env python3
import requests
import xml.etree.ElementTree as ET
import json
import time
import threading
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, unquote
import re

FEEDS = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cryptonews.com/news/feed/",
    "https://news.bitcoin.com/feed/",
]

news_cache = {"data": [], "time": 0}
CACHE_TTL = 300
cache_lock = threading.Lock()

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_FILE = os.path.join(THIS_DIR, "crypto-dashboard.html")

img_cache = {}
IMG_CACHE_TTL = 3600
cg_cache = {}
CG_CACHE_TTL = 120

# CoinGecko-to-CoinPaprika ID mapping
COIN_MAP = {
    "bitcoin": "btc-bitcoin",
    "ethereum": "eth-ethereum",
    "solana": "sol-solana",
    "ripple": "xrp-ripple",
    "cardano": "ada-cardano",
    "dogecoin": "doge-dogecoin",
    "polkadot": "dot-polkadot",
    "litecoin": "ltc-litecoin",
    "chainlink": "link-chainlink",
    "avalanche-2": "avax-avalanche-2",
    "polygon": "matic-polygon",
    "shiba-inu": "shib-shiba-inu",
    "tron": "trx-tron",
    "bitcoin-cash": "bch-bitcoin-cash",
    "stellar": "xlm-stellar",
    "uniswap": "uni-uniswap",
}
def pap_id(cg_id):
    return COIN_MAP.get(cg_id, cg_id)

def fetch_feed(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        r = requests.get(url, headers=headers, timeout=(5, 10), allow_redirects=True)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        items = []
        channel = root.find("channel")
        if channel is not None:
            for item in channel.findall("item"):
                img = ""
                mc = item.find("media:content", {"media": "http://search.yahoo.com/mrss/"})
                if mc is not None:
                    img = mc.get("url", "")
                if not img:
                    mt = item.find("media:thumbnail", {"media": "http://search.yahoo.com/mrss/"})
                    if mt is not None:
                        img = mt.get("url", "")
                if not img:
                    enc = item.find("enclosure")
                    if enc is not None and enc.get("type", "").startswith("image"):
                        img = enc.get("url", "")
                if not img:
                    m = item.findtext("description", "")
                    match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', m)
                    if match:
                        img = match.group(1)
                items.append({
                    "title": item.findtext("title", ""),
                    "url": item.findtext("link", ""),
                    "body": item.findtext("description", ""),
                    "published_on": item.findtext("pubDate", ""),
                    "source": item.findtext("source", "") or url.split("/")[2],
                    "image": img,
                })
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        if not items:
            for entry in root.findall("atom:entry", ns):
                link_el = entry.find("atom:link", ns)
                desc_el = entry.find("atom:content", ns)
                img = ""
                mc = entry.find("media:content", {"media": "http://search.yahoo.com/mrss/"})
                if mc is not None:
                    img = mc.get("url", "")
                if not img:
                    mt = entry.find("media:thumbnail", {"media": "http://search.yahoo.com/mrss/"})
                    if mt is not None:
                        img = mt.get("url", "")
                if not img and desc_el is not None:
                    match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', desc_el.text or "")
                    if match:
                        img = match.group(1)
                items.append({
                    "title": entry.findtext("atom:title", "", ns),
                    "url": link_el.get("href") if link_el is not None else "",
                    "body": (desc_el.text or "")[:500] if desc_el is not None else "",
                    "published_on": entry.findtext("atom:published", "", ns),
                    "source": entry.findtext("atom:author/atom:name", "", ns) or url.split("/")[2],
                    "image": img,
                })
        return items
    except Exception as e:
        print(f"Feed error {url}: {e}")
        return []

def parse_date(date_str):
    from datetime import datetime
    import email.utils
    try:
        if not date_str:
            return 0
        parsed = email.utils.parsedate_tz(date_str)
        if parsed:
            return int(time.mktime(parsed[:8]) - (parsed[8] or 0))
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except:
        return int(time.time())

def refresh_cache():
    global news_cache
    if not cache_lock.acquire(blocking=False):
        return
    try:
        if time.time() - news_cache["time"] < CACHE_TTL:
            return
        print("Refreshing news cache...")
        all_items = []
        seen = set()
        for url in FEEDS:
            for item in fetch_feed(url):
                key = item.get("title", "").strip().lower()
                if key and key not in seen:
                    seen.add(key)
                    item["published_on"] = parse_date(item.get("published_on", ""))
                    all_items.append(item)
        all_items.sort(key=lambda x: x.get("published_on", 0), reverse=True)
        news_cache = {"data": all_items, "time": time.time()}
        print(f"Cached {len(all_items)} news items")
    finally:
        cache_lock.release()

class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, status: int = 200, content_type: str = "text/html; charset=utf-8", extra_headers: dict = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            try:
                with open(HTML_FILE, "r", encoding="utf-8") as f:
                    body = f.read().encode("utf-8")
            except FileNotFoundError:
                body = b"HTML file not found"
            self._send(body)
            return

        # Image proxy
        if path == "/img":
            qs = parsed.query
            img_url = ""
            for part in qs.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    if k == "url":
                        img_url = unquote(v)
                        break
            if not img_url:
                self._send(b"missing url", status=400)
                return

            now = time.time()
            if img_url in img_cache and now - img_cache[img_url]["time"] < IMG_CACHE_TTL:
                c = img_cache[img_url]
                self._send(c["data"], content_type=c["type"], extra_headers={"Cache-Control": "max-age=3600", "Access-Control-Allow-Origin": "*"})
                return

            hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", "Referer": "https://www.cryptotracker.info/"}
            try:
                r = requests.get(img_url, headers=hdrs, timeout=10)
                r.raise_for_status()
                ctype = r.headers.get("Content-Type", "image/jpeg")
                img_cache[img_url] = {"data": r.content, "type": ctype, "time": now}
                self._send(r.content, content_type=ctype, extra_headers={"Cache-Control": "max-age=3600", "Access-Control-Allow-Origin": "*"})
            except Exception as e:
                print(f"Img proxy: {e}")
                hdrs2 = {"User-Agent": hdrs["User-Agent"], "Referer": img_url}
                try:
                    r2 = requests.get(img_url, headers=hdrs2, timeout=10)
                    r2.raise_for_status()
                    ctype = r2.headers.get("Content-Type", "image/jpeg")
                    self._send(r2.content, content_type=ctype, extra_headers={"Cache-Control": "max-age=3600", "Access-Control-Allow-Origin": "*"})
                except:
                    self.send_response(302)
                    self.send_header("Location", img_url)
                    self.end_headers()
            return

        # Crypto API proxy (CoinPaprika + alternative.me - avoids CoinGecko rate limits)
        if path.startswith("/api/proxy/"):
            target = path[11:]
            cache_key = f"{target}:{parsed.query}"
            now_cc = time.time()
            if cache_key in cg_cache and now_cc - cg_cache[cache_key]["time"] < CG_CACHE_TTL:
                self._send(cg_cache[cache_key]["data"], content_type="application/json", extra_headers={"Access-Control-Allow-Origin": "*"})
                return
            hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", "Accept": "application/json"}
            try:
                if target == "global":
                    r = requests.get("https://api.coinpaprika.com/v1/global", headers=hdrs, timeout=15)
                    r.raise_for_status()
                    g = r.json()
                    body = json.dumps({"data": {"total_market_cap": {"usd": g["market_cap_usd"]}, "total_volume": {"usd": g["volume_24h_usd"]}, "market_cap_percentage": {"btc": g["bitcoin_dominance_percentage"]}}}).encode()
                elif target == "markets":
                    ids_str = parsed.query.split("=")[1] if "=" in parsed.query else ""
                    ids_list = [x.strip() for x in ids_str.split(",") if x.strip()]
                    coins = []
                    for cid in ids_list:
                        pid = pap_id(cid)
                        try:
                            r = requests.get(f"https://api.coinpaprika.com/v1/tickers/{pid}", headers=hdrs, timeout=10)
                            r.raise_for_status()
                            t = r.json()
                            coins.append({
                                "id": cid, "symbol": t.get("symbol", "").lower(),
                                "name": t.get("name", ""),
                                "image": f"https://static.coinpaprika.com/coin/{t.get('id', cid)}/logo.png",
                                "current_price": t["quotes"]["USD"]["price"],
                                "price_change_percentage_24h": t["quotes"]["USD"]["percent_change_24h"],
                                "market_cap": t["quotes"]["USD"]["market_cap"],
                                "market_cap_rank": t.get("rank", 0)
                            })
                        except Exception:
                            pass
                    body = json.dumps(coins).encode()
                elif target == "ohlc":
                    q = dict(p.split("=") for p in parsed.query.split("&") if "=" in p)
                    cid = q.get("id", "bitcoin")
                    r = requests.get(f"https://api.coingecko.com/api/v3/coins/{cid}/ohlc?vs_currency=usd&days={q.get('days','7')}", headers=hdrs, timeout=15)
                    if r.status_code == 429:
                        time.sleep(2)
                        r = requests.get(r.url, headers=hdrs, timeout=15)
                    r.raise_for_status()
                    body = r.content
                elif target == "coin":
                    q = dict(p.split("=") for p in parsed.query.split("&") if "=" in p)
                    cid = pap_id(q.get("id", "bitcoin"))
                    r = requests.get(f"https://api.coinpaprika.com/v1/tickers/{cid}", headers=hdrs, timeout=15)
                    r.raise_for_status()
                    t = r.json()
                    u = t["quotes"]["USD"]
                    body = json.dumps({
                        "market_data": {
                            "current_price": {"usd": u["price"]},
                            "price_change_percentage_24h": u["percent_change_24h"],
                            "price_change_percentage_7d": u.get("percent_change_7d", 0),
                            "price_change_percentage_30d": u.get("percent_change_30d", 0),
                            "high_24h": {"usd": u.get("ath_price", u["price"])},
                            "low_24h": {"usd": 0},
                            "total_volume": {"usd": u["volume_24h"]},
                            "market_cap": {"usd": u["market_cap"]},
                            "ath": {"usd": u.get("ath_price", u["price"])}
                        },
                        "market_cap_rank": t.get("rank", 0)
                    }).encode()
                elif target == "fng":
                    r = requests.get("https://api.alternative.me/fng/?limit=1", headers=hdrs, timeout=10)
                    r.raise_for_status()
                    body = r.content
                else:
                    self._send(b'{"error":"unknown"}', content_type="application/json")
                    return
                cg_cache[cache_key] = {"data": body, "time": now_cc}
                self._send(body, content_type="application/json", extra_headers={"Access-Control-Allow-Origin": "*"})
            except Exception as e:
                print(f"Proxy {target}: {e}")
                self._send(json.dumps({"error": str(e)}).encode(), status=502, content_type="application/json")
            return

        # News & health API
        if time.time() - news_cache["time"] > CACHE_TTL:
            threading.Thread(target=refresh_cache, daemon=True).start()

        if path == "/api/news":
            data = news_cache["data"][:30]
        elif path == "/api/health":
            data = {"status": "ok", "cached": len(news_cache["data"]), "age_sec": int(time.time() - news_cache["time"])}
        else:
            data = {"error": "not found"}
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send(body, content_type="application/json; charset=utf-8", extra_headers={"Access-Control-Allow-Origin": "*"})

    def log_message(self, fmt, *args):
        pass

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 8765))
    print(f"Starting CryptoHub server on port {PORT}...")
    threading.Thread(target=refresh_cache, daemon=True).start()
    print(f"Server ready! Open http://localhost:{PORT}")
    from socketserver import ThreadingMixIn
    class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
        allow_reuse_address = True
    ThreadedHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
