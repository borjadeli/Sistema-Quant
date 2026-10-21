"""Precios, foto de mercado y cadenas de opciones vía yfinance (fuente NO oficial).

Ventajas: gratis, cobertura global (sufijos .T, .TW, .TO, ...). Limitaciones: sin SLA, puede cambiar o
bloquearse, cotizaciones de opciones con retraso. Por eso:
  - cada dato queda etiquetado `source="yahoo"` con timestamp de obtención;
  - el módulo de opciones exige que la cadena tenga menos de N horas de antigüedad;
  - la interfaz permite reemplazarlo por IBKR / Polygon sin tocar el resto del sistema.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd

from qa.providers.base import MarketSnapshotData, OptionChain, OptionQuoteData, ProviderUnavailable
from qa.universe.model import Company


def _yf():
    try:
        import yfinance as yf  # noqa: PLC0415
    except ImportError as e:  # pragma: no cover
        raise ProviderUnavailable("yfinance no instalado: pip install 'quant-advisory[market]'") from e
    return yf


class YahooProvider:
    name = "yahoo"

    def __init__(self, fx: dict[str, float] | None = None):
        self._fx_cache: dict[str, float] = dict(fx or {})

    # ------------------------------------------------------------------ precios
    def history(self, symbol: str, start: date, end: date | None = None) -> pd.DataFrame:
        yf = _yf()
        try:
            df = yf.Ticker(symbol).history(start=start, end=end, auto_adjust=False)
        except Exception as e:  # yfinance lanza excepciones heterogéneas
            raise ProviderUnavailable(f"yahoo history {symbol}: {e}") from e
        if df is None or df.empty:
            raise ProviderUnavailable(f"yahoo history {symbol}: vacío")
        df = df.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close",
                                "Adj Close": "adj_close", "Volume": "volume"})
        df.index = pd.to_datetime(df.index).tz_localize(None).date
        return df[[c for c in ["open", "high", "low", "close", "adj_close", "volume"] if c in df.columns]]

    def fx_to_usd(self, currency: str) -> float:
        """Tipo de cambio spot moneda->USD (Yahoo). GBp (peniques) se maneja aparte."""
        if currency in ("USD", None):
            return 1.0
        if currency in self._fx_cache:
            return self._fx_cache[currency]
        df = self.history(f"{currency}USD=X", start=date.today() - pd.Timedelta(days=10))
        rate = float(df["close"].iloc[-1])
        self._fx_cache[currency] = rate
        return rate

    def snapshot(self, company: Company) -> MarketSnapshotData:
        yf = _yf()
        sym = company.yahoo_symbol
        try:
            t = yf.Ticker(sym)
            fi = t.fast_info
            price = float(fi["last_price"])
            currency = fi.get("currency") or company.currency
            shares = fi.get("shares")
            mcap = fi.get("market_cap")
        except Exception as e:
            raise ProviderUnavailable(f"yahoo snapshot {sym}: {e}") from e
        hist = self.history(sym, start=date.today() - pd.Timedelta(days=40))
        tail = hist.tail(20)
        scale = 0.01 if currency == "GBp" else 1.0            # LSE cotiza en peniques
        ccy = "GBP" if currency == "GBp" else currency
        fx = self.fx_to_usd(ccy)
        adv = float((tail["close"] * tail["volume"]).mean()) * scale * fx if len(tail) else None
        return MarketSnapshotData(
            company_id=company.id, as_of=datetime.now(timezone.utc), price=price * scale, currency=ccy,
            shares_outstanding=float(shares) if shares else None,
            market_cap=float(mcap) * scale if mcap else None,
            market_cap_usd=float(mcap) * scale * fx if mcap else None,
            adv_usd_20d=adv, source="yahoo")

    # ------------------------------------------------------------------ opciones
    def chain(self, symbol: str, max_expiries: int = 6) -> OptionChain:
        yf = _yf()
        now = datetime.now(timezone.utc)
        try:
            t = yf.Ticker(symbol)
            expiries = list(t.options)[:max_expiries]
            spot = float(t.fast_info["last_price"])
        except Exception as e:
            raise ProviderUnavailable(f"yahoo options {symbol}: {e}") from e
        quotes: list[OptionQuoteData] = []
        for exp in expiries:
            oc = t.option_chain(exp)
            for right, df in (("C", oc.calls), ("P", oc.puts)):
                for _, r in df.iterrows():
                    quotes.append(OptionQuoteData(
                        underlying=symbol, retrieved_at=now, expiry=date.fromisoformat(exp),
                        strike=float(r["strike"]), right=right,
                        bid=_f(r.get("bid")), ask=_f(r.get("ask")), last=_f(r.get("lastPrice")),
                        volume=_f(r.get("volume")), open_interest=_f(r.get("openInterest")),
                        implied_vol=_f(r.get("impliedVolatility")), underlying_price=spot, source="yahoo"))
        return OptionChain(underlying=symbol, retrieved_at=now, underlying_price=spot, quotes=quotes, source="yahoo")


def _f(x) -> float | None:  # noqa: ANN001
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v != v else v
