from __future__ import annotations

from typing import Any, Optional

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, CreateOrderOptions
from py_clob_client.order_builder.constants import BUY

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
    """
    Importante:
    - En py_clob_client, el constructor usa key=... (no private_key=...)
    - Para operar (L2) necesitas set_api_creds(...). Lo más robusto es derivarlas con L1:
      client.create_or_derive_api_creds() y luego client.set_api_creds(...)
      (tal como en el README del repo).  [oai_citation:5‡GitHub](https://github.com/etiennedemers/clob_client)
    """
    client = ClobClient(
        host=cfg.clob_host.rstrip("/"),
        key=cfg.private_key,
        chain_id=cfg.chain_id,
        signature_type=cfg.signature_type,
        funder=cfg.funder_address,
    )

    # Si el usuario pasó creds en env, las usamos; si no, derivamos.
    if cfg.clob_api_key and cfg.clob_api_secret and cfg.clob_api_passphrase:
        client.set_api_creds(ApiCreds(
            api_key=cfg.clob_api_key,
            api_secret=cfg.clob_api_secret,
            api_passphrase=cfg.clob_api_passphrase,
        ))
        return client

    # Deriva L2 creds con L1 (private key) y setéalas en el cliente
    derived = client.create_or_derive_api_creds()
    client.set_api_creds(derived)
    return client


def place_dual_orders_for_market(cfg: Config, market: dict) -> dict[str, Any]:
    slug = market.get("slug", "?")

    if market.get("closed") is True:
        raise RuntimeError(f"Market cerrado: {slug}")
    if market.get("acceptingOrders") is False:
        raise RuntimeError(f"Market no acepta órdenes: {slug}")

    token_up, token_down = _parse_clob_token_ids(market)[0:2]

    # Metadata de mercado (útil para opts)
    tick_size = str(market.get("orderPriceMinTickSize", "0.01"))
    neg_risk = bool(market.get("negRisk", False))
    maker_fee_bps = int(market.get("makerBaseFee", 0))  # en tus logs: 1000

    if cfg.dry_run:
        return {
            "slug": slug,
            "dry_run": True,
            "meta": {"tick_size": tick_size, "neg_risk": neg_risk, "maker_fee_bps": maker_fee_bps},
            "up": {"token_id": token_up, "price": cfg.price_up, "size": cfg.size_up},
            "down": {"token_id": token_down, "price": cfg.price_down, "size": cfg.size_down},
        }

    client = _mk_client(cfg)

    # Opciones de creación (si el SDK las usa)
    opts = CreateOrderOptions(
        tick_size=tick_size,
        neg_risk=neg_risk,
    )

    def _mk_order(token_id: str, price: float, size: float) -> OrderArgs:
        # Algunos builds aceptan fee_rate_bps, otros no; lo manejamos abajo.
        try:
            return OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=BUY,
                fee_rate_bps=maker_fee_bps,
            )
        except TypeError:
            return OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=BUY,
            )

    up_order = _mk_order(token_up, cfg.price_up, cfg.size_up)
    down_order = _mk_order(token_down, cfg.price_down, cfg.size_down)

    # create_and_post_order es el camino más estable (ejemplo oficial del repo)  [oai_citation:6‡GitHub](https://github.com/etiennedemers/clob_client)
    # Distintas versiones aceptan (OrderArgs) o (OrderArgs, opts). Probamos ambas.
    def _post(order: OrderArgs) -> Any:
        try:
            return client.create_and_post_order(order, opts)
        except TypeError:
            return client.create_and_post_order(order)

    up_resp = _post(up_order)
    down_resp = _post(down_order)

    return {
        "slug": slug,
        "meta": {"tick_size": tick_size, "neg_risk": neg_risk, "maker_fee_bps": maker_fee_bps},
        "up": up_resp,
        "down": down_resp,
    }
