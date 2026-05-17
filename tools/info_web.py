"""tools/info_web.py — Obtiene info oficial de panypasta.cl (cache 24h)"""
import logging
import time
from urllib.request import urlopen, Request
from html.parser import HTMLParser

logger = logging.getLogger(__name__)
_cache: dict = {"ts": 0, "content": ""}
_TTL = 86400  # 24 horas

class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._skip = False
        self.texts = []
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "nav", "footer", "head"):
            self._skip = True
    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "footer", "head"):
            self._skip = False
    def handle_data(self, data):
        if not self._skip:
            t = data.strip()
            if len(t) > 3:
                self.texts.append(t)

def get_info_web() -> str:
    global _cache
    if time.time() - _cache["ts"] < _TTL and _cache["content"]:
        return _cache["content"]
    try:
        req = Request("https://www.panypasta.cl", headers={"User-Agent": "Mozilla/5.0"})
        html = urlopen(req, timeout=8).read().decode("utf-8", errors="ignore")
        p = _TextExtractor()
        p.feed(html)
        lines = []
        seen = set()
        for t in p.texts:
            if t not in seen and len(t) > 5:
                seen.add(t)
                lines.append(t)
        content = "\n".join(lines[:200])
        _cache = {"ts": time.time(), "content": content}
        logger.info("[info_web] panypasta.cl cargado OK")
        return content
    except Exception as e:
        logger.warning(f"[info_web] Error obteniendo panypasta.cl: {e}")
        return ""
