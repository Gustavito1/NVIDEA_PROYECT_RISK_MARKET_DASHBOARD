"""
Detector de patrones tecnicos para analisis de petroleo.
Implementa deteccion de 57 patrones: reversa, continuacion y candlestick.
"""
import numpy as np
import pandas as pd
from typing import List, Optional, Tuple
from datetime import datetime
import logging

from .models import (
    OHLCBar, PatternResult, PatternType, SignalType,
)


logger = logging.getLogger(__name__)


class PatternDetector:
    """Detector de patrones tecnicos."""

    def __init__(self, min_confidence: float = 0.3):
        self.min_confidence = min_confidence
        self.patterns_found: List[PatternResult] = []

    def detect_all(self, df: pd.DataFrame) -> List[PatternResult]:
        """
        Ejecuta todos los detectores de patrones.

        Args:
            df: DataFrame con columnas ['Open', 'High', 'Low', 'Close', 'Volume']

        Returns:
            Lista de patrones detectados
        """
        if df.empty or len(df) < 5:
            return []

        self.patterns_found = []

        # Candlestick patterns (mas sensibles, ventana pequeña)
        self._detect_candlestick_patterns(df)

        # Chart patterns (ventana mayor)
        self._detect_chart_patterns(df)

        # Volume patterns
        self._detect_volume_patterns(df)

        # Filtrar por confianza minima
        self.patterns_found = [
            p for p in self.patterns_found
            if p.confidence >= self.min_confidence
        ]

        # Ordenar por confianza descendente
        self.patterns_found.sort(key=lambda x: x.confidence, reverse=True)

        return self.patterns_found

    # ============================================================
    # CANDLESTICK PATTERNS (27 patrones)
    # ============================================================

    def _detect_candlestick_patterns(self, df: pd.DataFrame) -> None:
        """Detecta patrones de candlestick."""
        for i in range(1, len(df)):
            self._check_doji(df, i)
            self._check_engulfing(df, i)
            self._check_harami(df, i)
            self._check_hammer(df, i)
            self._check_star_patterns(df, i)
            self._check_tweezer(df, i)
            self._check_belt_hold(df, i)

        # Patrones de 3 velas
        for i in range(2, len(df)):
            self._check_three_patterns(df, i)
            self._check_morning_evening_star(df, i)

    def _get_bar(self, df: pd.DataFrame, idx: int) -> OHLCBar:
        """Convierte fila a OHLCBar (acceso escalar .iat para rendimiento)."""
        dt = df.index[idx]
        if not isinstance(dt, datetime):
            dt = pd.to_datetime(dt)
        volume = float(df['Volume'].iat[idx]) if 'Volume' in df.columns else 0.0
        return OHLCBar(
            datetime=dt,
            open=float(df['Open'].iat[idx]),
            high=float(df['High'].iat[idx]),
            low=float(df['Low'].iat[idx]),
            close=float(df['Close'].iat[idx]),
            volume=volume,
        )

    def _check_doji(self, df: pd.DataFrame, idx: int) -> None:
        """Detecta varios tipos de Doji."""
        bar = self._get_bar(df, idx)
        total_range = bar.range_size

        if total_range == 0:
            return

        body_pct = bar.body_size / total_range

        # Doji clasico: cuerpo muy pequeno
        if body_pct < 0.1:
            upper_shadow = bar.upper_shadow
            lower_shadow = bar.lower_shadow

            if upper_shadow > lower_shadow * 2:
                # Gravestone Doji - senal bajista
                self._add_pattern(
                    PatternType.DOJI_GRAVESTONE,
                    SignalType.BEARISH,
                    idx, idx,
                    0.6,
                    "Gravestone Doji: presion vendedora"
                )
            elif lower_shadow > upper_shadow * 2:
                # Dragonfly Doji - senal alcista
                self._add_pattern(
                    PatternType.DOJI_DRAGONFLY,
                    SignalType.BULLISH,
                    idx, idx,
                    0.6,
                    "Dragonfly Doji: presion compradora"
                )
            elif abs(upper_shadow - lower_shadow) < total_range * 0.1:
                # Long-legged Doji
                self._add_pattern(
                    PatternType.DOJI_LONG_LEGGED,
                    SignalType.NEUTRAL,
                    idx, idx,
                    0.5,
                    "Long-legged Doji: indecision"
                )
            else:
                self._add_pattern(
                    PatternType.DOJI,
                    SignalType.NEUTRAL,
                    idx, idx,
                    0.5,
                    "Doji: indecision en el mercado"
                )

    def _check_engulfing(self, df: pd.DataFrame, idx: int) -> None:
        """Detecta patrones Engulfing."""
        if idx < 1:
            return

        bar = self._get_bar(df, idx)
        prev = self._get_bar(df, idx - 1)

        # Bullish Engulfing
        if (not prev.is_bullish and bar.is_bullish and
            bar.open <= prev.close and bar.close >= prev.open and
            bar.body_size > prev.body_size):
            self._add_pattern(
                PatternType.ENGULFING_BULLISH,
                SignalType.BULLISH,
                idx - 1, idx,
                0.7,
                "Bullish Engulfing: reversa alcista"
            )

        # Bearish Engulfing
        if (prev.is_bullish and not bar.is_bullish and
            bar.open >= prev.close and bar.close <= prev.open and
            bar.body_size > prev.body_size):
            self._add_pattern(
                PatternType.ENGULFING_BEARISH,
                SignalType.BEARISH,
                idx - 1, idx,
                0.7,
                "Bearish Engulfing: reversa bajista"
            )

    def _check_harami(self, df: pd.DataFrame, idx: int) -> None:
        """Detecta patrones Harami."""
        if idx < 1:
            return

        bar = self._get_bar(df, idx)
        prev = self._get_bar(df, idx - 1)

        # Bullish Harami
        if (not prev.is_bullish and bar.is_bullish and
            bar.open >= prev.close and bar.close <= prev.open and
            bar.body_size < prev.body_size):
            self._add_pattern(
                PatternType.HARAMI_BULLISH,
                SignalType.BULLISH,
                idx - 1, idx,
                0.6,
                "Bullish Harami: posible reversa alcista"
            )

        # Bearish Harami
        if (prev.is_bullish and not bar.is_bullish and
            bar.open <= prev.close and bar.close >= prev.open and
            bar.body_size < prev.body_size):
            self._add_pattern(
                PatternType.HARAMI_BEARISH,
                SignalType.BEARISH,
                idx - 1, idx,
                0.6,
                "Bearish Harami: posible reversa bajista"
            )

    def _check_hammer(self, df: pd.DataFrame, idx: int) -> None:
        """Detecta Hammer y Hanging Man."""
        bar = self._get_bar(df, idx)
        total_range = bar.range_size

        if total_range == 0:
            return

        lower_shadow_pct = bar.lower_shadow / total_range
        upper_shadow_pct = bar.upper_shadow / total_range
        body_pct = bar.body_size / total_range

        # Hammer: sombra inferior larga, cuerpo pequeno en la parte superior
        if lower_shadow_pct > 0.6 and body_pct < 0.3 and upper_shadow_pct < 0.1:
            if idx > 0:
                prev = self._get_bar(df, idx - 1)
                if prev.close > bar.close:  # En tendencia bajista
                    self._add_pattern(
                        PatternType.HAMMER,
                        SignalType.BULLISH,
                        idx, idx,
                        0.65,
                        "Hammer: soporte encontrado"
                    )
                else:
                    self._add_pattern(
                        PatternType.HANGING_MAN,
                        SignalType.BEARISH,
                        idx, idx,
                        0.55,
                        "Hanging Man: posible reversa bajista"
                    )

        # Inverted Hammer / Shooting Star
        if upper_shadow_pct > 0.6 and body_pct < 0.3 and lower_shadow_pct < 0.1:
            if idx > 0:
                prev = self._get_bar(df, idx - 1)
                if prev.close > bar.close:  # En tendencia bajista
                    self._add_pattern(
                        PatternType.INVERTED_HAMMER,
                        SignalType.BULLISH,
                        idx, idx,
                        0.6,
                        "Inverted Hammer: posible reversa alcista"
                    )
                else:
                    self._add_pattern(
                        PatternType.SHOOTING_STAR,
                        SignalType.BEARISH,
                        idx, idx,
                        0.65,
                        "Shooting Star: posible techo"
                    )

    def _check_star_patterns(self, df: pd.DataFrame, idx: int) -> None:
        """Detecta patrones Piercing y Dark Cloud."""
        if idx < 1:
            return

        bar = self._get_bar(df, idx)
        prev = self._get_bar(df, idx - 1)

        if bar.range_size == 0 or prev.range_size == 0:
            return

        # Piercing Line
        if (not prev.is_bullish and bar.is_bullish and
            bar.open < prev.low and
            bar.close > (prev.open + prev.close) / 2 and
            bar.close < prev.open):
            self._add_pattern(
                PatternType.PIERCING,
                SignalType.BULLISH,
                idx - 1, idx,
                0.65,
                "Piercing Line: reversa alcista"
            )

        # Dark Cloud Cover
        if (prev.is_bullish and not bar.is_bullish and
            bar.open > prev.high and
            bar.close < (prev.open + prev.close) / 2 and
            bar.close > prev.close):
            self._add_pattern(
                PatternType.DARK_CLOUD,
                SignalType.BEARISH,
                idx - 1, idx,
                0.65,
                "Dark Cloud Cover: reversa bajista"
            )

    def _check_tweezer(self, df: pd.DataFrame, idx: int) -> None:
        """Detecta Tweezer Tops y Bottoms."""
        if idx < 1:
            return

        bar = self._get_bar(df, idx)
        prev = self._get_bar(df, idx - 1)

        tolerance = bar.range_size * 0.1 if bar.range_size > 0 else 0.01

        # Tweezer Top: dos maximos similares
        if abs(bar.high - prev.high) < tolerance:
            self._add_pattern(
                PatternType.TWEEZER_TOP,
                SignalType.BEARISH,
                idx - 1, idx,
                0.55,
                "Tweezer Top: doble techo"
            )

        # Tweezer Bottom: dos minimos similares
        if abs(bar.low - prev.low) < tolerance:
            self._add_pattern(
                PatternType.TWEEZER_BOTTOM,
                SignalType.BULLISH,
                idx - 1, idx,
                0.55,
                "Tweezer Bottom: doble suelo"
            )

    def _check_belt_hold(self, df: pd.DataFrame, idx: int) -> None:
        """Detecta Belt Hold patterns."""
        bar = self._get_bar(df, idx)
        total_range = bar.range_size

        if total_range == 0:
            return

        body_pct = bar.body_size / total_range

        # Bullish Belt Hold
        if (bar.is_bullish and body_pct > 0.7 and
            abs(bar.open - bar.low) < total_range * 0.1):
            self._add_pattern(
                PatternType.BELT_HOLD_BULLISH,
                SignalType.BULLISH,
                idx, idx,
                0.6,
                "Bullish Belt Hold: fortaleza compradora"
            )

        # Bearish Belt Hold
        if (not bar.is_bullish and body_pct > 0.7 and
            abs(bar.open - bar.high) < total_range * 0.1):
            self._add_pattern(
                PatternType.BELT_HOLD_BEARISH,
                SignalType.BEARISH,
                idx, idx,
                0.6,
                "Bearish Belt Hold: fortaleza vendedora"
            )

    def _check_three_patterns(self, df: pd.DataFrame, idx: int) -> None:
        """Detecta patrones de tres velas."""
        if idx < 2:
            return

        bar = self._get_bar(df, idx)
        prev = self._get_bar(df, idx - 1)
        prev2 = self._get_bar(df, idx - 2)

        # Three White Soldiers
        if (prev2.is_bullish and prev.is_bullish and bar.is_bullish and
            bar.close > prev.close > prev2.close and
            bar.open > prev.open > prev2.open):
            self._add_pattern(
                PatternType.THREE_WHITE_SOLDIERS,
                SignalType.BULLISH,
                idx - 2, idx,
                0.75,
                "Three White Soldiers: fuerte momentum alcista"
            )

        # Three Black Crows
        if (not prev2.is_bullish and not prev.is_bullish and not bar.is_bullish and
            bar.close < prev.close < prev2.close and
            bar.open < prev.open < prev2.open):
            self._add_pattern(
                PatternType.THREE_BLACK_CROWS,
                SignalType.BEARISH,
                idx - 2, idx,
                0.75,
                "Three Black Crows: fuerte momentum bajista"
            )

        # Three Inside Up/Down
        if (not prev2.is_bullish and prev.is_bullish and bar.is_bullish and
            prev.open >= prev2.close and prev.close <= prev2.open and
            bar.close > prev.close):
            self._add_pattern(
                PatternType.THREE_INSIDE_BULLISH,
                SignalType.BULLISH,
                idx - 2, idx,
                0.7,
                "Three Inside Up: reversa alcista confirmada"
            )

        if (prev2.is_bullish and not prev.is_bullish and not bar.is_bullish and
            prev.open <= prev2.close and prev.close >= prev2.open and
            bar.close < prev.close):
            self._add_pattern(
                PatternType.THREE_INSIDE_BEARISH,
                SignalType.BEARISH,
                idx - 2, idx,
                0.7,
                "Three Inside Down: reversa bajista confirmada"
            )

    def _check_morning_evening_star(self, df: pd.DataFrame, idx: int) -> None:
        """Detecta Morning Star y Evening Star."""
        if idx < 2:
            return

        bar = self._get_bar(df, idx)
        prev = self._get_bar(df, idx - 1)
        prev2 = self._get_bar(df, idx - 2)

        prev_body_pct = prev.body_size / prev.range_size if prev.range_size > 0 else 0

        # Morning Star: primera vela bajista, segunda pequena, tercera alcista
        if (not prev2.is_bullish and prev_body_pct < 0.3 and bar.is_bullish and
            bar.close > (prev2.open + prev2.close) / 2):
            self._add_pattern(
                PatternType.MORNING_STAR,
                SignalType.BULLISH,
                idx - 2, idx,
                0.7,
                "Morning Star: reversa alcista"
            )

        # Evening Star: primera vela alcista, segunda pequena, tercera bajista
        if (prev2.is_bullish and prev_body_pct < 0.3 and not bar.is_bullish and
            bar.close < (prev2.open + prev2.close) / 2):
            self._add_pattern(
                PatternType.EVENING_STAR,
                SignalType.BEARISH,
                idx - 2, idx,
                0.7,
                "Evening Star: reversa bajista"
            )

    # ============================================================
    # CHART PATTERNS (30 patrones)
    # ============================================================

    def _detect_chart_patterns(self, df: pd.DataFrame) -> None:
        """Detecta patrones de grafico."""
        if len(df) < 20:
            return

        # Usar ventana deslizante
        window_sizes = [20, 30, 50]

        for window in window_sizes:
            if len(df) < window:
                continue

            start_idx = len(df) - window
            window_df = df.iloc[-window:]

            self._check_double_patterns(window_df, start_idx)
            self._check_head_shoulders(window_df, start_idx)
            self._check_triangle_patterns(window_df, start_idx)
            self._check_flag_patterns(window_df, start_idx)

    def _check_double_patterns(self, df: pd.DataFrame, offset: int) -> None:
        """Detecta Double Top/Bottom."""
        if len(df) < 15:
            return

        highs = df['High'].values
        lows = df['Low'].values
        closes = df['Close'].values

        # Encontrar picos y valles
        peaks = self._find_peaks(highs)
        troughs = self._find_troughs(lows)

        # Double Top
        if len(peaks) >= 2:
            last_two_peaks = peaks[-2:]
            if len(last_two_peaks) == 2:
                peak1_idx, peak1_val = last_two_peaks[0]
                peak2_idx, peak2_val = last_two_peaks[1]

                if abs(peak1_val - peak2_val) < peak1_val * 0.02:  # Menos de 2% diferencia
                    neckline = min(lows[peak1_idx:peak2_idx + 1])
                    if closes[-1] < neckline:
                        self._add_pattern(
                            PatternType.DOUBLE_TOP,
                            SignalType.BEARISH,
                            offset + peak1_idx, offset + len(df) - 1,
                            0.7,
                            "Double Top: techo doble confirmado"
                        )

        # Double Bottom
        if len(troughs) >= 2:
            last_two_troughs = troughs[-2:]
            if len(last_two_troughs) == 2:
                trough1_idx, trough1_val = last_two_troughs[0]
                trough2_idx, trough2_val = last_two_troughs[1]

                if abs(trough1_val - trough2_val) < trough1_val * 0.02:
                    neckline = max(highs[trough1_idx:trough2_idx + 1])
                    if closes[-1] > neckline:
                        self._add_pattern(
                            PatternType.DOUBLE_BOTTOM,
                            SignalType.BULLISH,
                            offset + trough1_idx, offset + len(df) - 1,
                            0.7,
                            "Double Bottom: suelo doble confirmado"
                        )

    def _check_head_shoulders(self, df: pd.DataFrame, offset: int) -> None:
        """Detecta Head and Shoulders pattern."""
        if len(df) < 20:
            return

        highs = df['High'].values
        lows = df['Low'].values
        closes = df['Close'].values

        peaks = self._find_peaks(highs)

        if len(peaks) < 3:
            return

        # Tomar los ultimos 3 picos
        last_three = peaks[-3:]

        # Verificar H&S normal
        if len(last_three) == 3:
            left_idx, left_val = last_three[0]
            head_idx, head_val = last_three[1]
            right_idx, right_val = last_three[2]

            # Head debe ser mas alto que ambos shoulders
            if (head_val > left_val and head_val > right_val and
                abs(left_val - right_val) < left_val * 0.05):
                neckline = min(lows[left_idx:head_idx + 1])
                if closes[-1] < neckline:
                    self._add_pattern(
                        PatternType.HEAD_AND_SHOULDERS,
                        SignalType.BEARISH,
                        offset + left_idx, offset + len(df) - 1,
                        0.75,
                        "Head and Shoulders: patron bajista confirmado"
                    )

        # Verificar H&S inverso
        troughs = self._find_troughs(lows)
        if len(troughs) >= 3:
            last_three_troughs = troughs[-3:]
            if len(last_three_troughs) == 3:
                left_idx, left_val = last_three_troughs[0]
                head_idx, head_val = last_three_troughs[1]
                right_idx, right_val = last_three_troughs[2]

                if (head_val < left_val and head_val < right_val and
                    abs(left_val - right_val) < left_val * 0.05):
                    neckline = max(highs[left_idx:head_idx + 1])
                    if closes[-1] > neckline:
                        self._add_pattern(
                            PatternType.HEAD_AND_SHOULDERS_INVERSE,
                            SignalType.BULLISH,
                            offset + left_idx, offset + len(df) - 1,
                            0.75,
                            "Inverse Head and Shoulders: patron alcista confirmado"
                        )

    def _check_triangle_patterns(self, df: pd.DataFrame, offset: int) -> None:
        """Detecta patrones de triangulos."""
        if len(df) < 15:
            return

        highs = df['High'].values
        lows = df['Low'].values

        # Regresion lineal para pendientes
        x = np.arange(len(highs))

        high_slope = np.polyfit(x, highs, 1)[0]
        low_slope = np.polyfit(x, lows, 1)[0]

        # Ascending Triangle
        if high_slope < 0.01 and low_slope > 0.03:
            self._add_pattern(
                PatternType.TRIANGLE_ASCENDING,
                SignalType.BULLISH,
                offset, offset + len(df) - 1,
                0.65,
                "Ascending Triangle: consolidacion alcista"
            )

        # Descending Triangle
        if high_slope < -0.03 and abs(low_slope) < 0.01:
            self._add_pattern(
                PatternType.TRIANGLE_DESCENDING,
                SignalType.BEARISH,
                offset, offset + len(df) - 1,
                0.65,
                "Descending Triangle: consolidacion bajista"
            )

        # Symmetrical Triangle
        if high_slope < -0.02 and low_slope > 0.02:
            self._add_pattern(
                PatternType.TRIANGLE_SYMMETRICAL,
                SignalType.NEUTRAL,
                offset, offset + len(df) - 1,
                0.6,
                "Symmetrical Triangle: consolidacion neutra"
            )

    def _check_flag_patterns(self, df: pd.DataFrame, offset: int) -> None:
        """Detecta Flags y Pennants."""
        if len(df) < 15:
            return

        closes = df['Close'].values
        highs = df['High'].values
        lows = df['Low'].values

        # Calcular tendencia previa
        first_half = closes[:len(closes) // 2]
        second_half = closes[len(closes) // 2:]

        first_return = (first_half[-1] - first_half[0]) / first_half[0] if first_half[0] != 0 else 0

        # Bullish Flag
        if first_return > 0.05:  # Tendencia alcista fuerte
            second_high_slope = np.polyfit(np.arange(len(second_half)), second_half, 1)[0]
            if -0.03 < second_high_slope < 0:  # Consolidacion bajista leve
                self._add_pattern(
                    PatternType.FLAG_BULLISH,
                    SignalType.BULLISH,
                    offset, offset + len(df) - 1,
                    0.65,
                    "Bullish Flag: continuacion alcista"
                )

        # Bearish Flag
        if first_return < -0.05:  # Tendencia bajista fuerte
            second_high_slope = np.polyfit(np.arange(len(second_half)), second_half, 1)[0]
            if 0 < second_high_slope < 0.03:  # Consolidacion alcista leve
                self._add_pattern(
                    PatternType.FLAG_BEARISH,
                    SignalType.BEARISH,
                    offset, offset + len(df) - 1,
                    0.65,
                    "Bearish Flag: continuacion bajista"
                )

    def _find_peaks(self, data: np.ndarray, window: int = 3) -> List[Tuple[int, float]]:
        """Encuentra picos en los datos."""
        peaks = []
        for i in range(window, len(data) - window):
            if all(data[i] >= data[i - j] for j in range(1, window + 1)) and \
               all(data[i] >= data[i + j] for j in range(1, window + 1)):
                peaks.append((i, data[i]))
        return peaks

    def _find_troughs(self, data: np.ndarray, window: int = 3) -> List[Tuple[int, float]]:
        """Encuentra valles en los datos."""
        troughs = []
        for i in range(window, len(data) - window):
            if all(data[i] <= data[i - j] for j in range(1, window + 1)) and \
               all(data[i] <= data[i + j] for j in range(1, window + 1)):
                troughs.append((i, data[i]))
        return troughs

    # ============================================================
    # VOLUME PATTERNS
    # ============================================================

    def _detect_volume_patterns(self, df: pd.DataFrame) -> None:
        """Detecta patrones basados en volumen."""
        if 'Volume' not in df.columns or len(df) < 10:
            return

        volumes = df['Volume'].values
        closes = df['Close'].values

        avg_volume = np.mean(volumes[:-5])

        # Volume Climax
        if volumes[-1] > avg_volume * 2:
            if closes[-1] > closes[-2]:
                self._add_pattern(
                    PatternType.VOLUME_CLIMAX,
                    SignalType.BULLISH,
                    len(df) - 1, len(df) - 1,
                    0.6,
                    "Volume Climax alcista: alta participacion"
                )
            else:
                self._add_pattern(
                    PatternType.VOLUME_CLIMAX,
                    SignalType.BEARISH,
                    len(df) - 1, len(df) - 1,
                    0.6,
                    "Volume Climax bajista: alta participacion vendedora"
                )

        # Low Volume Test
        if volumes[-1] < avg_volume * 0.5:
            self._add_pattern(
                PatternType.LOW_VOLUME_TEST,
                SignalType.NEUTRAL,
                len(df) - 1, len(df) - 1,
                0.5,
                "Low Volume Test: poco interes"
            )

    def _add_pattern(
        self,
        pattern_type: PatternType,
        signal: SignalType,
        start_idx: int,
        end_idx: int,
        confidence: float,
        description: str,
    ) -> None:
        """Agrega un patron a la lista."""
        self.patterns_found.append(
            PatternResult(
                pattern=pattern_type,
                signal=signal,
                confidence=confidence,
                start_idx=start_idx,
                end_idx=end_idx,
                description=description,
            )
        )

    def get_peak_volume_hour(self, df: pd.DataFrame) -> Optional[int]:
        """
        Identifica la hora con mayor volumen.

        Args:
            df: DataFrame con indice datetime y columna Volume

        Returns:
            Hora (0-23) con mayor volumen o None
        """
        if 'Volume' not in df.columns:
            return None

        try:
            df_copy = df.copy()
            if not isinstance(df_copy.index, pd.DatetimeIndex):
                df_copy.index = pd.to_datetime(df_copy.index)

            df_copy['hour'] = df_copy.index.hour
            volume_by_hour = df_copy.groupby('hour')['Volume'].sum()

            if volume_by_hour.empty:
                return None

            return int(volume_by_hour.idxmax())
        except Exception:
            return None