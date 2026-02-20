import os
import sys
from pathlib import Path

# Ensure local imports work regardless of the runtime working directory
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["PYTHONUNBUFFERED"] = "1"

try:
    # Optional; requirements.txt should install it, but don't crash if the platform skips deps.
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    def load_dotenv(*_args, **_kwargs):
        return False

from tool.config import load_config
from tool.gamma import gamma_list_markets_for_series_in_window
from tool.clob_orders import place_dual_orders_for_market


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
    print("========================================\n")

    markets = gamma_list_markets_for_series_in_window(cfg)

    if markets:
        m0 = markets[0]
        print("[DEBUG first market]")
        print("DEBUG makerBaseFee:", m0.get("makerBaseFee"))
        print("DEBUG orderPriceMinTickSize:", m0.get("orderPriceMinTickSize"))
        print("DEBUG negRisk:", m0.get("negRisk"))
        print()

    if not markets:
        print("No encontré mercados en esa ventana.")
        return 0

    print(f"Encontrados {len(markets)} markets en ventana (cap MAX_MARKETS={cfg.max_markets}).\n")

    ok = 0
    fail = 0

    for m in markets:
        try:
            res = place_dual_orders_for_market(cfg, m)
            ok += 1
            print(f"[OK] {m.get('slug','?')} -> {res}\n")
        except Exception as e:
            fail += 1
            print(f"[FAIL] {m.get('slug','?')}: {e}\n")

    print("=== Resumen ===")
    print(f"OK: {ok}")
    print(f"FAIL: {fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
