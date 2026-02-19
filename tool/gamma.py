import requests
from datetime import datetime
import pytz

from tool.config import Config

def _safe_json(resp: requests.Response):
    try:
        return resp.json()
    except Exception:
        raise RuntimeError(f"Gamma no devolvió JSON. Status={resp.status_code}, body={resp.text[:300]}")

def _extract_slot_start_iso(m: dict) -> str | None:
    # 1) Preferido: startTime del evento (donde está el slot)
    evs = m.get("events")
    if isinstance(evs, list) and evs:
        ev0 = evs[0] if isinstance(evs[0], dict) else None
        if ev0:
            return ev0.get("startTime") or ev0.get("eventStartTime") or ev0.get("startDate")

    # 2) Fallbacks (a veces viene “plano”)
    return m.get("eventStartTime") or m.get("startTime") or m.get("startDate")

def gamma_list_markets_for_series_in_window(cfg: Config) -> list[dict]:
    """
    Trae markets de la serie y filtra por *slot start* dentro de la ventana.
    Ventana: cfg.window_start_local / cfg.window_end_local (Madrid) => se convierten a UTC.
    Gamma devuelve timestamps ISO con Z (UTC).
    """
    url = f"{cfg.gamma_host.rstrip('/')}/markets"

    start_utc = datetime.fromisoformat(cfg.window_start_utc_iso().replace("Z", "+00:00")).astimezone(pytz.UTC)
    end_utc = datetime.fromisoformat(cfg.window_end_utc_iso().replace("Z", "+00:00")).astimezone(pytz.UTC)

    out: list[dict] = []
    limit = min(100, cfg.max_markets)  # paginamos en bloques
    offset = 0

    while True:
        params = {
            "limit": limit,
            "offset": offset,
            # QUITA active / enableOrderBook
            "closed": "false",
            "archived": "false",
            "seriesSlug": cfg.series_slug,
            "sortBy": "startTime",
            "sortDirection": "asc",
        }

        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()

        markets = _safe_json(r)
        if markets:
            first_iso = _extract_slot_start_iso(markets[0])
            last_iso = _extract_slot_start_iso(markets[-1])
            print(f"[Gamma page offset={offset}] first={first_iso} last={last_iso} count={len(markets)}")
        if not isinstance(markets, list) or not markets:
            break

        # Procesamos este batch
        out = []
        for m in markets:
            st = _extract_slot_start_iso(m)
            if not st:
                continue
        
            try:
                st_dt = datetime.fromisoformat(st.replace("Z", "+00:00")).astimezone(pytz.UTC)
            except Exception:
                continue
        
            # Mejor: end exclusivo para ventanas [start, end)
            if start_utc <= st_dt < end_utc:
                slug = m.get("slug", "?")
                print(f"[MATCH] {slug} start={st}")
                out.append(m)

        offset += limit
        if offset >= cfg.max_markets:
            break

    return out
