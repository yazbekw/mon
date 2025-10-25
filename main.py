import asyncio
import logging
import time
import requests
import hmac
import hashlib
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from binance.client import Client
from binance.exceptions import BinanceAPIException
from flask import Flask, jsonify, request
import threading
import schedule

# 📊 نظام التسجيل
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('trade_manager.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class TradeManager:
    def __init__(self):
        # 🔐 تحميل المفاتيح من متغيرات البيئة
        self.binance_api_key = os.getenv("BINANCE_API_KEY")
        self.binance_secret_key = os.getenv("BINANCE_SECRET_KEY")
        self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        
        # التحقق من وجود المفاتيح
        if not all([self.binance_api_key, self.binance_secret_key]):
            raise ValueError("مفاتيح Binance غير موجودة في متغيرات البيئة")
        
        # 🎯 إعدادات التداول
        self.supported_symbols = ["BNBUSDT", "ETHUSDT"]
        self.leverage = 50
        self.base_quantity = 3
        self.max_concurrent_trades = 1
        
        # 🛡️ إعدادات المخاطرة
        self.min_stop_loss_pct = 1.5
        self.max_stop_loss_pct = 5.0
        self.volatility_multiplier = 1.5
        self.margin_risk_threshold = 0.7
        
        # 📈 إعدادات جني الأرباح
        self.take_profit_levels = [
            {"profit_pct": 0.25, "close_pct": 0.50},
            {"profit_pct": 0.30, "close_pct": 0.30},
            {"profit_pct": 0.35, "close_pct": 0.20}
        ]
        
        # ⏰ الفترات الزمنية
        self.trade_detection_interval = 30
        self.margin_check_interval = 60
        self.levels_check_interval = 10
        self.performance_report_interval = 6 * 3600  # 6 ساعات
        
        # 🔧 تهيئة العميل
        self.client = Client(self.binance_api_key, self.binance_secret_key)
        self.managed_positions = {}
        self.performance_stats = {
            'total_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'total_pnl': 0.0,
            'take_profit_count': 0,
            'stop_loss_count': 0,
            'partial_tp_count': 0,
            'partial_sl_count': 0
        }
        self.last_sync_time = None
        self.is_running = True
        
    # 🔄 المزامنة التلقائية مع Binance
    def sync_with_binance(self):
        """مزامنة الصفقات مع Binance"""
        try:
            logger.info("بدء مزامنة الصفقات مع Binance...")
            
            # الحصول على المراكز المفتوحة
            positions = self.client.futures_position_information()
            
            for position in positions:
                symbol = position['symbol']
                position_amt = float(position['positionAmt'])
                
                # تجاهل المراكز ذات الكمية الصفرية
                if position_amt == 0:
                    if symbol in self.managed_positions:
                        del self.managed_positions[symbol]
                    continue
                
                # التحقق إذا كانت العملة مدعومة
                if symbol not in self.supported_symbols:
                    continue
                
                # التحقق من الحد الأقصى للصفقات المتزامنة
                if len(self.managed_positions) >= self.max_concurrent_trades:
                    logger.warning(f"تم الوصول للحد الأقصى للصفقات، تجاهل {symbol}")
                    continue
                
                # إضافة الصفقة الجديدة للإدارة
                if symbol not in self.managed_positions:
                    self._add_new_position(symbol, position)
                    self._send_telegram_message(
                        f"🎯 بدء إدارة صفقة جديدة\n"
                        f"العملة: {symbol}\n"
                        f"الكمية: {position_amt}\n"
                        f"الاتجاه: {'LONG' if position_amt > 0 else 'SHORT'}\n"
                        f"الرافعة: {self.leverage}x"
                    )
            
            self.last_sync_time = datetime.now()
            logger.info("تمت مزامنة الصفقات بنجاح")
            
        except Exception as e:
            logger.error(f"خطأ في المزامنة: {str(e)}")
            self._send_telegram_message(f"❌ خطأ في مزامنة الصفقات: {str(e)}")
    
    def _add_new_position(self, symbol: str, position: dict):
        """إضافة صفقة جديدة للإدارة"""
        entry_price = float(position['entryPrice'])
        position_amt = float(position['positionAmt'])
        
        # حساب مستويات وقف الخسارة
        stop_loss_levels = self._calculate_stop_loss_levels(symbol, entry_price, position_amt > 0)
        
        # حساب مستويات جني الأرباح
        take_profit_levels = self._calculate_take_profit_levels(entry_price, position_amt > 0)
        
        self.managed_positions[symbol] = {
            'symbol': symbol,
            'entry_price': entry_price,
            'quantity': abs(position_amt),
            'is_long': position_amt > 0,
            'leverage': self.leverage,
            'stop_loss_levels': stop_loss_levels,
            'take_profit_levels': take_profit_levels,
            'partial_sl_executed': False,
            'tp_levels_executed': [False, False, False],
            'created_at': datetime.now(),
            'last_update': datetime.now()
        }
        
        self.performance_stats['total_trades'] += 1
        
        logger.info(f"تمت إضافة {symbol} للإدارة - السعر: {entry_price}")

    # 🛡️ نظام وقف الخسارة المزدوج الديناميكي
    def _calculate_stop_loss_levels(self, symbol: str, entry_price: float, is_long: bool) -> Dict:
        """حساب مستويات وقف الخسارة باستخدام ATR والدعم/المقاومة"""
        try:
            # حساب ATR (Average True Range)
            atr_value = self._calculate_atr(symbol, period=14)
            
            # حساب مستويات الدعم والمقاومة
            support, resistance = self._calculate_support_resistance(symbol, period=20)
            
            current_price = self._get_current_price(symbol)
            
            if is_long:
                # وقف الخسارة الكامل بناء على الدعم و ATR
                full_stop_loss = support - (atr_value * self.volatility_multiplier)
                full_stop_loss_pct = (entry_price - full_stop_loss) / entry_price * 100
                
                # وقف الخسارة الجزئي (40% من المسافة)
                partial_sl_distance = (entry_price - full_stop_loss) * 0.4
                partial_stop_loss = entry_price - partial_sl_distance
                partial_stop_loss_pct = (entry_price - partial_stop_loss) / entry_price * 100
            else:
                # وقف الخسارة الكامل بناء على المقاومة و ATR
                full_stop_loss = resistance + (atr_value * self.volatility_multiplier)
                full_stop_loss_pct = (full_stop_loss - entry_price) / entry_price * 100
                
                # وقف الخسارة الجزئي (40% من المسافة)
                partial_sl_distance = (full_stop_loss - entry_price) * 0.4
                partial_stop_loss = entry_price + partial_sl_distance
                partial_stop_loss_pct = (partial_stop_loss - entry_price) / entry_price * 100
            
            # تطبيق الحدود الدنيا والعليا
            full_stop_loss_pct = max(self.min_stop_loss_pct, 
                                   min(self.max_stop_loss_pct, full_stop_loss_pct))
            partial_stop_loss_pct = max(self.min_stop_loss_pct * 0.4, 
                                      min(self.max_stop_loss_pct * 0.4, partial_stop_loss_pct))
            
            # إعادة حساب الأسعار بناء على النسب المئوية المعدلة
            if is_long:
                full_stop_loss = entry_price * (1 - full_stop_loss_pct / 100)
                partial_stop_loss = entry_price * (1 - partial_stop_loss_pct / 100)
            else:
                full_stop_loss = entry_price * (1 + full_stop_loss_pct / 100)
                partial_stop_loss = entry_price * (1 + partial_stop_loss_pct / 100)
            
            return {
                'partial': {
                    'price': partial_stop_loss,
                    'pct': partial_stop_loss_pct,
                    'quantity_pct': 0.3  # 30% من الصفقة
                },
                'full': {
                    'price': full_stop_loss,
                    'pct': full_stop_loss_pct,
                    'quantity_pct': 1.0  # 100% من الصفقة
                }
            }
            
        except Exception as e:
            logger.error(f"خطأ في حساب وقف الخسارة: {str(e)}")
            # استخدام القيم الافتراضية في حالة الخطأ
            default_sl_pct = (self.min_stop_loss_pct + self.max_stop_loss_pct) / 2
            partial_sl_pct = default_sl_pct * 0.4
            
            if is_long:
                full_sl_price = entry_price * (1 - default_sl_pct / 100)
                partial_sl_price = entry_price * (1 - partial_sl_pct / 100)
            else:
                full_sl_price = entry_price * (1 + default_sl_pct / 100)
                partial_sl_price = entry_price * (1 + partial_sl_pct / 100)
            
            return {
                'partial': {'price': partial_sl_price, 'pct': partial_sl_pct, 'quantity_pct': 0.3},
                'full': {'price': full_sl_price, 'pct': default_sl_pct, 'quantity_pct': 1.0}
            }

    def _calculate_atr(self, symbol: str, period: int = 14) -> float:
        """حساب Average True Range"""
        try:
            klines = self.client.futures_klines(
                symbol=symbol, 
                interval=Client.KLINE_INTERVAL_15MINUTE, 
                limit=period + 1
            )
            
            true_ranges = []
            for i in range(1, len(klines)):
                high = float(klines[i][2])
                low = float(klines[i][3])
                prev_close = float(klines[i-1][4])
                
                tr1 = high - low
                tr2 = abs(high - prev_close)
                tr3 = abs(low - prev_close)
                
                true_ranges.append(max(tr1, tr2, tr3))
            
            return sum(true_ranges) / len(true_ranges) if true_ranges else 0.0
            
        except Exception as e:
            logger.error(f"خطأ في حساب ATR: {str(e)}")
            return 0.0

    def _calculate_support_resistance(self, symbol: str, period: int = 20) -> Tuple[float, float]:
        """حساب مستويات الدعم والمقاومة"""
        try:
            klines = self.client.futures_klines(
                symbol=symbol,
                interval=Client.KLINE_INTERVAL_15MINUTE,
                limit=period
            )
            
            lows = [float(k[3]) for k in klines]
            highs = [float(k[2]) for k in klines]
            
            support = min(lows)
            resistance = max(highs)
            
            return support, resistance
            
        except Exception as e:
            logger.error(f"خطأ في حساب الدعم/المقاومة: {str(e)}")
            current_price = self._get_current_price(symbol)
            return current_price * 0.98, current_price * 1.02

    # 🎯 جني الأرباح متعدد المستويات
    def _calculate_take_profit_levels(self, entry_price: float, is_long: bool) -> List[Dict]:
        """حساب مستويات جني الأرباح"""
        levels = []
        
        for i, level_config in enumerate(self.take_profit_levels):
            profit_pct = level_config['profit_pct']
            close_pct = level_config['close_pct']
            
            if is_long:
                tp_price = entry_price * (1 + profit_pct / 100)
            else:
                tp_price = entry_price * (1 - profit_pct / 100)
            
            levels.append({
                'level': i + 1,
                'price': tp_price,
                'profit_pct': profit_pct,
                'close_pct': close_pct,
                'executed': False
            })
        
        return levels

    # 📈 مراقبة الهامش والرافعة
    def check_margin_health(self):
        """فحص صحة الهامش والرافعة"""
        try:
            account_info = self.client.futures_account()
            
            total_wallet_balance = float(account_info['totalWalletBalance'])
            total_margin_balance = float(account_info['totalMarginBalance'])
            total_position_im = float(account_info['totalInitialMargin'])
            
            if total_wallet_balance == 0:
                return
            
            margin_ratio = total_position_im / total_wallet_balance
            
            if margin_ratio >= self.margin_risk_threshold:
                warning_msg = (
                    f"⚠️ تحذير مخاطرة الهامش\n"
                    f"نسبة الهامش المستخدم: {margin_ratio:.1%}\n"
                    f"الحد الأقصى المسموح: {self.margin_risk_threshold:.1%}\n"
                    f"يرجى تقليل المراكز أو إضافة هامش"
                )
                self._send_telegram_message(warning_msg)
                logger.warning(f"مخاطرة عالية في الهامش: {margin_ratio:.1%}")
                
                # تقليل المراكز تلقائياً إذا كانت النسبة عالية جداً
                if margin_ratio > 0.85:
                    self._reduce_positions_automatically()
                    
        except Exception as e:
            logger.error(f"خطأ في فحص الهامش: {str(e)}")

    def _reduce_positions_automatically(self):
        """تقليل المراكز تلقائياً عند الخطر"""
        try:
            for symbol, position in list(self.managed_positions.items()):
                # إغلاق 50% من كل صفقة
                close_quantity = position['quantity'] * 0.5
                
                if close_quantity > 0:
                    self._close_position_partial(symbol, close_quantity, "تلقائي بسبب مخاطرة الهامش")
                    
        except Exception as e:
            logger.error(f"خطأ في تقليل المراكز: {str(e)}")

    # 🎯 فحص المستويات وتنفيذ الأوامر
    def check_levels_and_execute(self):
        """فحص مستويات وقف الخسارة وجني الأرباح وتنفيذ الأوامر"""
        try:
            for symbol, position in list(self.managed_positions.items()):
                current_price = self._get_current_price(symbol)
                
                # فحص وقف الخسارة الجزئي
                if not position['partial_sl_executed']:
                    self._check_partial_stop_loss(symbol, position, current_price)
                
                # فحص وقف الخسارة الكامل
                self._check_full_stop_loss(symbol, position, current_price)
                
                # فحص جني الأرباح
                self._check_take_profit_levels(symbol, position, current_price)
                
                position['last_update'] = datetime.now()
                
        except Exception as e:
            logger.error(f"خطأ في فحص المستويات: {str(e)}")

    def _check_partial_stop_loss(self, symbol: str, position: dict, current_price: float):
        """فحص وتنفيذ وقف الخسارة الجزئي"""
        sl_level = position['stop_loss_levels']['partial']
        is_long = position['is_long']
        
        if ((is_long and current_price <= sl_level['price']) or
            (not is_long and current_price >= sl_level['price'])):
            
            close_quantity = position['quantity'] * sl_level['quantity_pct']
            self._close_position_partial(symbol, close_quantity, "وقف خسارة جزئي")
            
            position['partial_sl_executed'] = True
            
            self._send_telegram_message(
                f"🛡️ وقف خسارة جزئي تم تنفيذه\n"
                f"العملة: {symbol}\n"
                f"الكمية: {close_quantity:.4f}\n"
                f"السعر: {current_price:.4f}\n"
                f"النسبة: {sl_level['pct']:.2f}%"
            )
            
            self.performance_stats['partial_sl_count'] += 1

    def _check_full_stop_loss(self, symbol: str, position: dict, current_price: float):
        """فحص وتنفيذ وقف الخسارة الكامل"""
        sl_level = position['stop_loss_levels']['full']
        is_long = position['is_long']
        
        if ((is_long and current_price <= sl_level['price']) or
            (not is_long and current_price >= sl_level['price'])):
            
            self._close_position_full(symbol, "وقف خسارة كامل")
            
            self._send_telegram_message(
                f"🛑 وقف خسارة كامل تم تنفيذه\n"
                f"العملة: {symbol}\n"
                f"السعر: {current_price:.4f}\n"
                f"النسبة: {sl_level['pct']:.2f}%"
            )
            
            self.performance_stats['stop_loss_count'] += 1
            self.performance_stats['losing_trades'] += 1

    def _check_take_profit_levels(self, symbol: str, position: dict, current_price: float):
        """فحص وتنفيذ مستويات جني الأرباح"""
        is_long = position['is_long']
        
        for i, tp_level in enumerate(position['take_profit_levels']):
            if tp_level['executed']:
                continue
            
            if ((is_long and current_price >= tp_level['price']) or
                (not is_long and current_price <= tp_level['price'])):
                
                close_quantity = position['quantity'] * tp_level['close_pct']
                self._close_position_partial(symbol, close_quantity, f"جني أرباح المستوى {tp_level['level']}")
                
                tp_level['executed'] = True
                position['tp_levels_executed'][i] = True
                
                self._send_telegram_message(
                    f"🎯 جني أرباح المستوى {tp_level['level']}\n"
                    f"العملة: {symbol}\n"
                    f"الكمية: {close_quantity:.4f}\n"
                    f"السعر: {current_price:.4f}\n"
                    f"الربح: {tp_level['profit_pct']:.2f}%"
                )
                
                self.performance_stats['partial_tp_count'] += 1
                
                # إذا كانت آخر مستوى، إغلاق الصفقة كاملة
                if i == len(position['take_profit_levels']) - 1:
                    remaining_quantity = position['quantity']
                    if remaining_quantity > 0:
                        self._close_position_full(symbol, "جني أرباح كامل")
                        self.performance_stats['take_profit_count'] += 1
                        self.performance_stats['winning_trades'] += 1

    # 🛠️ وظائف التنفيذ
    def _close_position_partial(self, symbol: str, quantity: float, reason: str):
        """إغلاق جزء من الصفقة"""
        try:
            position = self.managed_positions[symbol]
            side = "SELL" if position['is_long'] else "BUY"
            
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type=Client.ORDER_TYPE_MARKET,
                quantity=round(quantity, 4),
                reduceOnly=True
            )
            
            # تحديث الكمية المتبقية
            position['quantity'] -= quantity
            
            logger.info(f"تم إغلاق {quantity:.4f} من {symbol} - السبب: {reason}")
            
            # إذا كانت الكمية المتبقية صغيرة، إغلاق الصفقة كاملة
            if position['quantity'] < position['quantity'] * 0.05:  # أقل من 5%
                self._close_position_full(symbol, "إغلاق كامل بعد تصفية")
                
        except Exception as e:
            logger.error(f"خطأ في الإغلاق الجزئي: {str(e)}")
            self._send_telegram_message(f"❌ خطأ في الإغلاق الجزئي لـ {symbol}: {str(e)}")

    def _close_position_full(self, symbol: str, reason: str):
        """إغلاق الصفقة كاملة"""
        try:
            if symbol not in self.managed_positions:
                return
                
            position = self.managed_positions[symbol]
            side = "SELL" if position['is_long'] else "BUY"
            
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type=Client.ORDER_TYPE_MARKET,
                quantity=round(position['quantity'], 4),
                reduceOnly=True
            )
            
            # حساب PnL التقريبي
            current_price = self._get_current_price(symbol)
            if position['is_long']:
                pnl = (current_price - position['entry_price']) * position['quantity']
            else:
                pnl = (position['entry_price'] - current_price) * position['quantity']
            
            self.performance_stats['total_pnl'] += pnl
            
            # إزالة الصفقة من الإدارة
            del self.managed_positions[symbol]
            
            logger.info(f"تم إغلاق {symbol} كاملاً - السبب: {reason} - PnL: {pnl:.4f}")
            
        except Exception as e:
            logger.error(f"خطأ في الإغلاق الكامل: {str(e)}")
            self._send_telegram_message(f"❌ خطأ في الإغلاق الكامل لـ {symbol}: {str(e)}")

    # 🔔 نظام إشعارات Telegram
    def _send_telegram_message(self, message: str):
        """إرسال رسالة عبر Telegram"""
        try:
            if not self.telegram_bot_token or not self.telegram_chat_id:
                logger.warning("مفاتيح Telegram غير موجودة - تخطي الإرسال")
                return
                
            url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
            payload = {
                'chat_id': self.telegram_chat_id,
                'text': message,
                'parse_mode': 'HTML'
            }
            
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code != 200:
                logger.error(f"فشل إرسال رسالة Telegram: {response.text}")
                
        except Exception as e:
            logger.error(f"خطأ في إرسال Telegram: {str(e)}")

    def send_performance_report(self):
        """إرسال تقرير أداء دوري"""
        try:
            stats = self.performance_stats
            
            if stats['total_trades'] == 0:
                return
                
            win_rate = (stats['winning_trades'] / stats['total_trades']) * 100
            
            report = (
                f"📊 تقرير أداء البوت\n"
                f"──────────────────\n"
                f"إجمالي الصفقات: {stats['total_trades']}\n"
                f"الصفقات الرابحة: {stats['winning_trades']}\n"
                f"الصفقات الخاسرة: {stats['losing_trades']}\n"
                f"معدل الربح: {win_rate:.1f}%\n"
                f"إجمالي PnL: {stats['total_pnl']:.4f} USDT\n"
                f"──────────────────\n"
                f"جني الأرباح: {stats['take_profit_count']}\n"
                f"وقف الخسارة: {stats['stop_loss_count']}\n"
                f"جني جزئي: {stats['partial_tp_count']}\n"
                f"وقف جزئي: {stats['partial_sl_count']}\n"
                f"الصفقات النشطة: {len(self.managed_positions)}"
            )
            
            self._send_telegram_message(report)
            logger.info("تم إرسال تقرير الأداء")
            
        except Exception as e:
            logger.error(f"خطأ في إرسال تقرير الأداء: {str(e)}")

    # 🛠️ وظائف مساعدة
    def _get_current_price(self, symbol: str) -> float:
        """الحصول على السعر الحالي"""
        try:
            ticker = self.client.futures_symbol_ticker(symbol=symbol)
            return float(ticker['price'])
        except Exception as e:
            logger.error(f"خطأ في الحصول على السعر: {str(e)}")
            return 0.0

    def get_status(self) -> Dict:
        """الحصول على حالة البوت"""
        return {
            'is_running': self.is_running,
            'managed_positions': len(self.managed_positions),
            'last_sync': self.last_sync_time.isoformat() if self.last_sync_time else None,
            'performance_stats': self.performance_stats,
            'supported_symbols': self.supported_symbols,
            'leverage': self.leverage
        }

