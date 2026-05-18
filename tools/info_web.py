"""tools/info_web.py — Obtiene info oficial de panypasta.cl (cache 24h)"""
import logging
import time
from urllib.request import urlopen, Request
from urllib.parse import urljoin, urlparse
from html.parser import HTMLParser

logger = logging.getLogger(__name__)
_cache: dict = {"ts": 0, "content": ""}
_TTL = 86400
BASE_URL = "https://www.panypasta.cl"

class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._skip = False
        self.texts = []
        self.links = []
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "nav", "footer", "head"):
            self._skip = True
        if tag == "a":
            href = dict(attrs).get("href", "")
            if href:
                self.links.append(href)
    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "footer", "head"):
            self._skip = False
    def handle_data(self, data):
        if not self._skip:
            t = data.strip()
            if len(t) > 3:
                self.texts.append(t)

def _fetch(url: str) -> tuple[str, list[str]]:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    html = urlopen(req, timeout=8).read().decode("utf-8", errors="ignore")
    p = _TextExtractor()
    p.feed(html)
    return p.texts, p.links

def _extract_text(texts: list[str], max_lines: int = 150) -> str:
    seen = set()
    lines = []
    for t in texts:
        if t not in seen and len(t) > 5:
            seen.add(t)
            lines.append(t)
            if len(lines) >= max_lines:
                break
    return "\n".join(lines)

def get_info_web() -> str:
    global _cache
    if time.time() - _cache["ts"] < _TTL and _cache["content"]:
        return _cache["content"]
    try:
        sections = []
        visited = set()
        base_domain = urlparse(BASE_URL).netloc

        # Pagina principal
        texts, links = _fetch(BASE_URL)
        sections.append(f"=== {BASE_URL} ===\n" + _extract_text(texts))
        visited.add(BASE_URL)

        # Subpaginas internas
        internal = set()
        for href in links:
            full = urljoin(BASE_URL, href)
            parsed = urlparse(full)
            if parsed.netloc == base_domain and parsed.scheme in ("http", "https"):
                clean = parsed.scheme + "://" + parsed.netloc + parsed.path.rstrip("/")
                if clean not in visited and not parsed.fragment:
                    internal.add(clean)

        for url in list(internal)[:20]:  # max 20 subpaginas
            try:
                visited.add(url)
                texts, _ = _fetch(url)
                sections.append(f"=== {url} ===\n" + _extract_text(texts, max_lines=80))
            except Exception:
                pass

        content = "\n\n".join(sections)
        _cache = {"ts": time.time(), "content": content}
        logger.info(f"[info_web] panypasta.cl scrapeado: {len(sections)} paginas")
        return content
    except Exception as e:
        logger.warning(f"[info_web] Error: {e}")
        return ""
