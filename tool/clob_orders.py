from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.constants import POLYGON

from tool.config import Config

def _parse_clob_token_ids(market: dict) -> list[str]:
    """
    Gamma devuelve clobTokenIds como string JSON tipo:
    "[\"tokenUp\",\"tokenDown\"]"
    o como lista ya parseada.
    """
    v = market.get("clobTokenIds")
    if v is None:
        raise RuntimeError("Market no trae clobTokenIds")

    if isinstance(v, list):
        return [str(x) for x in v]

    if isinstance(v, str):
        s = v.strip()
        if s.startswith("[") and s.endswith("]"):
            # parse minimal sin json lib extra
            import json
            arr = json.loads(s)
            if not isinstance(arr, list) or len(arr) < 2:
                raise RuntimeError("clobTokenIds no tiene 2 elementos")
            return [str(x) for x in arr]

    raise RuntimeError(f"Formato clobTokenIds inesperado: {type(v)}")

def _outcomes(market: dict) -> list[str]:
    v = market.get("outcomes")
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str):
        import json
        try:
            arr = json.loads(v)
            if isinstance(arr, list):
                return [str(x) for x in arr]
        except Exception:
            pass
    return ["Up", "Down"]

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds  # <-- importante

def _mk_client(cfg: Config) -> ClobClient:
    api_creds = ApiCreds(
        api_key=cfg.clob_api_key,
        api_secret=cfg.clob_api_secret,
        api_passphrase=cfg.clob_api_passphrase,
    )

    return ClobClient(
        host=cfg.clob_host.rstrip("/"),
        chain_id=cfg.chain_id,
        key=cfg.private_key,          # <-- el SDK actual usa 'key'
        creds=api_creds,              # <-- L2
        signature_type=cfg.signature_type,  # normalmente 1
        funder=cfg.funder_address,    # <-- TU address que paga colateral
    )

def place_dual_orders_for_market(cfg: Config, market: dict) -> dict[str, Any]:
    slug = market.get("slug", "?")
    if market.get("closed") is True:
        raise RuntimeError(f"Market cerrado: {slug}")
    if market.get("acceptingOrders") is False:
        raise RuntimeError(f"Market no acepta órdenes: {slug}")

    token_ids = _parse_clob_token_ids(market)
    token_up, token_down = token_ids[0], token_ids[1]

    if cfg.dry_run:
        return {
            "slug": slug,
            "dry_run": True,
            "up": {"token_id": token_up, "price": cfg.price_up, "size": cfg.size_up},
            "down": {"token_id": token_down, "price": cfg.price_down, "size": cfg.size_down},
        }

    client = _mk_client(cfg)

    # tick_size / neg_risk: puedes hardcodear, o leerlo del market si lo traes
    meta = {"tick_size": "0.01", "neg_risk": bool(market.get("negRisk", False))}

    up_resp = client.create_and_post_order(
        {"token_id": token_up, "price": cfg.price_up, "size": cfg.size_up, "side": "BUY"},
        meta,
    )

    down_resp = client.create_and_post_order(
        {"token_id": token_down, "price": cfg.price_down, "size": cfg.size_down, "side": "BUY"},
        meta,
    )

    return {"slug": slug, "up": up_resp, "down": down_resp}
