import time
_HITS = {}
def allow(ip: str, limit: int = 3, window_sec: int = 60) -> bool:
    now = time.time()
    hits = [t for t in _HITS.get(ip, []) if now - t < window_sec]
    if len(hits) >= limit:
        _HITS[ip] = hits; return False
    hits.append(now); _HITS[ip] = hits; return True
