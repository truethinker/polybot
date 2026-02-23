import os
os.environ["PYTHONUNBUFFERED"] = "1"

from datetime import datetime, timedelta
import time

from tool.config import load_config
from tool.gamma import gamma_list_markets_for_series_in_window, gamma_get_markets_by_slugs
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
    """Modo determinista anclado a WINDOW_START/WINDOW_END (0 interacción humana).

    Requisito (tal como lo pides):
    - El *timeline* empieza en WINDOW_START (local) y avanza en pasos de 30 minutos.
    - En cada tick (p.ej. 03:00), coloca órdenes para el slot [03:30, 04:00] (lead=30min, slot=30min).
      Ejemplos:
        03:00 -> 03:30-04:00
        04:00 -> 04:30-05:00
        04:30 -> 05:00-05:30
    - AUTO_REDEEM (si está activado) se ejecuta cada 30 minutos (por defecto) con lookback de hasta 24h.

    Env vars (defaults = lo que pides):
      AUTO_MODE=true
      AUTO_TICK_MINUTES=30
      AUTO_LEAD_MINUTES=30
      AUTO_SLOT_MINUTES=30
      AUTO_ORDERS_PER_PLAN=6

      AUTO_REDEEM=true
      AUTO_REDEEM_INTERVAL_MINUTES=30
      REDEEM_LOOKBACK_HOURS=24
    """

    tick_min = int(os.getenv("AUTO_TICK_MINUTES", "30"))
    lead_min = int(os.getenv("AUTO_LEAD_MINUTES", "30"))
    slot_min = int(os.getenv("AUTO_SLOT_MINUTES", "30"))

    # Cuántos markets máximo intentamos en cada plan.
    orders_per_plan = int(os.getenv("AUTO_ORDERS_PER_PLAN", os.getenv("AUTO_ORDERS_PER_TICK", "6")))

    auto_redeem = os.getenv("AUTO_REDEEM", "false").strip().lower() in ("1", "true", "yes", "y")
    redeem_every_min = int(os.getenv("AUTO_REDEEM_INTERVAL_MINUTES", "30"))
    lookback_h = int(os.getenv("REDEEM_LOOKBACK_HOURS", "24"))

    # Barrera simple de gasto por market
    pair_budget = float(os.getenv("AUTO_PAIR_BUDGET_USDC", "50") or 50)
    est_cost = (cfg.price_up * cfg.size_up) + (cfg.price_down * cfg.size_down)
    if est_cost > pair_budget:
        print(
            f"[auto][WARN] coste estimado por market ({est_cost:.4f} USDC) > AUTO_PAIR_BUDGET_USDC ({pair_budget:.4f}). "
            "Reduciendo SIZE_* o sube el budget si es intencional.",
            flush=True,
        )

    # --- Timeline anclado a WINDOW_START/WINDOW_END ---
    timeline_start_local = cfg.parse_local_dt(cfg.window_start_local).replace(second=0, microsecond=0)
    timeline_end_local = cfg.parse_local_dt(cfg.window_end_local).replace(second=0, microsecond=0)
    if timeline_end_local <= timeline_start_local:
        timeline_end_local = timeline_end_local + timedelta(days=1)

    cursor = timeline_start_local

    placed_slugs: set[str] = set()
    last_redeem_at: datetime | None = None

    print("=== AUTO_MODE ENABLED (anchored to WINDOW_START) ===", flush=True)
    print(
        f"timeline={timeline_start_local.strftime('%Y-%m-%d %H:%M')} -> {timeline_end_local.strftime('%Y-%m-%d %H:%M')} "
        f"| tick={tick_min}m lead={lead_min}m slot={slot_min}m orders/plan={orders_per_plan} "
        f"| redeem_every={redeem_every_min}m lookback={lookback_h}h",
        flush=True,
    )

    while True:
        # Stop condition: cuando el cursor ya está fuera de la ventana.
        if cursor >= timeline_end_local:
            print("[auto] WINDOW_END alcanzado. Terminando bucles.", flush=True)
            return 0

        # Si aún no hemos llegado al instante "cursor" en tiempo real, esperamos.
        now_local_real = datetime.now(cfg.tz).replace(second=0, microsecond=0)
        if now_local_real < cursor:
            # Dormimos hasta el cursor (con granularidad segura)
            sleep_s = max(1, int((cursor - now_local_real).total_seconds()))
            time.sleep(min(sleep_s, 30))
            continue

        # --- Redeem (alineado a timeline, no a time.time()) ---
        if auto_redeem:
            if (last_redeem_at is None) or (cursor - last_redeem_at) >= timedelta(minutes=redeem_every_min):
                try:
                    print(f"[auto][redeem @ {cursor.strftime('%Y-%m-%d %H:%M')}] START", flush=True)
                    from tool.redeem import redeem_last_hours
                    redeem_last_hours(cfg, lookback_h)
                    print(f"[auto][redeem @ {cursor.strftime('%Y-%m-%d %H:%M')}] END", flush=True)
                except Exception as e:
                    print(f"[auto][redeem][FAIL] {e}", flush=True)
                last_redeem_at = cursor

        # --- Planificación anclada ---
        slot_start_local = cursor + timedelta(minutes=lead_min)
        if slot_start_local >= timeline_end_local:
            print("[auto] Slot start fuera de WINDOW_END. Terminando.", flush=True)
            return 0

        slot_end_local = slot_start_local + timedelta(minutes=slot_min)
        if slot_end_local > timeline_end_local:
            slot_end_local = timeline_end_local

        cfg.window_start_local = slot_start_local.strftime("%Y-%m-%d %H:%M")
        cfg.window_end_local = slot_end_local.strftime("%Y-%m-%d %H:%M")

        start_utc, end_utc = cfg.window_utc_range()
        print(
            f"[auto][plan @ {cursor.strftime('%Y-%m-%d %H:%M')}] local {cfg.window_start_local} -> {cfg.window_end_local} "
            f"| utc {start_utc.isoformat()} -> {end_utc.isoformat()}",
            flush=True,
        )

        # --- Descubrimiento de markets ---
        markets = []
        try:
            # Para BTC 5m: lookup por slugs esperados (cada 5m) para evitar latencia de listado por ventana.
            step = 5 * 60
            t0 = int(start_utc.timestamp())
            t1 = int(end_utc.timestamp())
            t0 = (t0 // step) * step
            if t0 < int(start_utc.timestamp()):
                t0 += step
            expected_slugs = [f"btc-updown-5m-{ts}" for ts in range(t0, t1, step)]

            markets = gamma_get_markets_by_slugs(cfg, expected_slugs)

            if not markets:
                markets = gamma_list_markets_for_series_in_window(cfg)

        except Exception as e:
            print(f"[auto][gamma][FAIL] {e}", flush=True)
            # Avanzamos cursor igualmente para no quedarnos bloqueados.
            cursor = cursor + timedelta(minutes=tick_min)
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

        # Avanzamos el timeline
        cursor = cursor + timedelta(minutes=tick_min)


def main():