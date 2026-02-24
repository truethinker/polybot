import os
os.environ["PYTHONUNBUFFERED"] = "1"

from datetime import datetime, timedelta, timezone
import time

from tool.config import load_config
from tool.gamma import gamma_list_markets_for_series_in_window
from tool.clob_orders import place_dual_orders_for_market


def _align_to_next_5m(dt_local: datetime) -> datetime:
    # Polymarket BTC up/down 5m suele estar alineado a múltiplos de 5 minutos.
    dt_local = dt_local.replace(second=0, microsecond=0)
    minute = dt_local.minute
    delta = (5 - (minute % 5)) % 5
    if delta == 0:
        delta = 5
    return dt_local + timedelta(minutes=delta)


def _run_trading_once(cfg):
    markets = gamma_list_markets_for_series_in_window(cfg)

    if not markets:
        print("No encontré mercados en esa ventana.", flush=True)
        return 0

    if markets:
        m0 = markets[0]
        print("[DEBUG first market]", flush=True)
        print("DEBUG startDate:", m0.get("startDate"), flush=True)
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


def _run_auto_mode(cfg):
    """Modo 0-interacción humana.

    - Cada 30' (o el intervalo que definas) planifica un *slot* futuro y mete órdenes.
      Ejemplo: si el plan corre a las 03:00, operará el slot 04:00 -> 04:30.
    - Cada Y minutos ejecuta redeem (si AUTO_REDEEM=true) para liberar colateral/ganancias.

    Env vars (defaults = lo que pides):
      AUTO_MODE=true
      AUTO_PLAN_INTERVAL_MINUTES=30
      AUTO_TARGET_OFFSET_MINUTES=60
      AUTO_SLOT_MINUTES=30
      AUTO_ORDERS_PER_PLAN=6

      AUTO_REDEEM=true
      AUTO_REDEEM_INTERVAL_MINUTES=10
      REDEEM_LOOKBACK_HOURS=24
    """
    def _ceil_to_interval(dt_local: datetime, interval_min: int) -> datetime:
        """Siguiente instante alineado a un múltiplo de interval_min (minutos), con segundos=0."""
        dt_local = dt_local.replace(second=0, microsecond=0)
        mod = dt_local.minute % interval_min
        if mod == 0:
            return dt_local
        return dt_local + timedelta(minutes=(interval_min - mod))

    auto_redeem = os.getenv("AUTO_REDEEM", "false").strip().lower() in ("1", "true", "yes", "y")
    lookback_h = int(os.getenv("REDEEM_LOOKBACK_HOURS", "24"))

    plan_every_min = int(os.getenv("AUTO_PLAN_INTERVAL_MINUTES", "30"))
    target_offset_min = int(os.getenv("AUTO_TARGET_OFFSET_MINUTES", "60"))
    slot_min = int(os.getenv("AUTO_SLOT_MINUTES", "30"))

    # Por compatibilidad con versiones anteriores, aceptamos AUTO_ORDERS_PER_TICK.
    orders_per_plan = int(os.getenv("AUTO_ORDERS_PER_PLAN", os.getenv("AUTO_ORDERS_PER_TICK", "6")))

    redeem_every_min = int(os.getenv("AUTO_REDEEM_INTERVAL_MINUTES", "10"))

    # Control simple de gasto por market (para la idea de "empiezo con 50€ y no meto más")
    # Nota: esto NO reemplaza un chequeo real de balance; es una barrera de seguridad.
    pair_budget = float(os.getenv("AUTO_PAIR_BUDGET_USDC", "50") or 50)
    est_cost = (cfg.price_up * cfg.size_up) + (cfg.price_down * cfg.size_down)
    if est_cost > pair_budget:
        print(
            f"[auto][WARN] coste estimado por market ({est_cost:.4f} USDC) > AUTO_PAIR_BUDGET_USDC ({pair_budget:.4f}). "
            "Reduciendo SIZE_* o sube el budget si es intencional.",
            flush=True,
        )

    placed_slugs: set[str] = set()
    last_redeem = 0.0

    # Base temporal del planner: igual que redeem -> WINDOW_END (UTC) convertido a local
    end_utc = datetime.fromisoformat(cfg.window_end_utc_iso().replace("Z", "+00:00"))
    base_local = end_utc.astimezone(cfg.tz)
    next_plan_run = _ceil_to_interval(base_local, plan_every_min)

    print("=== AUTO_MODE ENABLED ===", flush=True)
    print(
        f"plan_every={plan_every_min}m target_offset={target_offset_min}m slot={slot_min}m orders/plan={orders_per_plan} redeem_every={redeem_every_min}m lookback={lookback_h}h",
        flush=True,
    )

    while True:
        now_local = datetime.now(cfg.tz)

        # redeem periódico
        if auto_redeem and (time.time() - last_redeem) >= redeem_every_min * 60:
            try:
                print("[auto][redeem] START", flush=True)
                from tool.redeem import redeem_last_hours
                redeem_last_hours(cfg, lookback_h)
                print("[auto][redeem] END", flush=True)
            except Exception as e:
                print(f"[auto][redeem][FAIL] {e}", flush=True)
            last_redeem = time.time()

        # Planificación SOLO cuando toque (cada 30' alineado)
        if now_local >= next_plan_run:
        # cursor = next_plan_run, que avanza cada plan_every_min
            cursor_local = next_plan_run

            start_local = cursor_local + timedelta(minutes=target_offset_min)
            end_local = start_local + timedelta(minutes=slot_min)
            
            cfg.window_start_local = start_local.strftime("%Y-%m-%d %H:%M")
            cfg.window_end_local = end_local.strftime("%Y-%m-%d %H:%M")

            start_utc, end_utc = cfg.window_utc_range()
            print(
                f"[auto][plan @ {next_plan_run.strftime('%Y-%m-%d %H:%M')}] local {cfg.window_start_local} -> {cfg.window_end_local} | utc {start_utc.isoformat()} -> {end_utc.isoformat()}",
                flush=True,
            )

            try:
                markets = gamma_list_markets_for_series_in_window(cfg)
            except Exception as e:
                print(f"[auto][gamma][FAIL] {e}", flush=True)
                next_plan_run = next_plan_run + timedelta(minutes=plan_every_min)
                time.sleep(2)
                continue

            fresh = [m for m in markets if str(m.get("slug", "")) and str(m.get("slug")) not in placed_slugs]
            if not fresh:
                print("[auto] no hay markets nuevos en el slot.", flush=True)
            else:
                to_place = fresh[: max(0, orders_per_plan)]
                for m in to_place:
                    slug = str(m.get("slug", ""))
                    try:
                        if est_cost > pair_budget:
                            print(f"[auto][SKIP] {slug} (est_cost={est_cost:.4f} > budget={pair_budget:.4f})", flush=True)
                            continue
                        res = place_dual_orders_for_market(cfg, m)
                        placed_slugs.add(slug)
                        print(f"[auto][OK] {slug} -> {res}", flush=True)
                    except Exception as e:
                        print(f"[auto][FAIL] {slug}: {e}", flush=True)

            next_plan_run = next_plan_run + timedelta(minutes=plan_every_min)

        # Sleep corto: redeem se ejecuta por timer y la planificación por next_plan_run.
        time.sleep(2)
