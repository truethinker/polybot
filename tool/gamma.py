import requests
from datetime import datetime
import pytz

from tool.config import Config

def _safe_json(resp: requests.Response):
    try:
        return resp.json()
    except Exception:
        raise RuntimeError(f"Gamma no devolvió JSON. Status={resp.status_code}, body={resp.text[:300]}")

def gamma_list_markets_for_series_in_window(cfg: Config) -> list[dict]:
    """
    Busca markets de la serie (SERIES_SLUG) y filtra por startTime dentro de [WINDOW_START, WINDOW_END] (UTC).
    Gamma base: https://gamma-api.polymarket.com  [oai_citation:2‡Polymarket](https://docs.polymarket.com/developers/gamma-markets-api/gamma-structure?utm_source=chatgpt.com)
    """
    url = f"{cfg.gamma_host.rstrip('/')}/markets"
    params = {
        "limit": cfg.max_markets,
        "offset": 0,
        "active": True,
        "closed": False,
        "archived": False,
        "seriesSlug": cfg.series_slug,
        "enableOrderBook": True,
        "sortBy": "startTime",
        "sortDirection": "asc",
    }

    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    markets = _safe_json(r)
    if not isinstance(markets, list):
        raise RuntimeError(f"Respuesta Gamma inesperada: {type(markets)}")

    start_utc = datetime.fromisoformat(cfg.window_start_utc_iso().replace("Z", "+00:00"))
    end_utc = datetime.fromisoformat(cfg.window_end_utc_iso().replace("Z", "+00:00"))

    out = []
    for m in markets:
        # En estos markets suele venir eventStartTime o startTime (ISO Z).
        st = m.get("eventStartTime") or m.get("startTime") or m.get("startDate")
        if not st:
            continue

        try:
            st_dt = datetime.fromisoformat(st.replace("Z", "+00:00")).astimezone(pytz.UTC)
        except Exception:
            continue

        if start_utc <= st_dt <= end_utc:
            out.append(m)

    return out
