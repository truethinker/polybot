from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional, Any

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

from tool.config import Config


# NOTE
# ----
# Este módulo NO toca nada del flujo de órdenes.
# Mantiene el comportamiento previo (detect-only) por defecto.
# Si REDEEM_ONCHAIN=true, ejecuta redeem on-chain con la EOA (cfg.private_key).


def _env_flag(name: str, default: str = "false") -> bool:
    import os
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "y")


# =====================================
# CLIENT (IDÉNTICO AL QUE METE ÓRDENES)
# =====================================

def _mk_client(cfg: Config) -> ClobClient:
    client = ClobClient(
        host=cfg.clob_host.rstrip("/"),
        chain_id=cfg.chain_id,
        key=cfg.private_key,
        signature_type=cfg.signature_type,
        funder=cfg.funder_address,
    )

    if cfg.use_derived_creds:
        client.set_api_creds(client.create_or_derive_api_creds())
        print("[redeem] derived creds applied")
    else:
        client.set_api_creds(
            ApiCreds(
                api_key=cfg.clob_api_key,
                api_secret=cfg.clob_api_secret,
                api_passphrase=cfg.clob_api_passphrase,
            )
        )
        print("[redeem] manual creds applied")

    return client


# =====================================
# TIMESTAMP PARSING ROBUSTO
# =====================================

def _parse_dt(v: Any) -> Optional[datetime]:
    if v is None:
        return None

    if isinstance(v, datetime):
        return v.astimezone(timezone.utc)

    if isinstance(v, (int, float)):
        x = float(v)
        if x > 1e12:  # ms
            return datetime.fromtimestamp(x / 1000, tz=timezone.utc)
        return datetime.fromtimestamp(x, tz=timezone.utc)

    if isinstance(v, str):
        s = v.strip()
        if s.isdigit():
            return _parse_dt(int(s))
        try:
            if s.endswith("Z"):
                s = s.replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    return None


# =====================================
# REDEEM LOGIC (SOLO DETECCIÓN)
# =====================================