def main():
    print(">>> MAIN.PY LOADED: REDEEM CHECKPOINT v1 <<<", flush=True)
    
    cfg = load_config()

    auto_mode = os.getenv("AUTO_MODE", "false").strip().lower() in ("1", "true", "yes", "y")

    auto_redeem = os.getenv("AUTO_REDEEM", "false").strip().lower() in ("1", "true", "yes", "y")
    lookback_h = int(os.getenv("REDEEM_LOOKBACK_HOURS", "12"))

    print("=== Polymarket 5m BTC Slot Order Tool ===", flush=True)
    print(f"Series: {cfg.series_slug}", flush=True)
    start_utc, end_utc = cfg.window_utc_range()
    print(f"Timezone: {cfg.tz}", flush=True)
    print(f"Window (local): {cfg.window_start_local} -> {cfg.window_end_local}", flush=True)
    print(f"Window (UTC):   {start_utc.isoformat()} -> {end_utc.isoformat()}", flush=True)
    print(f"Orders: UP price={cfg.price_up} size={cfg.size_up} | DOWN price={cfg.price_down} size={cfg.size_down}", flush=True)
    print(f"DRY_RUN={cfg.dry_run}", flush=True)
    print(f"FUNDER_ADDRESS={cfg.funder_address}", flush=True)
    print(f"CHAIN_ID={cfg.chain_id} SIGNATURE_TYPE={cfg.signature_type}", flush=True)
    print(f"USE_DERIVED_CREDS={os.getenv('USE_DERIVED_CREDS','')}", flush=True)
    print(f"AUTO_MODE={auto_mode}", flush=True)
    print(f"AUTO_REDEEM={auto_redeem} REDEEM_LOOKBACK_HOURS={lookback_h}", flush=True)
    print("========================================\n", flush=True)

    if auto_mode:
        _run_auto_mode(cfg)
        return 0

    # 1) REDEEM SIEMPRE (aunque no haya markets en la ventana)
    if auto_redeem:
        try:
            print("[redeem] START", flush=True)
            from tool.redeem import redeem_last_hours
            redeem_last_hours(cfg, lookback_h)
            print("[redeem] END", flush=True)
        except Exception as e:
            print(f"[redeem][FAIL] {e}", flush=True)

    if cfg.auto_redeem:
        try:
            from tool.redeem import redeem_last_hours
            redeem_last_hours(cfg, cfg.redeem_lookback_hours)
        except Exception as e:
            print(f"[redeem][FAIL] {e}")
    # 2) TRADING (si hay markets)
    return _run_trading_once(cfg)

if __name__ == "__main__":
    main()
