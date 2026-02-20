import "dotenv/config";
import axios from "axios";
import { ethers } from "ethers";
import {
  createRelayerClient,
  createRedeemPositionsMessage,
  createTokenApprovalMessage,
} from "@polymarket/builder-relayer-client";
import {
  createOperatorApprovalMessage,
  signClobAuthMessage,
  signUserOperation,
} from "@polymarket/builder-signing-sdk";

/*
  Redeem (cobrar) mercados resueltos.

  Basado en el ejemplo oficial de Polymarket (inventory / redemption) y un script de referencia
  publicado por la comunidad.

  Flujo (alto nivel):
  1) Consultar posiciones del usuario en Data API (ERC1155 positions)
  2) Filtrar las que estén 'redeemable'
  3) Dar approvals necesarios (operator approval para ConditionalTokens + token approval de collateral)
  4) Ejecutar redeemPositions vía relayer (gasless)

  IMPORTANTE:
  - Esto no "retira" a tu banco: el redeem convierte tus shares ganadoras en colateral (USDC/USDC.e).
  - Si además tienes saldo "en el exchange" (CLOB), el withdraw es otro paso.
*/

const {
  PRIVATE_KEY,
  FUNDER_ADDRESS,
  CLOB_API_KEY,
  CLOB_API_SECRET,
  CLOB_API_PASSPHRASE,
  CHAIN_ID,
  // opcionales
  RELAYER_URL,
  DATA_API_URL,
  // contratos (defaults en Polygon)
  CONDITIONAL_TOKENS_ADDRESS,
  COLLATERAL_TOKEN_ADDRESS,
  // control
  DRY_RUN,
  MIN_REDEEMABLE_USD,
} = process.env;

if (!PRIVATE_KEY) throw new Error("Missing PRIVATE_KEY");
if (!FUNDER_ADDRESS) throw new Error("Missing FUNDER_ADDRESS");
if (!CLOB_API_KEY || !CLOB_API_SECRET || !CLOB_API_PASSPHRASE) {
  throw new Error("Missing CLOB API creds (CLOB_API_KEY/SECRET/PASSPHRASE)");
}

const chainId = Number(CHAIN_ID || "137");
const relayerUrl = RELAYER_URL || "https://relayer.polymarket.com";
const dataApiUrl = DATA_API_URL || "https://data-api.polymarket.com";

// Nota: en Polygon el token que en muchas wallets aparece como "USDC.e" se representa por el
// contrato 0x2791... (Polymarket lo usa como colateral en muchos mercados).
const collateralTokenAddress =
  COLLATERAL_TOKEN_ADDRESS || "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174";

// Dirección del contrato ConditionalTokens usado por Polymarket (Polygon) en ejemplos públicos.
const conditionalTokensAddress =
  CONDITIONAL_TOKENS_ADDRESS || "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045";

const dryRun = String(DRY_RUN || "false").toLowerCase() in {
  "1": true,
  "true": true,
  "yes": true,
  "y": true,
};

const minRedeemableUsd = Number(MIN_REDEEMABLE_USD || "0");

const pk = PRIVATE_KEY.startsWith("0x") ? PRIVATE_KEY : `0x${PRIVATE_KEY}`;
const signer = new ethers.Wallet(pk);

function nowIso() {
  return new Date().toISOString();
}

async function fetchPositions() {
  // Data API: posiciones por address. Si esta ruta cambia, se ajusta aquí.
  const url = `${dataApiUrl.replace(/\/$/, "")}/positions?user=${FUNDER_ADDRESS}`;
  const { data } = await axios.get(url, { timeout: 30_000 });
  if (!Array.isArray(data)) throw new Error(`Unexpected positions payload: ${typeof data}`);
  return data;
}

function toUsd(pos) {
  // Muchos endpoints devuelven amount en unidades de colateral (6 decimales). Intento robusto.
  const v = pos?.redeemable || pos?.redeemableValue || pos?.redeemable_value || 0;
  const n = typeof v === "string" ? Number(v) : Number(v);
  return Number.isFinite(n) ? n : 0;
}

