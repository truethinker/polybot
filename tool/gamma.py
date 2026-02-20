import requests
from tool.config import Config


def _safe_json(resp: requests.Response):
    try:
        return resp.json()
    except Exception:
        raise RuntimeError(f"Gamma no devolvió JSON. Status={resp.status_code}, body={resp.text[:300]}")


def gamma_list_markets_for_series_in_window(cfg: Config) -> list[dict]:
    """
    Pide a Gamma SOLO markets cuyo startDate cae dentro de la ventana (UTC),
    ordenados por startDate asc. Luego filtra por prefix de slug (robusto).
    """
    url = f"{cfg.gamma_host.rstrip('/')}/markets"

    start_min = cfg.window_start_utc_iso()
    start_max = cfg.window_end_utc_iso()

    # robustez: evita 422 si están invertidos
    if start_max <= start_min:
        raise RuntimeError("Ventana UTC inválida (start_date_max <= start_date_min). Revisa WINDOW_START/WINDOW_END.")

    slug_prefix = "btc-updown-5m-"  # para esta serie

    out: list[dict] = []
    offset = 0
    limit = min(cfg.max_markets, 200)

    while True:
        params = {
            "limit": limit,
            "offset": offset,
            "order": "startDate",
            "ascending": "true",
            "closed": "false",
            "archived": "false",
            "start_date_min": start_min,
            "start_date_max": start_max,
        }

        r = requests.get(url, params=params, timeout=30)
        if r.status_code == 422:
            raise RuntimeError(f"Gamma 422: revisa ventana. url={r.url}")
        r.raise_for_status()

        page = _safe_json(r)
        if not isinstance(page, list):
            raise RuntimeError(f"Respuesta Gamma inesperada: {type(page)}")

        if page:
            first = page[0].get("startDate")
            last = page[-1].get("startDate")
            print(f"[Gamma page offset={offset}] first={first} last={last} count={len(page)}")
        else:
            print(f"[Gamma page offset={offset}] empty")

        for m in page:
            slug = str(m.get("slug", ""))
            if slug.startswith(slug_prefix):
                out.append(m)

        if len(page) < limit:
            break
        offset += limit

    return out