def redeem_last_hours(cfg: Config, lookback_hours: int) -> None:
    import os
    redeem_onchain = _env_flag("REDEEM_ONCHAIN", "false")
    auto_bridge_withdraw = _env_flag("AUTO_BRIDGE_WITHDRAW", "false")

    print(f"[redeem] START ({'onchain' if redeem_onchain else 'detect-only'})")

    end_utc = datetime.fromisoformat(
        cfg.window_end_utc_iso().replace("Z", "+00:00")
    ).astimezone(timezone.utc)

    start_utc = end_utc - timedelta(hours=int(lookback_hours))

    print(f"[redeem] window: {start_utc} -> {end_utc}")

    # --- Always keep the old detection output (useful debugging) ---
    try:
        client = _mk_client(cfg)
        trades = client.get_trades()
        if isinstance(trades, dict) and "data" in trades:
            trades = trades["data"]
        trades = trades or []
        print(f"[redeem] trades fetched: {len(trades)}")
        hits = []
        for t in trades:
            dt = _parse_dt(t.get("match_time")) or _parse_dt(t.get("last_update"))
            if dt and start_utc <= dt <= end_utc:
                hits.append((t, dt))
        print(f"[redeem] hits in window: {len(hits)}")
        for t, dt in hits[:20]:
            print(
                "[redeem][TRADE]",
                {
                    "dt": dt.isoformat(),
                    "market": t.get("market"),
                    "outcome": t.get("outcome"),
                    "side": t.get("side"),
                    "price": t.get("price"),
                    "size": t.get("size"),
                    "status": t.get("status"),
                },
            )
    except Exception as e:
        print(f"[redeem][WARN] detect-only step failed: {e}")

    if not redeem_onchain:
        print("[redeem] END")
        return

    # --- On-chain redeem ---
    try:
        import requests
        from web3 import Web3
    except Exception as e:
        print(f"[redeem][FAIL] missing deps for onchain redeem: {e}")
        print("[redeem] END")
        return

    rpc_url = os.getenv("RPC_URL", "").strip()
    if not rpc_url:
        print("[redeem][FAIL] RPC_URL is required when REDEEM_ONCHAIN=true")
        print("[redeem] END")
        return

    data_api = os.getenv("DATA_API_URL", "https://data-api.polymarket.com").rstrip("/")
    ctf_addr = Web3.to_checksum_address(os.getenv(
        "CONDITIONAL_TOKENS_ADDRESS",
        "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045",
    ))
    collateral_addr = Web3.to_checksum_address(os.getenv(
        "COLLATERAL_TOKEN_ADDRESS",
        # USDC (PoS) en Polygon (standard en Polymarket)
        "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
    ))
    min_usd = float(os.getenv("MIN_REDEEMABLE_USD", "0") or 0)
    wait_confs = int(os.getenv("REDEEM_WAIT_CONFIRMATIONS", "1") or 1)

    # Minimal ABI for redeemPositions
    CTF_ABI = [
        {
            "inputs": [
                {"internalType": "address", "name": "collateralToken", "type": "address"},
                {"internalType": "bytes32", "name": "parentCollectionId", "type": "bytes32"},
                {"internalType": "bytes32", "name": "conditionId", "type": "bytes32"},
                {"internalType": "uint256[]", "name": "indexSets", "type": "uint256[]"},
            ],
            "name": "redeemPositions",
            "outputs": [],
            "stateMutability": "nonpayable",
            "type": "function",
        }
    ]

    from web3 import Web3
    from web3.middleware.proof_of_authority import ExtraDataToPOAMiddleware

    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 20}))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

    if not w3.is_connected():
        print("[redeem][FAIL] could not connect to RPC_URL")
        print("[redeem] END")
        return

    acct = w3.eth.account.from_key(cfg.private_key)
    eoa = acct.address

    # Pull redeemable positions from Data API
    # Docs: /positions?user=0x..&redeemable=true&sizeThreshold=0&limit=500
    url = f"{data_api}/positions"
    params = {
        "user": eoa,
        "redeemable": "true",
        "sizeThreshold": "0",
        "limit": "500",
    }

    try:
        r = requests.get(url, params=params, timeout=20)
        if not r.ok:
            raise RuntimeError(f"positions API failed: {r.status_code} {r.text[:200]}")
        positions = r.json() or []
    except Exception as e:
        print(f"[redeem][FAIL] fetching redeemable positions failed: {e}")
        print("[redeem] END")
        return

    # Filter positions that are actually redeemable + above min_usd if field present
    redeemable = []
    for p in positions:
        if not p or not p.get("redeemable"):
            continue
        cv = p.get("currentValue")
        if cv is not None:
            try:
                if float(cv) < min_usd:
                    continue
            except Exception:
                pass
        redeemable.append(p)

    print(f"[redeem][onchain] redeemable positions: {len(redeemable)} (min_usd={min_usd})")

    if not redeemable:
        print("[redeem][onchain] nothing to redeem")
        print("[redeem] END")
        return

    # Group by conditionId; gather indexSets from outcomeIndex
    grouped: dict[str, set[int]] = {}
    skipped_neg = 0
    for p in redeemable:
        if p.get("negativeRisk"):
            skipped_neg += 1
            continue
        cid = (p.get("conditionId") or "").lower()
        if not cid:
            continue
        oi = p.get("outcomeIndex")
        if oi is None:
            continue
        try:
            oi_int = int(oi)
        except Exception:
            continue
        index_set = 1 << oi_int
        grouped.setdefault(cid, set()).add(index_set)

    if skipped_neg:
        print(f"[redeem][onchain][WARN] skipped {skipped_neg} negativeRisk positions (adapter not enabled)")

    if not grouped:
        print("[redeem][onchain] nothing redeemable after filtering")
        print("[redeem] END")
        return

    ctf = w3.eth.contract(address=ctf_addr, abi=CTF_ABI)

    # EIP-1559 gas controls (optional)
    max_priority_gwei = float(os.getenv("MAX_PRIORITY_FEE_GWEI", "30") or 30)
    gas_bump = float(os.getenv("GAS_PRICE_BUMP", "1.2") or 1.2)
    dry_run = bool(getattr(cfg, "dry_run", False))

    chain_id = int(getattr(cfg, "chain_id", 137) or 137)

    # Nonce is shared across txs
    nonce = w3.eth.get_transaction_count(eoa)

    # parentCollectionId = 0x00..00 for Polymarket single-condition markets
    parent_collection_id = b"\x00" * 32

    success = 0
    failed = 0

    for cid, idx_sets in grouped.items():
        idx_list = sorted(idx_sets)
        title = None
        # optional: find a title from positions list
        for p in redeemable:
            if (p.get("conditionId") or "").lower() == cid and p.get("title"):
                title = p.get("title")
                break
        label = (title or cid[:12])

        try:
            tx = ctf.functions.redeemPositions(
                collateral_addr,
                parent_collection_id,
                Web3.to_bytes(hexstr=cid),
                idx_list,
            ).build_transaction({
                "from": eoa,
                "nonce": nonce,
                "chainId": chain_id,
            })

            # Estimate gas
            try:
                est = w3.eth.estimate_gas(tx)
                tx["gas"] = int(est * 1.25)  # buffer
            except Exception:
                # fallback
                tx["gas"] = 350_000

            # Fees (best-effort)
            try:
                pending = w3.eth.get_block("pending")
                base_fee = pending.get("baseFeePerGas")
                if base_fee is not None:
                    max_priority = int(max_priority_gwei * 1e9)
                    max_fee = int((int(base_fee) + max_priority) * gas_bump)
                    tx["maxPriorityFeePerGas"] = max_priority
                    tx["maxFeePerGas"] = max_fee
                    tx["type"] = 2
                else:
                    # legacy
                    gp = int(w3.eth.gas_price * gas_bump)
                    tx["gasPrice"] = gp
                    tx["type"] = 0
            except Exception:
                gp = int(w3.eth.gas_price * gas_bump)
                tx["gasPrice"] = gp
                tx["type"] = 0

            print(f"[redeem][onchain] redeem {label} indexSets={idx_list} nonce={nonce} gas={tx.get('gas')}")

            if dry_run:
                print("[redeem][onchain] DRY_RUN=true (tx not sent)")
                nonce += 1
                continue

            signed = acct.sign_transaction(tx)
            txh = w3.eth.send_raw_transaction(signed.raw_transaction)
            tx_hex = txh.hex()
            print(f"[redeem][onchain] sent: {tx_hex}")
            receipt = w3.eth.wait_for_transaction_receipt(txh, confirmations=wait_confs, timeout=300)
            status = receipt.get("status")
            if status == 1:
                print(f"[redeem][onchain] confirmed: {tx_hex}")
                success += 1
            else:
                print(f"[redeem][onchain][FAIL] reverted: {tx_hex}")
                failed += 1
            nonce += 1
        except Exception as e:
            failed += 1
            print(f"[redeem][onchain][FAIL] {label}: {e}")

    print(f"[redeem][onchain] done. success={success} failed={failed}")

    # Optional: bridge-withdraw USDC.e out of Polygon after redeem.
    # This is independent from the redeem itself.
    if auto_bridge_withdraw and success > 0:
        try:
            from tool.bridge_withdraw import BridgeWithdrawRequest, create_withdraw_addresses, erc20_transfer, to_raw_amount

            bridge_api = os.getenv("BRIDGE_API_URL", "https://bridge.polymarket.com").rstrip("/")
            to_chain_id = (os.getenv("BRIDGE_TO_CHAIN_ID", "") or "").strip()
            to_token = (os.getenv("BRIDGE_TO_TOKEN_ADDRESS", "") or "").strip()
            recipient = (os.getenv("BRIDGE_RECIPIENT", "") or "").strip()
            amount_usd = float(os.getenv("BRIDGE_WITHDRAW_AMOUNT_USD", "0") or 0)

            if not (to_chain_id and to_token and recipient and amount_usd > 0):
                print(
                    "[redeem][bridge][SKIP] AUTO_BRIDGE_WITHDRAW=true but missing config. "
                    "Need BRIDGE_TO_CHAIN_ID, BRIDGE_TO_TOKEN_ADDRESS, BRIDGE_RECIPIENT, BRIDGE_WITHDRAW_AMOUNT_USD>0"
                )
            else:
                req = BridgeWithdrawRequest(
                    address=eoa,
                    to_chain_id=str(to_chain_id),
                    to_token_address=str(to_token),
                    recipient_addr=str(recipient),
                )
                resp = create_withdraw_addresses(req, bridge_api=bridge_api)
                addr_obj = (resp or {}).get("address") or {}
                dep = addr_obj.get("evm")
                if not dep:
                    raise RuntimeError(f"bridge response missing address.evm: {resp}")

                raw = to_raw_amount(amount_usd, decimals=6)
                print(
                    f"[redeem][bridge] depositAddress(evm)={dep} amount_usd={amount_usd} raw={raw} "
                    f"toChainId={to_chain_id} recipient={recipient}"
                )

                txh = erc20_transfer(
                    rpc_url=rpc_url,
                    private_key=cfg.private_key,
                    token_address=collateral_addr,
                    to_address=dep,
                    amount_raw=raw,
                    chain_id=chain_id,
                    gas_price_bump=gas_bump,
                    max_priority_fee_gwei=max_priority_gwei,
                    wait_confirmations=wait_confs,
                    dry_run=dry_run,
                )
                print(f"[redeem][bridge] sent USDC.e transfer: {txh}")
        except Exception as e:
            print(f"[redeem][bridge][WARN] bridge withdraw step failed: {e}")

    print("[redeem] END")
