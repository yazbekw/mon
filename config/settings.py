import os
from dataclasses import dataclass
from typing import Dict, List
import pytz

@dataclass
class TradingSettings:
    symbols: List[str] = None
    base_trade_amount: float = 3
    leverage: int = 50
    max_simultaneous_trades: int = 1
    
    def __post_init__(self):
        if self.symbols is None:
            self.symbols = ["BNBUSDT", "ETHUSDT"]
    
    @property
    def position_size(self):
        return self.base_trade_amount * self.leverage

@dataclass
class RiskSettings:
    atr_period: int = 14
    risk_ratio: float = 0.5
    volatility_multiplier: float = 1.5
    margin_risk_threshold: float = 0.7
    position_reduction: float = 0.5
    partial_stop_ratio: float = 0.30
    full_stop_ratio: float = 1.0
    partial_close_ratio: float = 0.4
    min_stop_loss_pct: float = 0.015
    max_stop_loss_pct: float = 0.05

@dataclass
class TakeProfitSettings:
    levels: Dict = None
    
    def __post_init__(self):
        if self.levels is None:
            self.levels = {
                'LEVEL_1': {'target': 0.0025, 'allocation': 0.5},
                'LEVEL_2': {'target': 0.0030, 'allocation': 0.3},
                'LEVEL_3': {'target': 0.0035, 'allocation': 0.2}
            }

@dataclass
class AppSettings:
    damascus_tz = pytz.timezone('Asia/Damascus')
    check_interval: int = 10
    sync_interval: int = 300
    margin_check_interval: int = 60
    report_interval: int = 21600
