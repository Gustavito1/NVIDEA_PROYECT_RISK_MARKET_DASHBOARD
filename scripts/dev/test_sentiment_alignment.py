#!/usr/bin/env python
"""
Valida la alineacion de noticias con barras 1h (sin look-ahead).

1. Parser Alpha JSON -> articles / daily
2. Point-in-time: una noticia a las 10:45 afecta la barra de 11:00, NO la de 10:30
3. Leakage: borrar noticias futuras no cambia las features de la barra T
4. Fallback diario: el agregado del dia D solo aparece en el dia D+1

Uso: python scripts/dev/test_sentiment_alignment.py
"""
import json
import sys
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE_DIR))

import numpy as np
import pandas as pd

from core.data_providers.ibkr_intraday import get_latest_intradia_data
from core.news.alpha_vantage import (
    aggregate_daily,
    load_articles,
    daily_lag_features,
    rolling_sentiment_features,
)

NY = "America/New_York"


def _bars(dias: int = 10) -> pd.DataFrame:
    data = get_latest_intradia_data()
    df = data.get("NVDA_1H")
    if df is None or df.empty:
        fechas = pd.bdate_range("2026-06-01", periods=dias, tz=NY)
        rows = []
        for f in fechas:
            for h in range(9, 16):
                rows.append({"Datetime": pd.Timestamp(f.year, f.month, f.day, h,
                                                      30 if h == 9 else 0, tz=NY)})
        df = pd.DataFrame(rows)
        df["Datetime"] = df["Datetime"].dt.tz_convert("UTC")
    df = df.copy()
    df["Datetime"] = pd.to_datetime(df["Datetime"], utc=True)
    return df.sort_values("Datetime").head(70).reset_index(drop=True)


def test_parser():
    feed = {
        "feed": [
            {
                "title": "NVIDIA beats earnings",
                "url": "https://example.com/a1",
                "time_published": "20260610T144500",
                "ticker_sentiment": [
                    {"ticker": "NVDA", "relevance_score": "0.91",
                     "ticker_sentiment_score": "0.52",
                     "ticker_sentiment_label": "Bullish"},
                    {"ticker": "AMD", "relevance_score": "0.10",
                     "ticker_sentiment_score": "0.05",
                     "ticker_sentiment_label": "Neutral"},
                ],
            },
            {
                "title": "Chip export restrictions",
                "url": "https://example.com/a2",
                "time_published": "20260611T130000",
                "ticker_sentiment": [
                    {"ticker": "NVDA", "relevance_score": "0.80",
                     "ticker_sentiment_score": "-0.40",
                     "ticker_sentiment_label": "Bearish"},
                ],
            },
        ]
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(feed, f)
        path = Path(f.name)

    articles, daily = load_articles(path)
    assert articles is not None and len(articles) == 3, "parser JSON debe dar 3 filas ticker"
    assert daily is None
    assert articles["published_utc"].dt.tz is not None
    ag = aggregate_daily(articles)
    assert "sent_weighted" in ag.columns and len(ag) == 2
    print("[OK] parser Alpha JSON + agregado diario")


def test_point_in_time():
    bars = _bars()
    base_time = bars["Datetime"].iloc[20]

    arts = pd.DataFrame({
        "published_utc": [
            base_time - pd.Timedelta(hours=2),
            base_time + pd.Timedelta(minutes=15),
        ],
        "ticker": ["NVDA", "NVDA"],
        "relevance_score": [0.9, 0.9],
        "score": [0.5, -0.5],
    })
    arts["published_utc"] = pd.to_datetime(arts["published_utc"], utc=True)

    feats = rolling_sentiment_features(bars, arts, windows_h=(6, 24), half_life_h=12)
    idx = bars.index.get_loc(20)

    val_bar = feats.loc[idx, "sent_w6h"]
    val_next = feats.loc[idx + 1, "sent_w6h"] if idx + 1 < len(feats) else None
    assert abs(val_bar - 0.5) < 1e-9, f"barra T debe ver solo la noticia previa (vio {val_bar})"
    assert val_next is not None and val_next < 0.5, "barra T+1 debe incorporar la noticia nueva"
    print(f"[OK] point-in-time: barra T={val_bar:.3f}, barra T+1={val_next:.3f}")


def test_no_leakage_future():
    bars = _bars()
    rng = np.random.default_rng(7)
    n = 150
    times = bars["Datetime"].iloc[5] + pd.to_timedelta(
        rng.integers(0, 30 * 24 * 60, n), unit="m"
    )
    arts = pd.DataFrame({
        "published_utc": times,
        "ticker": "NVDA",
        "relevance_score": rng.uniform(0.3, 1.0, n),
        "score": rng.uniform(-1, 1, n),
    }).sort_values("published_utc")

    feats_all = rolling_sentiment_features(bars, arts, windows_h=(6, 24), half_life_h=12)

    t_check = bars["Datetime"].iloc[40]
    arts_past = arts[arts["published_utc"] < t_check]
    feats_past = rolling_sentiment_features(bars, arts_past, windows_h=(6, 24), half_life_h=12)

    cols = [c for c in feats_all.columns if c.startswith(("sent_", "cnt_", "hours_since"))]
    row_all = feats_all.loc[40, cols]
    row_past = feats_past.loc[40, cols]
    diff = (row_all - row_past).abs().max()
    assert diff < 1e-9, f"LEAKAGE: features de la barra 40 cambiaron al quitar futuro (diff={diff})"
    print(f"[OK] sin leakage: quitar noticias futuras no altera la barra (max diff {diff:.2e})")


def test_daily_lag():
    bars = _bars()
    daily = pd.DataFrame({
        "date": ["2026-06-10", "2026-06-11"],
        "sent_weighted": [0.8, -0.8],
        "sent_mean": [0.8, -0.8],
        "sent_count": [10, 12],
        "bull_ratio": [0.9, 0.1],
    })
    bars = bars.copy()
    bars["date_ny"] = pd.to_datetime(bars["Datetime"], utc=True).dt.tz_convert(NY).dt.date

    out = daily_lag_features(bars, daily)
    d10 = out[out["date_ny"] == pd.Timestamp("2026-06-10").date()]
    if not d10.empty:
        assert (d10["sent_prev_day"] == 0).all(), "el dia D no debe ver su propio agregado"
    d11 = out[out["date_ny"] == pd.Timestamp("2026-06-11").date()]
    if not d11.empty:
        assert (d11["sent_prev_day"] == 0.8).all(), "el dia D+1 debe ver el agregado de D"
    print("[OK] fallback diario con lag-1 (sin leakage)")


def main() -> int:
    print("=== Test alineacion sentimiento (day trading 1h) ===")
    test_parser()
    test_point_in_time()
    test_no_leakage_future()
    test_daily_lag()
    print("\n[OK] Todos los tests de alineacion pasaron")
    return 0


if __name__ == "__main__":
    sys.exit(main())
