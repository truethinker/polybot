import os
os.environ["PYTHONUNBUFFERED"] = "1"

from tool.config import load_config
from tool.gamma import gamma_list_markets_for_series_in_window
from tool.clob_orders import place_dual_orders_for_market

def main():
    print(">>> MAIN.PY LOADED: REDEEM CHECKPOINT v1 <<<", flush=True)

    cfg = load_config()

    auto_redeem = os.getenv("AUTO_REDEEM", "false").strip().lower() in ("1", "true", "yes", "y")
    lookback_h = int(os.getenv("REDEEM_LOOKBACK_HOURS", "12"))

    print("=== Polymarket 5m BTC Slot Order Tool ===", flush=True)
    print(f"Series: {cfg.series_slug}", flush=True)
    print(f"Window (Europe/Madrid): {cfg.window_start_local} -> {cfg.window_end_local}", flush=True)
    print(f"Orders: UP price={cfg.price_up} size={cfg.size_up} | DOWN price={cfg.price_down} size={cfg.size_down}", flush=True)
    print(f"DRY_RUN={cfg.dry_run}", flush=True)
    print(f"FUNDER_ADDRESS={cfg.funder_address}", flush=True)
    print(f"CHAIN_ID={cfg.chain_id} SIGNATURE_TYPE={cfg.signature_type}", flush=True)
    print(f"USE_DERIVED_CREDS={os.getenv('USE_DERIVED_CREDS','')}", flush=True)
    print(f"AUTO_REDEEM={auto_redeem} REDEEM_LOOKBACK_HOURS={lookback_h}", flush=True)
    print("========================================\n", flush=True)

    # 1) REDEEM SIEMPRE (aunque no haya markets en la ventana)
    if auto_redeem:
        try:
            print("[redeem] START", flush=True)
            from tool.redeem import redeem_last_hours
            redeem_last_hours(cfg, lookback_h)
            print("[redeem] END", flush=True)
        except Exception as e:
            print(f"[redeem][FAIL] {e}", flush=True)

    # 2) TRADING (si hay markets)
    markets = gamma_list_markets_for_series_in_window(cfg)

    if not markets:
        print("No encontré mercados en esa ventana.", flush=True)
        return 0

    if markets:
        m0 = markets[0]
        print("[DEBUG first market]", flush=True)
        print("DEBUG makerBaseFee:", m0.get("makerBaseFee"), flush=True)
        print("DEBUG orderPriceMinTickSize:", m0.get("orderPriceMinTickSize"), flush=True)
        print("DEBUG negRisk:", m0.get("negRisk"), flush=True)
        print("", flush=True)

    print(f"Encontrados {len(markets)} markets en ventana (cap MAX_MARKETS={cfg.max_markets}).\n", flush=True)

    ok = 0
    fail = 0

    for m in markets:
        try:
            res = place_dual_orders_for_market(cfg, m)
            ok += 1
            print(f"[OK] {m['slug']} -> {res}\n", flush=True)
        except Exception as e:
            fail += 1
            print(f"[FAIL] {m.get('slug','?')}: {e}\n", flush=True)

    print("=== Resumen ===", flush=True)
    print(f"OK: {ok}", flush=True)
    print(f"FAIL: {fail}", flush=True)
    return 0 if fail == 0 else 1

if __name__ == "__main__":
    raise SystemExit(main())
