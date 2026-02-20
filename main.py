import os
from dotenv import load_dotenv

os.environ["PYTHONUNBUFFERED"] = "1"

from tool.config import load_config
from tool.gamma import gamma_list_markets_for_series_in_window
from tool.clob_orders import place_dual_orders_for_market


def _maybe_auto_redeem(cfg, market: dict):
    """
    AUTO_REDEEM: solo intenta si el market está cerrado y trae conditionId.
    OJO: redeem on-chain usa gas (no es el relayer gasless).
    """
    if not cfg.auto_redeem:
        return

    if not market.get("closed"):
        return

    condition_id = market.get("conditionId") or market.get("condition_id")
    if not condition_id:
        print("[AUTO_REDEEM] market cerrado pero no trae conditionId. No puedo redeem.")
        return

    try:
        from tool.redeem import redeem_positions
        res = redeem_positions(cfg, condition_id)
        print(f"[AUTO_REDEEM] OK conditionId={condition_id} tx={res.tx_hash}")
    except Exception as e:
        print(f"[AUTO_REDEEM] FAIL conditionId={condition_id}: {e}")


def main():
    load_dotenv()
    cfg = load_config()

    print("=== Polymarket 5m BTC Slot Order Tool ===")
    print(f"Series: {cfg.series_slug}")
    print(f"Window (Europe/Madrid): {cfg.window_start_local} -> {cfg.window_end_local}")
    print(f"Orders: UP price={cfg.price_up} size={cfg.size_up} | DOWN price={cfg.price_down} size={cfg.size_down}")
    print(f"DRY_RUN={cfg.dry_run}")
    print(f"FUNDER_ADDRESS={cfg.funder_address}")
    print(f"CHAIN_ID={cfg.chain_id} SIGNATURE_TYPE={cfg.signature_type}")
    print(f"USE_DERIVED_CREDS={cfg.use_derived_creds}")
    print(f"AUTO_REDEEM={cfg.auto_redeem}")
    print("========================================\n")

    markets = gamma_list_markets_for_series_in_window(cfg)
    if not markets:
        print("No encontré mercados en esa ventana.")
        return 0

    m0 = markets[0]
    print("[DEBUG first market]")
    print("DEBUG makerBaseFee:", m0.get("makerBaseFee"))
    print("DEBUG orderPriceMinTickSize:", m0.get("orderPriceMinTickSize"))
    print("DEBUG negRisk:", m0.get("negRisk"))
    print()

    print(f"Encontrados {len(markets)} markets en ventana (cap MAX_MARKETS={cfg.max_markets}).\n")

    ok = 0
    fail = 0

    for m in markets:
        try:
            res = place_dual_orders_for_market(cfg, m)
            ok += 1
            print(f"[OK] {m.get('slug','?')} -> {res}\n")

            # Si quieres redeem automático, esto lo intentará cuando el market esté cerrado.
            _maybe_auto_redeem(cfg, m)

        except Exception as e:
            fail += 1
            print(f"[FAIL] {m.get('slug','?')}: {e}\n")

    print("=== Resumen ===")
    print(f"OK: {ok}")
    print(f"FAIL: {fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
