"""
Modelos de datos para el sistema PRECIARIO_OIL.
"""
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional, List, Dict, Any
from enum import Enum


class PatternType(Enum):
    """Tipos de patrones detectados."""
    # Patrones de reversa
    HEAD_AND_SHOULDERS = "Head and Shoulders"
    HEAD_AND_SHOULDERS_INVERSE = "Head and Shoulders Inverse"
    DOUBLE_TOP = "Double Top"
    DOUBLE_BOTTOM = "Double Bottom"
    TRIPLE_TOP = "Triple Top"
    TRIPLE_BOTTOM = "Triple Bottom"
    ROUNDING_TOP = "Rounding Top"
    ROUNDING_BOTTOM = "Rounding Bottom"
    V_TOP = "V-Top"
    V_BOTTOM = "V-Bottom"
    DIAMOND_TOP = "Diamond Top"
    DIAMOND_BOTTOM = "Diamond Bottom"
    BROADENING_TOP = "Broadening Top"
    BROADENING_BOTTOM = "Broadening Bottom"
    WEDGE_RISING = "Rising Wedge"
    WEDGE_FALLING = "Falling Wedge"

    # Patrones de continuacion
    FLAG_BULLISH = "Bullish Flag"
    FLAG_BEARISH = "Bearish Flag"
    PENNANT = "Pennant"
    RECTANGLE_BULLISH = "Bullish Rectangle"
    RECTANGLE_BEARISH = "Bearish Rectangle"
    TRIANGLE_ASCENDING = "Ascending Triangle"
    TRIANGLE_DESCENDING = "Descending Triangle"
    TRIANGLE_SYMMETRICAL = "Symmetrical Triangle"
    CUP_AND_HANDLE = "Cup and Handle"

    # Candlestick patterns
    DOJI = "Doji"
    DOJI_DRAGONFLY = "Dragonfly Doji"
    DOJI_GRAVESTONE = "Gravestone Doji"
    DOJI_LONG_LEGGED = "Long-Legged Doji"
    ENGULFING_BULLISH = "Bullish Engulfing"
    ENGULFING_BEARISH = "Bearish Engulfing"
    HARAMI_BULLISH = "Bullish Harami"
    HARAMI_BEARISH = "Bearish Harami"
    HAMMER = "Hammer"
    HANGING_MAN = "Hanging Man"
    INVERTED_HAMMER = "Inverted Hammer"
    SHOOTING_STAR = "Shooting Star"
    MORNING_STAR = "Morning Star"
    EVENING_STAR = "Evening Star"
    THREE_WHITE_SOLDIERS = "Three White Soldiers"
    THREE_BLACK_CROWS = "Three Black Crows"
    PIERCING = "Piercing Line"
    DARK_CLOUD = "Dark Cloud Cover"
    TWEEZER_TOP = "Tweezer Top"
    TWEEZER_BOTTOM = "Tweezer Bottom"
    BELT_HOLD_BULLISH = "Bullish Belt Hold"
    BELT_HOLD_BEARISH = "Bearish Belt Hold"
    THREE_INSIDE_BULLISH = "Three Inside Up"
    THREE_INSIDE_BEARISH = "Three Inside Down"
    THREE_OUTSIDE_BULLISH = "Three Outside Up"
    THREE_OUTSIDE_BEARISH = "Three Outside Down"

    # Volume patterns
    VOLUME_CLIMAX = "Volume Climax"
    LOW_VOLUME_TEST = "Low Volume Test"
    VOLUME_DIVERGENCE = "Volume Divergence"
    OBV_CONFIRMATION = "OBV Confirmation"
    OBV_REVERSAL = "OBV Reversal"


class SignalType(Enum):
    """Senal generada por el patron."""
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


@dataclass
class PatternResult:
    """Resultado de deteccion de patron."""
    pattern: PatternType
    signal: SignalType
    confidence: float  # 0.0 a 1.0
    start_idx: int
    end_idx: int
    description: str
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class OHLCBar:
    """Barra OHLCV."""
    datetime: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def body_size(self) -> float:
        return abs(self.close - self.open)

    @property
    def upper_shadow(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_shadow(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def range_size(self) -> float:
        return self.high - self.low


@dataclass
class NewsImpactResult:
    """Resultado del impacto de noticias en el precio."""
    net_adjustment: float  # Porcentaje de ajuste (-0.05 a +0.05)
    news_items: List[Dict[str, Any]]  # Lista de noticias consideradas
    impact_high_count: int  # Noticias de alto impacto
    impact_medium_count: int  # Noticias de medio impacto
    direction: str  # 'UP', 'DOWN', o 'NEUTRAL'
    confidence: float  # Confianza del ajuste
    # Campos opcionales para psicología del mercado
    market_state: Optional[str] = None  # 'NORMAL', 'CONFLICT', 'POST_CONFLICT'
    days_of_conflict: Optional[int] = None  # Días de conflicto
    residual_premium: Optional[float] = None  # Premium residual post-conflicto


@dataclass
class PreciarioResult:
    """Resultado del calculo de preciario."""
    asset: str
    calculation_date: date
    preciario: float  # Precio base sin ajuste
    closes_used: List[float]
    dates_used: List[date]
    is_prediction: bool  # True si es viernes (antes de martes)
    trading_days_available: int
    confidence: float
    # Ajuste por noticias
    news_adjustment: Optional[float] = None  # Porcentaje de ajuste
    preciario_adjusted: Optional[float] = None  # Precio ajustado por noticias
    news_impact: Optional[NewsImpactResult] = None  # Detalles del impacto
    # Ajustes adicionales
    residual_premium: Optional[float] = None  # Premium por cicatrices geopoliticas
    chokepoint_premium: Optional[float] = None  # Premium por chokepoint critico
    demand_destruction: Optional[float] = None  # Ajuste por destruccion de demanda
    # Nuevos campos para validación de divergencia
    checkpoint_status: Optional[str] = None  # 'INTACT', 'BROKEN', 'UNKNOWN'
    checkpoint_level: Optional[float] = None  # Nivel del checkpoint crítico
    price_action_trend: Optional[str] = None  # 'BULLISH', 'BEARISH', 'NEUTRAL'
    divergence_detected: Optional[bool] = None  # True si hay divergencia
    divergence_type: Optional[str] = None  # 'BULL_TRAP', 'BEAR_TRAP', 'NONE'
    divergence_report: Optional[str] = None  # Reporte de divergencia


@dataclass
class AnalysisResult:
    """Resultado completo del analisis."""
    asset: str
    data_range: tuple  # (start_date, end_date)
    total_bars: int
    preciario: Optional[PreciarioResult]
    patterns: List[PatternResult]
    peak_volume_hour: Optional[int]
    summary: str
    # Datos OHLCV del ultimo dia
    latest_ohlc: Optional[Dict[str, float]] = None  # {'open', 'high', 'low', 'close', 'volume', 'close_prev', 'daily_var', 'daily_var_prev', 'intraday_var'}