function pickRedeemables(positions) {
  // Heurística: pos.redeemable > 0 o pos.isRedeemable === true
  return positions
    .filter((p) => {
      const r = toUsd(p);
      return (p?.isRedeemable === true || r > 0) && r >= minRedeemableUsd;
    })
    .map((p) => ({
      conditionId: p.conditionId || p.condition_id || p.condition,
      indexSet: p.indexSet || p.index_set || p.index,
      redeemableUsd: toUsd(p),
    }))
    .filter((x) => x.conditionId && x.indexSet);
}

async function main() {
  console.log(`\n[${nowIso()}] === Redeem Bot ===`);
  console.log(`chainId=${chainId}`);
  console.log(`funder=${FUNDER_ADDRESS}`);
  console.log(`relayer=${relayerUrl}`);
  console.log(`dataApi=${dataApiUrl}`);
  console.log(`conditionalTokens=${conditionalTokensAddress}`);
  console.log(`collateralToken=${collateralTokenAddress}`);
  console.log(`dryRun=${dryRun}`);
  console.log(`minRedeemableUsd=${minRedeemableUsd}`);

  const positions = await fetchPositions();
  const redeemables = pickRedeemables(positions);

  if (redeemables.length === 0) {
    console.log("No hay posiciones redeemables.");
    return;
  }

  console.log(`Encontradas ${redeemables.length} posiciones redeemables:`);
  for (const r of redeemables) {
    console.log(`- conditionId=${r.conditionId} indexSet=${r.indexSet} redeemableUsd~${r.redeemableUsd}`);
  }

  if (dryRun) {
    console.log("DRY_RUN=true -> no ejecuto transacciones.");
    return;
  }

  const clobClient = createRelayerClient({
    apiKey: CLOB_API_KEY,
    apiSecret: CLOB_API_SECRET,
    apiPassphrase: CLOB_API_PASSPHRASE,
    chainId,
    relayerUrl,
  });

  // 1) Operator approval (ConditionalTokens)
  {
    const msg = createOperatorApprovalMessage({
      owner: FUNDER_ADDRESS,
      operator: conditionalTokensAddress,
      approved: true,
    });
    const sig = await signUserOperation({ signer, message: msg });
    const auth = await signClobAuthMessage({
      signer,
      apiKey: CLOB_API_KEY,
      apiSecret: CLOB_API_SECRET,
      apiPassphrase: CLOB_API_PASSPHRASE,
    });
    await clobClient.setOperatorApproval({ message: msg, signature: sig, clobAuth: auth });
  }

  // 2) Token approval (collateral)
  {
    const msg = createTokenApprovalMessage({
      tokenAddress: collateralTokenAddress,
      spender: conditionalTokensAddress,
      amount: ethers.constants.MaxUint256.toString(),
    });
    const sig = await signUserOperation({ signer, message: msg });
    const auth = await signClobAuthMessage({
      signer,
      apiKey: CLOB_API_KEY,
      apiSecret: CLOB_API_SECRET,
      apiPassphrase: CLOB_API_PASSPHRASE,
    });
    await clobClient.approveToken({ message: msg, signature: sig, clobAuth: auth });
  }

  // 3) Redeem positions (batch)
  {
    const msg = createRedeemPositionsMessage({
      user: FUNDER_ADDRESS,
      positions: redeemables.map((r) => ({ conditionId: r.conditionId, indexSet: r.indexSet })),
    });
    const sig = await signUserOperation({ signer, message: msg });
    const auth = await signClobAuthMessage({
      signer,
      apiKey: CLOB_API_KEY,
      apiSecret: CLOB_API_SECRET,
      apiPassphrase: CLOB_API_PASSPHRASE,
    });
    const resp = await clobClient.redeemPositions({ message: msg, signature: sig, clobAuth: auth });
    console.log("Redeem submitted:", resp);
  }

  console.log("Done.");
}

main().catch((e) => {
  console.error("Redeem bot failed:", e?.response?.data || e);
  process.exit(1);
});