# 🌐 واجهات التحكم (API)
app = Flask(__name__)
trade_manager = None

def setup_api(tm: TradeManager):
    global trade_manager
    trade_manager = tm
    
    @app.route('/')
    def index():
        return jsonify({
            "status": "البوت يعمل", 
            "timestamp": datetime.now().isoformat(),
            "version": "1.0.0"
        })
    
    @app.route('/api/management/status', methods=['GET'])
    def get_management_status():
        return jsonify(trade_manager.get_status())
    
    @app.route('/api/management/sync', methods=['POST'])
    def manual_sync():
        try:
            trade_manager.sync_with_binance()
            return jsonify({"status": "success", "message": "تمت المزامنة اليدوية"})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500
    
    @app.route('/api/management/close/<symbol>', methods=['POST'])
    def close_position(symbol):
        try:
            if symbol in trade_manager.managed_positions:
                trade_manager._close_position_full(symbol, "يدوي عبر API")
                return jsonify({"status": "success", "message": f"تم إغلاق {symbol}"})
            else:
                return jsonify({"status": "error", "message": "الصفقة غير موجودة"}), 404
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500
    
    @app.route('/api/debug/positions', methods=['GET'])
    def debug_positions():
        return jsonify(trade_manager.managed_positions)
    
    @app.route('/api/debug/telegram-test', methods=['POST'])
    def test_telegram():
        try:
            trade_manager._send_telegram_message("🧪 اختبار رسالة Telegram - البوت يعمل بشكل صحيح")
            return jsonify({"status": "success", "message": "تم إرسال رسالة الاختبار"})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

