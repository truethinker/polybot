# Polymarket BTC 5m Slot Order Tool

Herramienta para:
- Detectar mercados BTC Up/Down de 5 minutos en una ventana horaria (Europe/Madrid)
- Poner órdenes limitadas en Up y Down con precio y size definidos

## Setup

1) Copia `.env.example` a `.env` y rellena:
- PRIVATE_KEY (hot wallet)
- CLOB_API_KEY / SECRET / PASSPHRASE (API keys del CLOB)
- WINDOW_START / WINDOW_END
- PRICE_UP/SIZE_UP y PRICE_DOWN/SIZE_DOWN

2) Instala:
pip install -r requirements.txt

3) Ejecuta:
python main.py

## Seguridad
- Usa una hot wallet con poco saldo.
- Deja DRY_RUN=true hasta validar el listado de markets correcto.
