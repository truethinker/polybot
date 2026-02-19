from __future__ import annotations

from typing import Any

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType

from tool.config import Config


def _parse_clob_token_ids(market: dict) -> list[str]:
    v = market.get("clobTokenIds")
    if v is None:
        raise RuntimeError("Market no trae clobTokenIds")

    if isinstance(v, list):
        return [str(x) for x in v]

    if isinstance(v, str):
        s = v.strip()
        if s.startswith("[") and s.endswith("]"):
            import json
            arr = json.loads(s)
            if not isinstance(arr, list) or len(arr) < 2:
                raise RuntimeError("clobTokenIds no tiene 2 elementos")
            return [str(x) for x in arr]

    raise RuntimeError(f"Formato clobTokenIds inesperado: {type(v)}")


def _mk_client(cfg: Config) -> ClobClient:
    api_creds = ApiCreds(
        api_key=cfg.clob_api_key,
        api_secret=cfg.clob_api_secret,
        api_passphrase=cfg.clob_api_passphrase,
    )

    client = ClobClient(
        host=cfg.clob_host.rstrip("/"),
        chain_id=cfg.chain_id,
        key=cfg.private_key,              # SDK actual usa 'key'
        creds=api_creds,
        signature_type=cfg.signature_type,
        funder=cfg.funder_address,
    )

    return client


def _build_create_order_opts(tick_size: str, neg_risk: bool):
    """
    Algunas versiones del SDK aceptan CreateOrderOptions(...),
    otras aceptan un dict. Probamos ambas.
    """
    try:
        from py_clob_client.clob_types import CreateOrderOptions  # type: ignore
        return CreateOrderOptions(tick_size=tick_size, neg_risk=neg_risk)
    except Exception:
        return {"tick_size": tick_size, "neg_risk": neg_risk}


def _orderargs_compat(**kwargs) -> OrderArgs:
    """
    OrderArgs cambia entre versiones.
    Si fee_rate_bps no es aceptado, lo quitamos.
    """
    try:
        return OrderArgs(**kwargs)
    except TypeError as e:
        if "fee_rate_bps" in kwargs:
            kwargs.pop("fee_rate_bps", None)
            return OrderArgs(**kwargs)
        raise e


def _create_and_post(client: ClobClient, order: OrderArgs, order_type: OrderType, opts):
    """
    Compatibilidad entre versiones del SDK:
    - create_order(order, opts) y post_order(signed, order_type)
    - create_order(order, order_type, opts)
    - post_order(signed) sin order_type
    """
    # Variante A: create_order(order, opts)
    try:
        signed = client.create_order(order, opts)  # type: ignore
        try:
            return client.post_order(signed, order_type)  # type: ignore
        except TypeError:
            return client.post_order(signed)  # type: ignore
    except TypeError:
        pass

    # Variante B: create_order(order, order_type, opts)
    signed = client.create_order(order, order_type, opts)  # type: ignore
    try:
        return client.post_order(signed, order_type)  # type: ignore
    except TypeError:
        return client.post_order(signed)  # type: ignore


def place_dual_orders_for_market(cfg: Config, market: dict) -> dict[str, Any]:
    slug = market.get("slug", "?")

    if market.get("closed") is True:
        raise RuntimeError(f"Market cerrado: {slug}")
    if market.get("acceptingOrders") is False:
        raise RuntimeError(f"Market no acepta órdenes: {slug}")

    token_ids = _parse_clob_token_ids(market)
    token_up, token_down = token_ids[0], token_ids[1]

    tick_size = str(market.get("orderPriceMinTickSize", "0.01"))
    neg_risk = bool(market.get("negRisk", False))
    maker_fee_bps = int(market.get("makerBaseFee", 0))  # en tu debug: 1000

    meta = {
        "tick_size": tick_size,
        "neg_risk": neg_risk,
        "maker_fee_bps": maker_fee_bps,
    }

    if cfg.dry_run:
        return {
            "slug": slug,
            "dry_run": True,
            "meta": meta,
            "up": {"token_id": token_up, "price": cfg.price_up, "size": cfg.size_up},
            "down": {"token_id": token_down, "price": cfg.price_down, "size": cfg.size_down},
        }

    client = _mk_client(cfg)
    opts = _build_create_order_opts(tick_size=tick_size, neg_risk=neg_risk)

    # fee_rate_bps: solo mandarlo si es >0 (nunca 0)
    up_kwargs = dict(
        token_id=token_up,
        price=cfg.price_up,
        size=cfg.size_up,
        side="BUY",
    )
    down_kwargs = dict(
        token_id=token_down,
        price=cfg.price_down,
        size=cfg.size_down,
        side="BUY",
    )
    if maker_fee_bps > 0:
        up_kwargs["fee_rate_bps"] = maker_fee_bps
        down_kwargs["fee_rate_bps"] = maker_fee_bps

    up_order = _orderargs_compat(**up_kwargs)
    down_order = _orderargs_compat(**down_kwargs)

    up_resp = _create_and_post(client, up_order, OrderType.GTC, opts)
    down_resp = _create_and_post(client, down_order, OrderType.GTC, opts)

    return {
        "slug": slug,
        "meta": meta,
        "up": up_resp,
        "down": down_resp,
    }
