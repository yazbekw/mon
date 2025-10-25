import logging
from datetime import datetime
from typing import Dict, List, Optional
from config.settings import AppSettings, RiskSettings
from services.binance_client import BinanceClient
from services.notification import TelegramNotifier
from core.calculations import PriceCalculator

logger = logging.getLogger(__name__)

class TradeManager:
    def __init__(self, binance_client: BinanceClient, notifier: TelegramNotifier):
        self.client = binance_client
        self.notifier = notifier
        self.calculator = PriceCalculator()
        self.settings = AppSettings()
        self.risk_settings = RiskSettings()
        
        self.managed_trades: Dict = {}
        self.performance_stats = {
            'total_trades_managed': 0,
            'profitable_trades': 0,
            'stopped_trades': 0,
            'take_profit_hits': 0,
            'partial_stop_hits': 0,
            'total_pnl': 0
        }
    
    def sync_with_binance(self) -> int:
        """مزامنة الصفقات مع Binance وإدارة الصفقات الجديدة فوراً"""
        try:
            active_positions = self.client.get_active_positions()
            current_managed = set(self.managed_trades.keys())
            binance_symbols = {pos['symbol'] for pos in active_positions}
            
            logger.info(f"🔄 المزامنة: {len(active_positions)} صفقة في Binance")
            
            # إضافة الصفقات الجديدة
            added_count = 0
            for position in active_positions:
                if position['symbol'] not in current_managed:
                    logger.info(f"🔄 إضافة صفقة جديدة للمراقبة: {position['symbol']}")
                    if self._manage_new_trade(position):
                        added_count += 1
            
            # إزالة الصفقات المغلقة
            removed_count = 0
            for symbol in list(current_managed):
                if symbol not in binance_symbols:
                    logger.info(f"🔄 إزالة صفقة مغلقة: {symbol}")
                    del self.managed_trades[symbol]
                    removed_count += 1
            
            logger.info(f"✅ انتهت المزامنة: أضيف {added_count}، أزيل {removed_count}")
            return len(active_positions)
            
        except Exception as e:
            logger.error(f"❌ خطأ في المزامنة: {e}")
            return 0
    
    def _manage_new_trade(self, trade_data: Dict) -> bool:
        """بدء إدارة صفقة جديدة"""
        symbol = trade_data['symbol']
        
        try:
            df = self.client.get_price_data(symbol)
            if df is None or df.empty:
                logger.error(f"❌ لا يمكن إدارة {symbol} - بيانات السعر غير متوفرة")
                return False
            
            # حساب مستويات وقف الخسارة
            stop_loss_levels = self.calculator.calculate_stop_loss_levels(
                symbol, trade_data['entry_price'], trade_data['direction'], df
            )
            
            # حساب مستويات جني الأرباح
            take_profit_levels = self.calculator.calculate_take_profit_levels(
                symbol, trade_data['entry_price'], trade_data['direction'], trade_data['quantity'], df
            )
            
            # حفظ بيانات الإدارة
            self.managed_trades[symbol] = {
                **trade_data,
                'dynamic_stop_loss': stop_loss_levels,
                'take_profit_levels': take_profit_levels,
                'closed_levels': [],
                'partial_stop_hit': False,
                'last_update': datetime.now(self.settings.damascus_tz),
                'status': 'managed',
                'management_start': datetime.now(self.settings.damascus_tz)
            }
            
            self.performance_stats['total_trades_managed'] += 1
            
            # إرسال إشعار بدء الإدارة
            self._send_management_start_notification(symbol)
            return True
            
        except Exception as e:
            logger.error(f"❌ فشل إدارة الصفقة {symbol}: {e}")
            return False
    
    def check_managed_trades(self) -> List[str]:
        """فحص جميع الصفقات المدارة"""
        closed_trades = []
        
        for symbol, trade in list(self.managed_trades.items()):
            try:
                current_price = self.client.get_current_price(symbol)
                if not current_price:
                    continue
                
                # فحص وقف الخسارة
                if self._check_stop_loss(symbol, current_price):
                    closed_trades.append(symbol)
                    continue
                
                # فحص جني الأرباح
                self._check_take_profits(symbol, current_price)
                
                # تحديث المستويات كل ساعة
                if (datetime.now(self.settings.damascus_tz) - trade['last_update']).seconds > 3600:
                    self._update_dynamic_levels(symbol)
                    
            except Exception as e:
                logger.error(f"❌ خطأ في فحص الصفقة {symbol}: {e}")
        
        return closed_trades
    
    def _check_stop_loss(self, symbol: str, current_price: float) -> bool:
        """فحص وقف الخسارة المزدوج"""
        if symbol not in self.managed_trades:
            return False
        
        trade = self.managed_trades[symbol]
        stop_levels = trade['dynamic_stop_loss']
        
        # تحديد إذا كان يجب الإغلاق جزئياً أو كلياً
        if trade['direction'] == 'LONG':
            should_close_partial = current_price <= stop_levels['partial_stop_loss'] and not trade.get('partial_stop_hit')
            should_close_full = current_price <= stop_levels['full_stop_loss']
        else:
            should_close_partial = current_price >= stop_levels['partial_stop_loss'] and not trade.get('partial_stop_hit')
            should_close_full = current_price >= stop_levels['full_stop_loss']
        
        # الإغلاق الجزئي
        if should_close_partial:
            close_quantity = trade['quantity'] * self.risk_settings.partial_close_ratio
            if self.client.close_position(symbol, close_quantity, trade['direction']):
                trade['partial_stop_hit'] = True
                trade['quantity'] -= close_quantity
                self.performance_stats['partial_stop_hits'] += 1
                self._send_partial_stop_notification(trade, current_price, close_quantity)
        
        # الإغلاق الكامل
        if should_close_full:
            if self._close_entire_trade(symbol, "وقف خسارة كامل"):
                self.performance_stats['stopped_trades'] += 1
                pnl_pct = self._calculate_pnl_percentage(trade, current_price)
                self.performance_stats['total_pnl'] += pnl_pct
                self._send_trade_closed_notification(trade, current_price, "وقف خسارة كامل", pnl_pct)
                return True
        
        return False
    
    def _check_take_profits(self, symbol: str, current_price: float):
        """فحص مستويات جني الأرباح"""
        trade = self.managed_trades[symbol]
        
        for level, config in trade['take_profit_levels'].items():
            if level in trade['closed_levels']:
                continue
            
            should_close = False
            if trade['direction'] == 'LONG' and current_price >= config['price']:
                should_close = True
            elif trade['direction'] == 'SHORT' and current_price <= config['price']:
                should_close = True
            
            if should_close:
                if self.client.close_position(symbol, config['quantity'], trade['direction']):
                    trade['closed_levels'].append(level)
                    self.performance_stats['take_profit_hits'] += 1
                    self._send_take_profit_notification(trade, level, current_price)
                    
                    # إذا تم جني جميع المستويات
                    if len(trade['closed_levels']) == len(trade['take_profit_levels']):
                        self._close_entire_trade(symbol, "تم جني جميع مستويات الربح")
                        self.performance_stats['profitable_trades'] += 1
    
    def _close_entire_trade(self, symbol: str, reason: str) -> bool:
        """إغلاق كامل للصفقة"""
        if symbol not in self.managed_trades:
            return False
        
        trade = self.managed_trades[symbol]
        
        # حساب الكمية المتبقية (بعد الإغلاقات الجزئية)
        total_closed = sum(
            trade['take_profit_levels'][level]['quantity'] 
            for level in trade['closed_levels'] 
            if level in trade['take_profit_levels']
        )
        remaining_quantity = trade['quantity'] - total_closed
        
        if remaining_quantity > 0:
            if self.client.close_position(symbol, remaining_quantity, trade['direction']):
                del self.managed_trades[symbol]
                logger.info(f"✅ إغلاق كامل لـ {symbol}: {reason}")
                return True
        
        return False
    
    def _update_dynamic_levels(self, symbol: str):
        """تحديث المستويات الديناميكية"""
        if symbol not in self.managed_trades:
            return
        
        trade = self.managed_trades[symbol]
        df = self.client.get_price_data(symbol)
        if df is None:
            return
        
        # تحديث وقف الخسارة
        new_stop_loss = self.calculator.calculate_stop_loss_levels(
            symbol, trade['entry_price'], trade['direction'], df
        )
        
        # تحديث فقط إذا كان أفضل (ل LONG: أعلى، ل SHORT: أقل)
        current_stop = trade['dynamic_stop_loss']['full_stop_loss']
        new_stop = new_stop_loss['full_stop_loss']
        
        if (trade['direction'] == 'LONG' and new_stop > current_stop) or \
           (trade['direction'] == 'SHORT' and new_stop < current_stop):
            self.managed_trades[symbol]['dynamic_stop_loss'] = new_stop_loss
            self.managed_trades[symbol]['last_update'] = datetime.now(self.settings.damascus_tz)
            logger.info(f"🔄 تحديث وقف الخسارة لـ {symbol}")
    
    def _calculate_pnl_percentage(self, trade: Dict, current_price: float) -> float:
        """حساب نسبة الربح/الخسارة"""
        if trade['direction'] == 'LONG':
            return (current_price - trade['entry_price']) / trade['entry_price'] * 100
        else:
            return (trade['entry_price'] - current_price) / trade['entry_price'] * 100
    
    # وظائف الإشعارات
    def _send_management_start_notification(self, symbol: str):
        trade = self.managed_trades[symbol]
        stop_levels = trade['dynamic_stop_loss']
        
        message = (
            f"🔄 <b>بدء إدارة صفقة جديدة</b>\n"
            f"العملة: {symbol}\n"
            f"الاتجاه: {trade['direction']}\n"
            f"سعر الدخول: ${trade['entry_price']:.4f}\n"
            f"الكمية: {trade['quantity']:.6f}\n"
            f"وقف الخسارة الجزئي: ${stop_levels['partial_stop_loss']:.4f}\n"
            f"وقف الخسارة الكامل: ${stop_levels['full_stop_loss']:.4f}\n"
            f"الوقت: {datetime.now(self.settings.damascus_tz).strftime('%H:%M:%S')}"
        )
        
        self.notifier.send_message(message)
    
    def _send_partial_stop_notification(self, trade: Dict, current_price: float, closed_quantity: float):
        message = (
            f"🛡️ <b>وقف خسارة جزئي</b>\n"
            f"العملة: {trade['symbol']}\n"
            f"الكمية المغلقة: {closed_quantity:.6f}\n"
            f"الكمية المتبقية: {trade['quantity']:.6f}\n"
            f"السبب: تقليل التعرض للمخاطرة\n"
            f"الوقت: {datetime.now(self.settings.damascus_tz).strftime('%H:%M:%S')}"
        )
        
        self.notifier.send_message(message)
    
    def _send_take_profit_notification(self, trade: Dict, level: str, current_price: float):
        config = trade['take_profit_levels'][level]
        
        message = (
            f"🎯 <b>جني أرباح جزئي</b>\n"
            f"العملة: {trade['symbol']}\n"
            f"المستوى: {level}\n"
            f"الربح: {config['target_percent']:.2f}%\n"
            f"الكمية: {config['quantity']:.6f}\n"
            f"الوقت: {datetime.now(self.settings.damascus_tz).strftime('%H:%M:%S')}"
        )
        
        self.notifier.send_message(message)
    
    def _send_trade_closed_notification(self, trade: Dict, current_price: float, reason: str, pnl_pct: float):
        pnl_emoji = "🟢" if pnl_pct > 0 else "🔴"
        
        message = (
            f"🔒 <b>إغلاق الصفقة</b>\n"
            f"العملة: {trade['symbol']}\n"
            f"الربح/الخسارة: {pnl_emoji} {pnl_pct:+.2f}%\n"
            f"السبب: {reason}\n"
            f"الوقت: {datetime.now(self.settings.damascus_tz).strftime('%H:%M:%S')}"
        )
        
        self.notifier.send_message(message)
    
    def send_performance_report(self):
        if self.performance_stats['total_trades_managed'] > 0:
            win_rate = (self.performance_stats['profitable_trades'] / self.performance_stats['total_trades_managed']) * 100
        else:
            win_rate = 0
        
        message = (
            f"📊 <b>تقرير أداء مدير الصفقات</b>\n"
            f"إجمالي الصفقات: {self.performance_stats['total_trades_managed']}\n"
            f"معدل الربح: {win_rate:.1f}%\n"
            f"أرباح Take Profit: {self.performance_stats['take_profit_hits']}\n"
            f"صفقات Stop Loss: {self.performance_stats['stopped_trades']}\n"
            f"وقف خسارة جزئي: {self.performance_stats['partial_stop_hits']}\n"
            f"الصفقات النشطة: {len(self.managed_trades)}\n"
            f"الوقت: {datetime.now(self.settings.damascus_tz).strftime('%H:%M:%S')}"
        )
        
        self.notifier.send_message(message)
