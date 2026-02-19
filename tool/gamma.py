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
    """
    Para mercados 5m, el slot suele estar en events[0].eventStartTime.
    Fallbacks por si cambia el shape.
    """
    # 1) Lo correcto en estos 5m:
    evs = m.get("events")
    if isinstance(evs, list) and evs:
        ev0 = evs[0] if isinstance(evs[0], dict) else None
        if ev0:
            st = ev0.get("eventStartTime") or ev0.get("startTime") or ev0.get("startDate")
            if st:
                return st

    # 2) Fallbacks (menos fiables para “slot”):
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
            "active": "true",
            "closed": "false",
            "archived": "false",
            "seriesSlug": cfg.series_slug,
            "enableOrderBook": "true",
            "sortBy": "startTime",
            "sortDirection": "asc",
        }

        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()

        markets = _safe_json(r)
        if not isinstance(markets, list) or not markets:
            break

        # Procesamos este batch
        for m in markets:
            st_iso = _extract_slot_start_iso(m)
            if not st_iso:
                continue

            try:
                st_dt = datetime.fromisoformat(st_iso.replace("Z", "+00:00")).astimezone(pytz.UTC)
            except Exception:
                continue

            # Como viene ordenado asc, podemos:
            if st_dt < start_utc:
                continue

            if st_dt > end_utc:
                # Si ya hemos empezado a recoger, podemos cortar
                return out

            out.append(m)

        offset += limit
        if offset >= cfg.max_markets:
            break

    return out
