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
    Pide a Gamma SOLO markets cuyo startDate cae dentro de la ventana (UTC),
    ordenados por startDate asc. Luego filtra por la serie mediante slug prefix.
    """
    url = f"{cfg.gamma_host.rstrip('/')}/markets"

    # Ventana local (Madrid) -> UTC ISO Z (lo hace tu Config)
    start_min = cfg.window_start_utc_iso()
    start_max = cfg.window_end_utc_iso()

    # Para la serie BTC 5m, el slug de market suele empezar por "btc-updown-5m-"
    # (tu ejemplo: slug="btc-updown-5m-1771407900")
    slug_prefix = "btc-updown-5m-"

    out = []
    offset = 0
    limit = min(cfg.max_markets, 200)  # 100/200 según permita Gamma

    while True:
        params = {
            "limit": limit,
            "offset": offset,

            # OJO: estos son los nombres que entiende Gamma:
            "order": "startDate",
            "ascending": "true",
            "closed": "false",
            "archived": "false",

            # Filtrado server-side por fecha:
            "start_date_min": start_min,
            "start_date_max": start_max,
        }

        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        page = _safe_json(r)

        if not isinstance(page, list):
            raise RuntimeError(f"Respuesta Gamma inesperada: {type(page)}")

        # Debug útil
        if page:
            first = page[0].get("startDate")
            last = page[-1].get("startDate")
            print(f"[Gamma page offset={offset}] first={first} last={last} count={len(page)}")
        else:
            print(f"[Gamma page offset={offset}] empty")

        # Filtra por la serie via slug prefix (robusto aunque Gamma no tenga seriesSlug como param)
        for m in page:
            slug = str(m.get("slug", ""))
            if slug.startswith(slug_prefix):
                out.append(m)

        # Paginación
        if len(page) < limit:
            break
        offset += limit

    return out
