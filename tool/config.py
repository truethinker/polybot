import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
import pytz


@dataclass
class Config:
    gamma_host: str
    clob_host: str
    funder_address: str
    signature_type: int

    private_key: str
    clob_api_key: str
    clob_api_secret: str
    clob_api_passphrase: str
    chain_id: int

    series_slug: str

    window_start_local: str
    window_end_local: str

    price_up: float
    size_up: float
    price_down: float
    size_down: float

    dry_run: bool
    max_markets: int

    # redeem
    auto_redeem: bool
    redeem_lookback_hours: int
    use_derived_creds: bool

    @property
    def tz(self):
        # Permite override por env (útil si el bot corre en otra región)
        tz_name = os.getenv("TIMEZONE", "Europe/Madrid").strip() or "Europe/Madrid"
        return pytz.timezone(tz_name)

    def parse_local_dt(self, s: str) -> datetime:
        """Parsea fechas/hora de forma robusta en tz local.

        Acepta:
        - ISO: YYYY-MM-DD HH:MM[:SS] o con 'T'
        - DD/MM/YYYY HH:MM  | DD-MM-YYYY HH:MM
        - "21 de Febrero 2026 23:15" (mes en castellano, año opcional)

        Si el string ya viene con tzinfo (offset), se respeta y luego se convierte.
        """
        raw = (s or "").strip()
        if not raw:
            raise ValueError("Fecha/hora vacía")

        # 1) ISO (incluye aware con offset)
        try:
            iso = raw.replace("Z", "+00:00")
            dt = datetime.fromisoformat(iso)
            if dt.tzinfo is None:
                return self.tz.localize(dt)
            return dt.astimezone(self.tz)
        except Exception:
            pass

        # 2) DD/MM/YYYY o DD-MM-YYYY
        m = re.match(r"^(?P<d>\d{1,2})[\/-](?P<mo>\d{1,2})[\/-](?P<y>\d{2,4})\s+(?P<h>\d{1,2}):(?P<mi>\d{2})(?::(?P<se>\d{2}))?$", raw)
        if m:
            d = int(m.group("d"))
            mo = int(m.group("mo"))
            y = int(m.group("y"))
            if y < 100:
                y += 2000
            h = int(m.group("h"))
            mi = int(m.group("mi"))
            se = int(m.group("se") or 0)
            return self.tz.localize(datetime(y, mo, d, h, mi, se))

        # 3) "21 de Febrero [de] 2026 23:15" (año opcional)
        months_es = {
            "enero": 1,
            "febrero": 2,
            "marzo": 3,
            "abril": 4,
            "mayo": 5,
            "junio": 6,
            "julio": 7,
            "agosto": 8,
            "septiembre": 9,
            "setiembre": 9,
            "octubre": 10,
            "noviembre": 11,
            "diciembre": 12,
        }
        m = re.match(
            r"^(?P<d>\d{1,2})\s+de\s+(?P<mon>[A-Za-zÁÉÍÓÚáéíóúñÑ]+)(?:\s+de\s+(?P<y>\d{4}))?\s+(?P<h>\d{1,2}):(?P<mi>\d{2})(?::(?P<se>\d{2}))?$",
            raw,
            flags=re.IGNORECASE,
        )
        if m:
            d = int(m.group("d"))
            mon = m.group("mon").lower()
            mon = (
                mon.replace("á", "a")
                .replace("é", "e")
                .replace("í", "i")
                .replace("ó", "o")
                .replace("ú", "u")
            )
            if mon not in months_es:
                raise ValueError(f"Mes no reconocido: {m.group('mon')}")
            mo = months_es[mon]
            y = int(m.group("y") or datetime.now(self.tz).year)
            h = int(m.group("h"))
            mi = int(m.group("mi"))
            se = int(m.group("se") or 0)
            return self.tz.localize(datetime(y, mo, d, h, mi, se))

        raise ValueError(
            "Formato de fecha/hora no reconocido. Usa ISO (YYYY-MM-DD HH:MM) o DD/MM/YYYY HH:MM, "
            "o '21 de Febrero 23:15'."
        )

    def window_utc_range(self) -> tuple[datetime, datetime]:
        """Devuelve (start_utc, end_utc) asegurando end > start.

        Si el usuario define una ventana que cruza medianoche (end <= start),
        se asume que end es el día siguiente.
        """
        start_local = self.parse_local_dt(self.window_start_local)
        end_local = self.parse_local_dt(self.window_end_local)
        if end_local <= start_local:
            end_local = end_local + timedelta(days=1)
        return (start_local.astimezone(pytz.UTC), end_local.astimezone(pytz.UTC))

    def window_start_utc_iso(self) -> str:
        dt_utc, _ = self.window_utc_range()
        return dt_utc.isoformat().replace("+00:00", "Z")

    def window_end_utc_iso(self) -> str:
        _, dt_utc = self.window_utc_range()
        return dt_utc.isoformat().replace("+00:00", "Z")


def _getenv(name: str, default: str | None = None, required: bool = False) -> str:
    v = os.getenv(name, default)
    if required and (v is None or v.strip() == ""):
        raise RuntimeError(f"Falta variable de entorno requerida: {name}")
    return v.strip() if isinstance(v, str) else v


def load_config() -> Config:
    return Config(
        gamma_host=_getenv("GAMMA_HOST", "https://gamma-api.polymarket.com"),
        clob_host=_getenv("CLOB_HOST", "https://clob.polymarket.com"),
        funder_address=_getenv("FUNDER_ADDRESS", required=True),
        signature_type=int(_getenv("SIGNATURE_TYPE", "0")),

        private_key=_getenv("PRIVATE_KEY", required=True),
        clob_api_key=_getenv("CLOB_API_KEY", ""),
        clob_api_secret=_getenv("CLOB_API_SECRET", ""),
        clob_api_passphrase=_getenv("CLOB_API_PASSPHRASE", ""),
        chain_id=int(_getenv("CHAIN_ID", "137")),

        series_slug=_getenv("SERIES_SLUG", "btc-up-or-down-5m"),

        window_start_local=_getenv("WINDOW_START", required=True),
        window_end_local=_getenv("WINDOW_END", required=True),

        price_up=float(_getenv("PRICE_UP", required=True)),
        size_up=float(_getenv("SIZE_UP", required=True)),
        price_down=float(_getenv("PRICE_DOWN", required=True)),
        size_down=float(_getenv("SIZE_DOWN", required=True)),

        dry_run=_getenv("DRY_RUN", "true").lower() in ("1", "true", "yes"),
        max_markets=int(_getenv("MAX_MARKETS", "200")),

        auto_redeem=_getenv("AUTO_REDEEM", "false").lower() in ("1", "true", "yes"),
        redeem_lookback_hours=int(_getenv("REDEEM_LOOKBACK_HOURS", "12")),
        use_derived_creds=_getenv("USE_DERIVED_CREDS", "false").lower() in ("1", "true", "yes"),
    )
