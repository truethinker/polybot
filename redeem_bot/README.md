# Redeem bot (separado)

Este bot es **paralelo** al bot de órdenes Python (no lo toca). Sirve para **cobrar (redeem)** posiciones ganadoras **ya resueltas**.

> Nota: "redeem" convierte tus shares ganadoras en colateral (USDC/USDC.e). Si además quieres **withdraw** a tu wallet desde el exchange, eso es otro paso distinto.

## 1) Requisitos
- Node.js 18+.

## 2) Instalación
```bash
cd redeem_bot
npm i
```

## 3) Variables de entorno
Puedes reutilizar las mismas credenciales CLOB y la misma wallet.

Mínimo:
- `PRIVATE_KEY`
- `FUNDER_ADDRESS`
- `CLOB_API_KEY`
- `CLOB_API_SECRET`
- `CLOB_API_PASSPHRASE`

Opcional:
- `CHAIN_ID` (default 137)
- `DRY_RUN` (default false)
- `MIN_REDEEMABLE_USD` (default 0)
- `RELAYER_URL` (default https://relayer.polymarket.com)
- `DATA_API_URL` (default https://data-api.polymarket.com)
- `CONDITIONAL_TOKENS_ADDRESS` (default 0x4D97...)
- `COLLATERAL_TOKEN_ADDRESS` (default 0x2791...)

## 4) Ejecutar
```bash
npm run redeem
```

## Troubleshooting
- Si te devuelve 403 *Trading restricted in your region*, no es un error de código: es un bloqueo por región.
- Si te devuelve *invalid signature*, suele ser una combinación incorrecta de: `PRIVATE_KEY` / `FUNDER_ADDRESS` / credenciales o approvals.