# ⏰ نظام الجدولة
def setup_scheduling(tm: TradeManager):
    """إعداد الجدولة الزمنية للمهام"""
    
    # 🔍 كشف الصفقات كل 30 ثانية
    schedule.every(tm.trade_detection_interval).seconds.do(tm.sync_with_binance)
    
    # 📊 مراقبة الهامش كل دقيقة
    schedule.every(tm.margin_check_interval).seconds.do(tm.check_margin_health)
    
    # 🎯 فحص المستويات كل 10 ثواني
    schedule.every(tm.levels_check_interval).seconds.do(tm.check_levels_and_execute)
    
    # 📈 تقرير الأداء كل 6 ساعات
    schedule.every(tm.performance_report_interval).seconds.do(tm.send_performance_report)
    
    # 💾 تسجيل الحالة كل 10 دقائق
    schedule.every(10).minutes.do(lambda: logger.info("✅ البوت يعمل - تسجيل حالة منتظم"))

def run_scheduler():
    """تشغيل المجدول في thread منفصل"""
    while True:
        schedule.run_pending()
        time.sleep(1)

# 🚀 التشغيل الرئيسي
def main():
    """الدالة الرئيسية لتشغيل البوت"""
    try:
        logger.info("🚀 بدء تشغيل مدير الصفقات التلقائي...")
        
        # إنشاء مدير الصفقات
        tm = TradeManager()
        
        # إعداد واجهة API
        setup_api(tm)
        
        # إعداد الجدولة
        setup_scheduling(tm)
        
        # المزامنة الأولية
        tm.sync_with_binance()
        
        # بدء الجدولة في thread منفصل
        scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
        scheduler_thread.start()
        
        # إرسال رسالة بدء التشغيل
        tm._send_telegram_message(
            "🚀 بدء تشغيل مدير الصفقات التلقائي\n"
            "✅ البوت يعمل وجاهز للإدارة\n"
            f"📊 العملات المدعومة: {', '.join(tm.supported_symbols)}\n"
            f"⚡ الرافعة: {tm.leverage}x"
        )
        
        logger.info("✅ البوت يعمل بنجاح - جاهز لإدارة الصفقات")
        
        # تشغيل واجهة API
        port = int(os.getenv("PORT", 5000))
        app.run(host='0.0.0.0', port=port, debug=False)
        
    except Exception as e:
        logger.error(f"❌ خطأ في التشغيل الرئيسي: {str(e)}")
        if 'tm' in locals():
            tm._send_telegram_message(f"❌ خطأ حرج في البوت: {str(e)}")

if __name__ == "__main__":
    main()
