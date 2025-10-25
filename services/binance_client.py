import pandas as pd
import numpy as np
from binance.client import Client
import logging
from typing import Optional, Dict, List
from config.settings import TradingSettings

logger = logging.getLogger(__name__)

class BinanceClient:
    def __init__(self, api_key: str, api_secret: str):
        self.client = Client(api_key, api_secret)
        self.settings = TradingSettings()
        self._test_connection()
    
    def _test_connection(self):
        try:
            self.client.futures_time()
            logger.info("✅ اتصال Binance API نشط")
        except Exception as e:
            logger.error(f"❌ فشل الاتصال بـ Binance API: {e}")
            raise
    
    def get_price_data(self, symbol: str, interval: str = '15m', limit: int = 50) -> Optional[pd.DataFrame]:
        try:
            klines = self.client.futures_klines(
                symbol=symbol, 
                interval=interval, 
                limit=limit
            )
            
            df = pd.DataFrame(klines, columns=[
                'timestamp', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_asset_volume', 'number_of_trades',
                'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
            ])
            
            for col in ['open', 'high', 'low', 'close']:
                df[col] = pd.to_numeric(df[col])
            
            return df
        except Exception as e:
            logger.error(f"❌ خطأ في الحصول على بيانات السعر لـ {symbol}: {e}")
            return None
    
    def get_current_price(self, symbol: str) -> Optional[float]:
        try:
            ticker = self.client.futures_symbol_ticker(symbol=symbol)
            return float(ticker['price'])
        except Exception as e:
            logger.error(f"❌ خطأ في الحصول على سعر {symbol}: {e}")
            return None
    
    def get_active_positions(self) -> List[Dict]:
        try:
            positions = self.client.futures_account()['positions']
            active_positions = []
            
            for position in positions:
                symbol = position['symbol']
                position_amt = float(position['positionAmt'])
                
                if position_amt != 0 and symbol in self.settings.symbols:
                    active_positions.append({
                        'symbol': symbol,
                        'quantity': abs(position_amt),
                        'entry_price': float(position['entryPrice']),
                        'direction': 'LONG' if position_amt > 0 else 'SHORT',
                        'leverage': int(position['leverage']),
                        'unrealized_pnl': float(position['unrealizedProfit']),
                        'position_amt': position_amt
                    })
                    logger.info(f"✅ تم رصد صفقة نشطة: {symbol} | الاتجاه: {'LONG' if position_amt > 0 else 'SHORT'} | الكمية: {abs(position_amt)}")
            
            logger.info(f"✅ تم العثور على {len(active_positions)} صفقة نشطة")
            return active_positions
            
        except Exception as e:
            logger.error(f"❌ خطأ في الحصول على الصفقات من Binance: {e}")
            return []
    
    def close_position(self, symbol: str, quantity: float, direction: str, reduce_only: bool = True) -> bool:
        try:
            side = 'SELL' if direction == 'LONG' else 'BUY'
            
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type='MARKET',
                quantity=quantity,
                reduceOnly=reduce_only
            )
            
            if order:
                logger.info(f"✅ تم إغلاق {quantity:.6f} من {symbol}")
                return True
            return False
            
        except Exception as e:
            logger.error(f"❌ خطأ في إغلاق الصفقة {symbol}: {e}")
            return False
    
    def get_margin_info(self) -> Optional[Dict]:
        try:
            account_info = self.client.futures_account()
            total_wallet_balance = float(account_info['totalWalletBalance'])
            available_balance = float(account_info['availableBalance'])
            
            if total_wallet_balance > 0:
                margin_used = total_wallet_balance - available_balance
                margin_ratio = margin_used / total_wallet_balance
                
                return {
                    'total_wallet_balance': total_wallet_balance,
                    'available_balance': available_balance,
                    'margin_used': margin_used,
                    'margin_ratio': margin_ratio,
                    'is_risk_high': margin_ratio > 0.7  # Using default threshold
                }
            return None
            
        except Exception as e:
            logger.error(f"❌ خطأ في فحص الهامش: {e}")
            return None
