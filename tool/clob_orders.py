from __future__ import annotations
from typing import Any

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

from tool.config import Config

def _parse_clob_token_ids(market: dict) -> list[str]:
    v = market.get("clobTokenIds")
    if v is None:
        raise RuntimeError("Market no trae clobTokenIds")
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str):
        import json
        arr = json.loads(v)
        if not isinstance(arr, list) or len(arr) < 2:
            raise RuntimeError("clobTokenIds no tiene 2 elementos")
        return [str(x) for x in arr]
    raise RuntimeError(f"Formato clobTokenIds inesperado: {type(v)}")

def _mk_client(cfg: Config) -> ClobClient:
    client = ClobClient(
        cfg.clob_host.rstrip("/"),
        key=cfg.private_key,          # <- correcto (NO private_key=)
        chain_id=cfg.chain_id,
        signature_type=cfg.signature_type,  # 0 MetaMask/EOA, 1 email/Magic, 2 proxy  [oai_citation:4‡GitHub](https://github.com/Polymarket/py-clob-client)
        #funder=cfg.funder_address,    # <- address que paga colateral
    )
    from eth_account import Account
    Account.enable_unaudited_hdwallet_features()
    
    addr = Account.from_key(cfg.private_key).address
    print("SIGNER_ADDRESS:", addr)
    print("FUNDER_ADDRESS:", getattr(cfg, "funder_address", None))
    print("CHAIN_ID:", cfg.chain_id, "SIG_TYPE:", cfg.signature_type)
    # Si no quieres manejar CLOB_API_KEY/SECRET/PASSPHRASE a mano:
    # el SDK puede crearlos/derivarlos y setearlos.
    client.set_api_creds(client.create_or_derive_api_creds())  #  [oai_citation:5‡GitHub](https://github.com/Polymarket/py-clob-client)
    return client

def place_dual_orders_for_market(cfg: Config, market: dict) -> dict[str, Any]:
    slug = market.get("slug", "?")
    if market.get("closed") is True:
        raise RuntimeError(f"Market cerrado: {slug}")
    if market.get("acceptingOrders") is False:
        raise RuntimeError(f"Market no acepta órdenes: {slug}")

    token_up, token_down = _parse_clob_token_ids(market)

    if cfg.dry_run:
        return {
            "slug": slug,
            "dry_run": True,
            "up": {"token_id": token_up, "price": cfg.price_up, "size": cfg.size_up},
            "down": {"token_id": token_down, "price": cfg.price_down, "size": cfg.size_down},
        }

    client = _mk_client(cfg)

    up_order = OrderArgs(token_id=token_up, price=cfg.price_up, size=cfg.size_up, side=BUY)
    down_order = OrderArgs(token_id=token_down, price=cfg.price_down, size=cfg.size_down, side=BUY)

    signed_up = client.create_order(up_order)
    signed_down = client.create_order(down_order)

    up_resp = client.post_order(signed_up, OrderType.GTC)
    down_resp = client.post_order(signed_down, OrderType.GTC)

    return {"slug": slug, "up": up_resp, "down": down_resp}

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


