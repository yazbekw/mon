import pandas as pd
import numpy as np
import logging
from typing import Dict, Tuple
from config.settings import RiskSettings, TakeProfitSettings

logger = logging.getLogger(__name__)

class PriceCalculator:
    def __init__(self):
        self.risk_settings = RiskSettings()
        self.tp_settings = TakeProfitSettings()
    
    def calculate_atr(self, df: pd.DataFrame) -> pd.Series:
        try:
            high_low = df['high'] - df['low']
            high_close = np.abs(df['high'] - df['close'].shift())
            low_close = np.abs(df['low'] - df['close'].shift())
            
            true_range = np.maximum(high_low, np.maximum(high_close, low_close))
            atr = true_range.rolling(self.risk_settings.atr_period).mean()
            return atr
        except Exception as e:
            logger.error(f"❌ خطأ في حساب ATR: {e}")
            return pd.Series([df['close'].iloc[-1] * 0.01] * len(df))
    
    def calculate_support_resistance(self, df: pd.DataFrame) -> pd.DataFrame:
        try:
            df = df.copy()
            df['atr'] = self.calculate_atr(df)
            
            if df['atr'].isna().all() or df['atr'].iloc[-1] == 0:
                current_price = df['close'].iloc[-1]
                df['atr'] = current_price * 0.01
            
            df['resistance'] = df['high'].rolling(20, min_periods=1).max()
            df['support'] = df['low'].rolling(20, min_periods=1).min()
            
            df['resistance'].fillna(method='bfill', inplace=True)
            df['support'].fillna(method='bfill', inplace=True)
            df['atr'].fillna(method='bfill', inplace=True)
            
            return df
            
        except Exception as e:
            logger.error(f"❌ خطأ في حساب الدعم/المقاومة: {e}")
            return self._get_default_levels(df)
    
    def _get_default_levels(self, df: pd.DataFrame) -> pd.DataFrame:
        df_default = df.copy()
        current_price = df['close'].iloc[-1]
        df_default['atr'] = current_price * 0.01
        df_default['resistance'] = current_price * 1.02
        df_default['support'] = current_price * 0.98
        return df_default
    
    def calculate_stop_loss_levels(self, symbol: str, entry_price: float, direction: str, df: pd.DataFrame) -> Dict:
        try:
            df_with_levels = self.calculate_support_resistance(df)
            current_atr = df_with_levels['atr'].iloc[-1]
            
            if direction == 'LONG':
                support_level = df_with_levels['support'].iloc[-1]
                full_stop_loss = support_level - (current_atr * self.risk_settings.risk_ratio)
                partial_stop_loss = entry_price - ((entry_price - full_stop_loss) * self.risk_settings.partial_stop_ratio)
                
                # Apply min/max limits
                min_stop = entry_price * (1 - self.risk_settings.min_stop_loss_pct)
                max_stop = entry_price * (1 - self.risk_settings.max_stop_loss_pct)
                
                full_stop_loss = max(min(full_stop_loss, min_stop), max_stop)
                partial_stop_loss = entry_price - ((entry_price - full_stop_loss) * self.risk_settings.partial_stop_ratio)
                
            else:  # SHORT
                resistance_level = df_with_levels['resistance'].iloc[-1]
                full_stop_loss = resistance_level + (current_atr * self.risk_settings.risk_ratio)
                partial_stop_loss = entry_price + ((full_stop_loss - entry_price) * self.risk_settings.partial_stop_ratio)
                
                min_stop = entry_price * (1 + self.risk_settings.min_stop_loss_pct)
                max_stop = entry_price * (1 + self.risk_settings.max_stop_loss_pct)
                
                full_stop_loss = min(max(full_stop_loss, min_stop), max_stop)
                partial_stop_loss = entry_price + ((full_stop_loss - entry_price) * self.risk_settings.partial_stop_ratio)
            
            return {
                'partial_stop_loss': partial_stop_loss,
                'full_stop_loss': full_stop_loss
            }
            
        except Exception as e:
            logger.error(f"❌ خطأ في حساب وقف الخسارة لـ {symbol}: {e}")
            return self._get_default_stop_loss(entry_price, direction)
    
    def _get_default_stop_loss(self, entry_price: float, direction: str) -> Dict:
        if direction == 'LONG':
            min_stop = entry_price * (1 - self.risk_settings.min_stop_loss_pct)
            return {
                'partial_stop_loss': min_stop * 0.995,
                'full_stop_loss': min_stop
            }
        else:
            min_stop = entry_price * (1 + self.risk_settings.min_stop_loss_pct)
            return {
                'partial_stop_loss': min_stop * 1.005,
                'full_stop_loss': min_stop
            }
    
    def calculate_take_profit_levels(self, symbol: str, entry_price: float, direction: str, total_quantity: float, df: pd.DataFrame) -> Dict:
        try:
            current_atr = df['atr'].iloc[-1] if 'atr' in df.columns else 0
            current_close = df['close'].iloc[-1]
            
            take_profit_levels = {}
            
            for level, config in self.tp_settings.levels.items():
                base_target = config['target']
                
                if current_atr > 0 and current_close > 0:
                    atr_ratio = current_atr / current_close
                    volatility_factor = 1 + (atr_ratio * self.risk_settings.volatility_multiplier)
                    adjusted_target = base_target * volatility_factor
                else:
                    adjusted_target = base_target
                
                if direction == 'LONG':
                    tp_price = entry_price * (1 + adjusted_target)
                else:
                    tp_price = entry_price * (1 - adjusted_target)
                
                take_profit_levels[level] = {
                    'price': tp_price,
                    'target_percent': adjusted_target * 100,
                    'allocation': config['allocation'],
                    'quantity': total_quantity * config['allocation']
                }
            
            return take_profit_levels
            
        except Exception as e:
            logger.error(f"❌ خطأ في حساب جني الأرباح لـ {symbol}: {e}")
            return self._get_default_take_profit(entry_price, direction, total_quantity)
    
    def _get_default_take_profit(self, entry_price: float, direction: str, total_quantity: float) -> Dict:
        levels = {}
        for level, config in self.tp_settings.levels.items():
            if direction == 'LONG':
                tp_price = entry_price * (1 + config['target'])
            else:
                tp_price = entry_price * (1 - config['target'])
            
            levels[level] = {
                'price': tp_price,
                'target_percent': config['target'] * 100,
                'allocation': config['allocation'],
                'quantity': total_quantity * config['allocation']
            }
        return levels
