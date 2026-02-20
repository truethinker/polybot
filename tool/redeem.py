from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

from tool.config import Config


# Direcciones oficiales usadas en la doc de Polymarket (Polygon)  [oai_citation:3‡docs.polymarket.com](https://docs.polymarket.com/developers/market-makers/inventory)
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDCe_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

# ABI mínimo para redeemPositions y mergePositions (CTF)
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
    },
    {
        "inputs": [
            {"internalType": "address", "name": "collateralToken", "type": "address"},
            {"internalType": "bytes32", "name": "parentCollectionId", "type": "bytes32"},
            {"internalType": "bytes32", "name": "conditionId", "type": "bytes32"},
            {"internalType": "uint256[]", "name": "partition", "type": "uint256[]"},
            {"internalType": "uint256", "name": "amount", "type": "uint256"},
        ],
        "name": "mergePositions",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]


@dataclass
class RedeemResult:
    tx_hash: str


def _get_web3(cfg: Config):
    try:
        from web3 import Web3
    except Exception as e:
        raise RuntimeError("Falta dependencia web3. Instala: pip install web3") from e

    w3 = Web3(Web3.HTTPProvider(cfg.polygon_rpc_url))
    if not w3.is_connected():
        raise RuntimeError(f"No conecto al RPC Polygon: {cfg.polygon_rpc_url}")
    return w3


def redeem_positions(cfg: Config, condition_id_hex: str, index_sets: Optional[list[int]] = None) -> RedeemResult:
    """
    Ejecuta redeemPositions(collateral=USDC.e, parent=0x0, conditionId, indexSets)
    index_sets por defecto [1,2] (YES/NO). Solo paga la ganadora.  [oai_citation:4‡docs.polymarket.com](https://docs.polymarket.com/developers/market-makers/inventory)
    """
    if index_sets is None:
        index_sets = [1, 2]

    w3 = _get_web3(cfg)

    acct = w3.eth.account.from_key(cfg.private_key)
    from_addr = acct.address

    ctf = w3.eth.contract(address=w3.to_checksum_address(CTF_ADDRESS), abi=CTF_ABI)

    condition_id = bytes.fromhex(condition_id_hex.replace("0x", ""))
    if len(condition_id) != 32:
        raise RuntimeError("conditionId debe ser bytes32 (32 bytes). Pasa un hex 0x... de 32 bytes.")

    tx = ctf.functions.redeemPositions(
        w3.to_checksum_address(USDCe_ADDRESS),
        b"\x00" * 32,  # parentCollectionId = 0
        condition_id,
        index_sets,
    ).build_transaction(
        {
            "from": from_addr,
            "nonce": w3.eth.get_transaction_count(from_addr),
            "chainId": cfg.chain_id,
        }
    )

    # Gas estimation
    tx["gas"] = int(w3.eth.estimate_gas(tx) * 1.2)
    tx["maxFeePerGas"] = w3.eth.gas_price
    tx["maxPriorityFeePerGas"] = 0

    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)

    return RedeemResult(tx_hash=tx_hash.hex())
