import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import aiohttp
import pandas as pd
from binance import AsyncClient, BinanceSocketManager
from binance.exceptions import BinanceAPIException

logger = logging.getLogger(__name__)

class BinanceEngine:
    """
    🔄 محرك Binance - مسؤول عن جميع الاتصالات الخارجية مع Binance
    """
    
    def __init__(self, config: dict):
        self.config = config
        self.client: Optional[AsyncClient] = None
        self.socket_manager: Optional[BinanceSocketManager] = None
        self.session: Optional[aiohttp.ClientSession] = None
        self.last_api_call = 0
        self.min_api_interval = 0.1  # 100ms بين المكالمات لتجنب rate limits
        
    async def initialize(self):
        """تهيئة اتصال Binance"""
        try:
            logger.info("🔗 تهيئة اتصال Binance...")
            
            self.client = await AsyncClient.create(
                api_key=self.config.get('api_key', ''),
                api_secret=self.config.get('api_secret', ''),
                testnet=self.config.get('testnet', True)
            )
            
            self.socket_manager = BinanceSocketManager(self.client)
            self.session = aiohttp.ClientSession()
            
            logger.info("✅ تم تهيئة اتصال Binance بنجاح")
            return True
            
        except Exception as e:
            logger.error(f"❌ فشل تهيئة اتصال Binance: {e}")
            return False
    
    async def close(self):
        """إغلاق الاتصالات"""
        try:
            if self.client:
                await self.client.close_connection()
            if self.session:
                await self.session.close()
            logger.info("🔌 تم إغلاق اتصالات Binance")
        except Exception as e:
            logger.error(f"❌ خطأ في إغلاق الاتصالات: {e}")
    
    async def _rate_limit(self):
        """التحكم في معدل الاستعلامات للالتزام ب rate limits"""
        now = time.time()
        elapsed = now - self.last_api_call
        if elapsed < self.min_api_interval:
            await asyncio.sleep(self.min_api_interval - elapsed)
        self.last_api_call = time.time()
    
    async def get_open_positions(self) -> List[Dict]:
        """
        جلب جميع الصفقات المفتوحة في Futures
        """
        try:
            await self._rate_limit()
            
            # جلب معلومات الحساب
            account_info = await self.client.futures_account()
            
            positions = []
            for position in account_info['positions']:
                symbol = position['symbol']
                position_amt = float(position['positionAmt'])
                
                # تجاهل العملات بدون صفقات مفتوحة
                if position_amt == 0:
                    continue
                
                # جلب معلومات الصفقة
                position_info = {
                    'symbol': symbol,
                    'quantity': abs(position_amt),
                    'side': 'LONG' if position_amt > 0 else 'SHORT',
                    'entry_price': float(position['entryPrice']),
                    'leverage': int(position['leverage']),
                    'unrealized_pnl': float(position['unRealizedProfit']),
                    'liquidation_price': float(position['liquidationPrice']),
                    'update_time': datetime.now()
                }
                
                positions.append(position_info)
            
            logger.debug(f"📊 جلب {len(positions)} صفقة مفتوحة")
            return positions
            
        except BinanceAPIException as e:
            logger.error(f"❌ خطأ Binance في جلب الصفقات: {e}")
            return []
        except Exception as e:
            logger.error(f"❌ خطأ غير متوقع في جلب الصفقات: {e}")
            return []
    
    async def get_current_price(self, symbol: str) -> float:
        """
        جلب السعر الحالي للرمز
        """
        try:
            await self._rate_limit()
            
            ticker = await self.client.futures_symbol_ticker(symbol=symbol)
            price = float(ticker['price'])
            
            logger.debug(f"💰 سعر {symbol}: {price}")
            return price
            
        except BinanceAPIException as e:
            logger.error(f"❌ خطأ Binance في جلب سعر {symbol}: {e}")
            raise
        except Exception as e:
            logger.error(f"❌ خطأ غير متوقع في جلب سعر {symbol}: {e}")
            raise
    
    async def close_position(self, symbol: str, quantity: float, reason: str = "MANAGEMENT") -> Dict:
        """
        إغلاق جزء من الصفقة
        """
        try:
            await self._rate_limit()
            
            # جلب معلومات الصفقة الحالية لتحديد الجانب
            positions = await self.get_open_positions()
            position = next((p for p in positions if p['symbol'] == symbol), None)
            
            if not position:
                return {
                    'success': False,
                    'error': f'لا توجد صفقة مفتوحة للرمز {symbol}',
                    'order_id': None
                }
            
            # تحديد اتجاه أمر الإغلاق
            side = 'SELL' if position['side'] == 'LONG' else 'BUY'
            
            # التأكد من أن الكمية لا تتجاوز الكمية المفتوحة
            close_quantity = min(quantity, position['quantity'])
            
            # تنفيذ أمر الإغلاق
            order = await self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type='MARKET',
                quantity=close_quantity,
                reduceOnly=True
            )
            
            result = {
                'success': True,
                'order_id': order['orderId'],
                'symbol': symbol,
                'quantity': close_quantity,
                'side': side,
                'reason': reason,
                'timestamp': datetime.now()
            }
            
            logger.info(f"✅ تم إغلاق {close_quantity} من {symbol} - السبب: {reason}")
            return result
            
        except BinanceAPIException as e:
            error_msg = f"❌ خطأ Binance في إغلاق {symbol}: {e}"
            logger.error(error_msg)
            return {
                'success': False,
                'error': error_msg,
                'order_id': None
            }
        except Exception as e:
            error_msg = f"❌ خطأ غير متوقع في إغلاق {symbol}: {e}"
            logger.error(error_msg)
            return {
                'success': False,
                'error': error_msg,
                'order_id': None
            }
    
    async def get_margin_info(self) -> Dict:
        """
        جلب معلومات الهامش والحساب
        """
        try:
            await self._rate_limit()
            
            account_info = await self.client.futures_account()
            
            total_wallet_balance = float(account_info['totalWalletBalance'])
            total_margin_balance = float(account_info['totalMarginBalance'])
            available_balance = float(account_info['availableBalance'])
            total_unrealized_pnl = float(account_info['totalUnrealizedProfit'])
            
            # حساب نسبة استخدام الهامش
            margin_ratio = 0
            if total_margin_balance > 0:
                margin_ratio = (total_margin_balance - available_balance) / total_margin_balance * 100
            
            margin_info = {
                'total_wallet_balance': total_wallet_balance,
                'total_margin_balance': total_margin_balance,
                'available_balance': available_balance,
                'total_unrealized_pnl': total_unrealized_pnl,
                'margin_ratio': margin_ratio,
                'update_time': datetime.now()
            }
            
            logger.debug(f"🏦 معلومات الهامش - النسبة: {margin_ratio:.2f}%")
            return margin_info
            
        except BinanceAPIException as e:
            logger.error(f"❌ خطأ Binance في جلب معلومات الهامش: {e}")
            raise
        except Exception as e:
            logger.error(f"❌ خطأ غير متوقع في جلب معلومات الهامش: {e}")
            raise
    
    async def calculate_technical_levels(self, symbol: str) -> Dict:
        """
        حساب المستويات الفنية (ATR, الدعم, المقاومة)
        """
        try:
            # جلب البيانات التاريخية
            klines = await self.get_klines(symbol, '15m', 50)
            
            # حساب ATR
            atr = await self._calculate_atr(klines)
            
            # حساب الدعم والمقاومة
            support, resistance = await self._calculate_support_resistance(klines)
            
            # جلب السعر الحالي
            current_price = await self.get_current_price(symbol)
            
            technical_levels = {
                'atr': atr,
                'support': support,
                'resistance': resistance,
                'current_price': current_price,
                'timestamp': datetime.now()
            }
            
            logger.debug(f"📈 المستويات الفنية لـ {symbol}: ATR={atr:.4f}, الدعم={support:.4f}, المقاومة={resistance:.4f}")
            return technical_levels
            
        except Exception as e:
            logger.error(f"❌ خطأ في حساب المستويات الفنية لـ {symbol}: {e}")
            # قيم افتراضية في حالة الخطأ
            return {
                'atr': 0.01,
                'support': 0,
                'resistance': 0,
                'current_price': 0,
                'timestamp': datetime.now()
            }
    
    async def get_klines(self, symbol: str, interval: str = '15m', limit: int = 100) -> List[Dict]:
        """
        جلب البيانات الشمعية التاريخية
        """
        try:
            await self._rate_limit()
            
            klines = await self.client.futures_klines(
                symbol=symbol,
                interval=interval,
                limit=limit
            )
            
            formatted_klines = []
            for kline in klines:
                formatted_klines.append({
                    'timestamp': datetime.fromtimestamp(kline[0] / 1000),
                    'open': float(kline[1]),
                    'high': float(kline[2]),
                    'low': float(kline[3]),
                    'close': float(kline[4]),
                    'volume': float(kline[5])
                })
            
            return formatted_klines
            
        except BinanceAPIException as e:
            logger.error(f"❌ خطأ Binance في جلب البيانات لـ {symbol}: {e}")
            raise
        except Exception as e:
            logger.error(f"❌ خطأ غير متوقع في جلب البيانات لـ {symbol}: {e}")
            raise
    
    async def _calculate_atr(self, klines: List[Dict], period: int = 14) -> float:
        """
        حساب Average True Range (ATR)
        """
        try:
            if len(klines) < period + 1:
                return 0.01  # قيمة افتراضية
            
            true_ranges = []
            
            for i in range(1, len(klines)):
                high = klines[i]['high']
                low = klines[i]['low']
                prev_close = klines[i-1]['close']
                
                tr1 = high - low
                tr2 = abs(high - prev_close)
                tr3 = abs(low - prev_close)
                
                true_range = max(tr1, tr2, tr3)
                true_ranges.append(true_range)
            
            # حساب ATR
            atr = sum(true_ranges[-period:]) / period
            return atr
            
        except Exception as e:
            logger.error(f"❌ خطأ في حساب ATR: {e}")
            return 0.01
    
    async def _calculate_support_resistance(self, klines: List[Dict], lookback: int = 20) -> Tuple[float, float]:
        """
        حساب مستويات الدعم والمقاومة الديناميكية
        """
        try:
            if len(klines) < lookback:
                current_price = klines[-1]['close'] if klines else 0
                return current_price * 0.99, current_price * 1.01
            
            # استخدام آخر lookback شمعة
            recent_klines = klines[-lookback:]
            
            # إيجاد أعلى وأقل سعر في الفترة
            highs = [k['high'] for k in recent_klines]
            lows = [k['low'] for k in recent_klines]
            
            resistance = max(highs)
            support = min(lows)
            
            current_price = recent_klines[-1]['close']
            
            # تعديل المستويات بناءً على السعر الحالي
            if current_price > resistance:
                resistance = current_price * 1.005  # إضافة هامش صغير
            if current_price < support:
                support = current_price * 0.995  # إضافة هامش صغير
            
            return support, resistance
            
        except Exception as e:
            logger.error(f"❌ خطأ في حساب الدعم/المقاومة: {e}")
            current_price = klines[-1]['close'] if klines else 0
            return current_price * 0.99, current_price * 1.01
    
    async def get_exchange_info(self, symbol: str) -> Dict:
        """
        جلب معلومات التداول للرمز
        """
        try:
            await self._rate_limit()
            
            info = await self.client.futures_exchange_info()
            symbol_info = next((s for s in info['symbols'] if s['symbol'] == symbol), None)
            
            if symbol_info:
                filters = {f['filterType']: f for f in symbol_info['filters']}
                
                return {
                    'symbol': symbol,
                    'base_asset': symbol_info['baseAsset'],
                    'quote_asset': symbol_info['quoteAsset'],
                    'min_qty': float(filters['LOT_SIZE']['minQty']),
                    'step_size': float(filters['LOT_SIZE']['stepSize']),
                    'min_notional': float(filters['MIN_NOTIONAL']['notional'])
                }
            
            return {}
            
        except Exception as e:
            logger.error(f"❌ خطأ في جلب معلومات التداول لـ {symbol}: {e}")
            return {}
    
    async def test_connection(self) -> bool:
        """
        اختبار اتصال Binance
        """
        try:
            await self._rate_limit()
            
            # محاولة جلب وقت السيرفر
            server_time = await self.client.get_server_time()
            logger.info("✅ اتصال Binance يعمل بشكل صحيح")
            return True
            
        except Exception as e:
            logger.error(f"❌ فشل اختبار اتصال Binance: {e}")
            return False

# مثال على الاستخدام
async def main():
    """اختبار محرك Binance"""
    config = {
        'api_key': 'YOUR_API_KEY',
        'api_secret': 'YOUR_API_SECRET', 
        'testnet': True
    }
    
    engine = BinanceEngine(config)
    
    try:
        if await engine.initialize():
            # اختبار الاتصال
            if await engine.test_connection():
                print("✅ الاتصال بنجاح")
                
                # اختبار جلب الصفقات
                positions = await engine.get_open_positions()
                print(f"📊 الصفقات المفتوحة: {len(positions)}")
                
                # اختبار جلب السعر
                price = await engine.get_current_price('BNBUSDT')
                print(f"💰 سعر BNBUSDT: {price}")
                
                # اختبار المستويات الفنية
                levels = await engine.calculate_technical_levels('BNBUSDT')
                print(f"📈 المستويات الفنية: {levels}")
        
    except Exception as e:
        print(f"❌ خطأ: {e}")
    finally:
        await engine.close()

if __name__ == "__main__":
    asyncio.run(main())
