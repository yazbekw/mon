import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from binance_engine import BinanceEngine
from risk_engine import RiskEngine
from notification_manager import NotificationManager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('trade_manager.log'),
        logging.StreamHandler()
    ]
)

class TradeManager:
    """
    🎯 المدير الرئيسي - العقل المفكر لنظام إدارة الصفقات التلقائي
    """
    
    def __init__(self, config: dict):
        self.config = config
        self.is_running = False
        self.active_positions: Dict[str, dict] = {}
        self.performance_stats = {
            'total_managed': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'total_take_profits': 0,
            'total_stop_losses': 0,
            'total_pnl': 0.0
        }
        
        # تهيئة المكونات
        self.binance = BinanceEngine(config['binance'])
        self.risk = RiskEngine(config['risk'])
        self.notifier = NotificationManager(config['notifications'])
        
        # الجدولة الزمنية
        self.scheduled_tasks = []
        
        logger.info("✅ تم تهيئة Trade Manager")
    
    async def start(self):
        """بدء النظام بالكامل"""
        if self.is_running:
            logger.warning("⚠️  النظام يعمل بالفعل")
            return
        
        logger.info("🚀 بدء تشغيل نظام إدارة الصفقات")
        self.is_running = True
        
        # إرسال إشعار البدء
        await self.notifier.send_message("🚀 بدء نظام إدارة الصفقات التلقائي")
        
        # المزامنة الأولية
        await self._initial_sync()
        
        # تشغيل المهام المجدولة
        self.scheduled_tasks = [
            asyncio.create_task(self._schedule_trade_detection()),
            asyncio.create_task(self._schedule_margin_monitoring()),
            asyncio.create_task(self._schedule_levels_check()),
            asyncio.create_task(self._schedule_performance_report()),
            asyncio.create_task(self._schedule_state_save())
        ]
        
        logger.info("✅ تم بدء جميع المهام المجدولة")
    
    async def stop(self):
        """إيقاف النظام"""
        if not self.is_running:
            return
        
        logger.info("🛑 إيقاف نظام إدارة الصفقات")
        self.is_running = False
        
        # إلغاء جميع المهام المجدولة
        for task in self.scheduled_tasks:
            task.cancel()
        
        await self.notifier.send_message("🛑 تم إيقاف نظام إدارة الصفقات")
    
    async def _initial_sync(self):
        """المزامنة الأولية مع Binance"""
        try:
            logger.info("🔄 بدء المزامنة الأولية مع Binance")
            
            # جلب جميع الصفقات المفتوحة
            positions = await self.binance.get_open_positions()
            
            for position in positions:
                if position['symbol'] in self.config['symbols']:
                    await self._initialize_position(position)
            
            logger.info(f"✅ تمت المزامنة الأولية - {len(self.active_positions)} صفقة نشطة")
            await self.notifier.send_message(
                f"🔄 المزامنة الأولية - {len(self.active_positions)} صفقة نشطة"
            )
            
        except Exception as e:
            logger.error(f"❌ خطأ في المزامنة الأولية: {e}")
            await self.notifier.send_message(f"❌ خطأ في المزامنة الأولية: {e}")
    
    async def _initialize_position(self, position_data: dict):
        """تهيئة صفقة جديدة للإدارة"""
        symbol = position_data['symbol']
        
        if symbol in self.active_positions:
            logger.info(f"🔄 تحديث الصفقة الموجودة: {symbol}")
        else:
            logger.info(f"🆕 إضافة صفقة جديدة للإدارة: {symbol}")
            self.performance_stats['total_managed'] += 1
        
        # تحديث بيانات الصفقة
        self.active_positions[symbol] = {
            **position_data,
            'managed_since': datetime.now(),
            'last_update': datetime.now(),
            'take_profit_levels_hit': set(),
            'partial_stop_hit': False
        }
        
        # إرسال إشعار بالصفقة الجديدة
        if symbol not in self.active_positions:
            await self.notifier.send_new_position_alert(self.active_positions[symbol])
    
    async def _schedule_trade_detection(self):
        """كشف الصفقات الجديدة كل 30 ثانية"""
        logger.info("⏰ بدء جدولة كشف الصفقات (كل 30 ثانية)")
        
        while self.is_running:
            try:
                await self._detect_and_manage_trades()
                await asyncio.sleep(30)  # انتظار 30 ثانية
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ خطأ في كشف الصفقات: {e}")
                await asyncio.sleep(30)
    
    async def _detect_and_manage_trades(self):
        """اكتشاف الصفقات الجديدة وإدارتها"""
        try:
            # 1. جلب الصفقات المفتوحة من Binance
            positions = await self.binance.get_open_positions()
            
            # 2. تصفية الصفقات المدعومة فقط
            supported_positions = [
                p for p in positions 
                if p['symbol'] in self.config['symbols']
            ]
            
            # 3. اكتشاف الصفقات الجديدة
            current_symbols = set(self.active_positions.keys())
            new_symbols = set(p['symbol'] for p in supported_positions) - current_symbols
            
            for symbol in new_symbols:
                position_data = next(p for p in supported_positions if p['symbol'] == symbol)
                await self._initialize_position(position_data)
            
            # 4. إدارة الصفقات النشطة
            for symbol in list(self.active_positions.keys()):
                if symbol not in [p['symbol'] for p in supported_positions]:
                    # الصفقة أغلقت خارج النظام
                    logger.info(f"📭 الصفقة {symbol} أغلقت خارج النظام")
                    del self.active_positions[symbol]
                    continue
                
                await self._manage_single_position(symbol)
                
        except Exception as e:
            logger.error(f"❌ خطأ في إدارة الصفقات: {e}")
    
    async def _manage_single_position(self, symbol: str):
        """إدارة صفقة فردية"""
        try:
            position = self.active_positions[symbol]
            
            # 1. تحديث بيانات الصفقة
            current_price = await self.binance.get_current_price(symbol)
            position['current_price'] = current_price
            position['last_update'] = datetime.now()
            
            # حساب PnL الحالي
            pnl_info = self._calculate_current_pnl(position)
            position.update(pnl_info)
            
            # 2. حساب قرارات المخاطرة
            risk_actions = await self.risk.calculate_actions(position)
            
            # 3. تنفيذ القرارات
            for action in risk_actions:
                await self._execute_risk_action(action, position)
                
        except Exception as e:
            logger.error(f"❌ خطأ في إدارة الصفقة {symbol}: {e}")
    
    async def _execute_risk_action(self, action: dict, position: dict):
        """تنفيذ قرار المخاطرة"""
        try:
            symbol = position['symbol']
            
            if action['type'] in ['PARTIAL_STOP_LOSS', 'FULL_STOP_LOSS', 'TAKE_PROFIT']:
                # تنفيذ أمر إغلاق
                result = await self.binance.close_position(
                    symbol=symbol,
                    quantity=action['quantity'],
                    reason=action['type']
                )
                
                if result['success']:
                    # تحديث الإحصائيات
                    self._update_performance_stats(action, position)
                    
                    # إرسال إشعار
                    await self.notifier.send_trade_update(position, action, result)
                    
                    # إذا كان إغلاقاً كاملاً، إزالة الصفقة
                    if action['type'] == 'FULL_STOP_LOSS':
                        if symbol in self.active_positions:
                            del self.active_positions[symbol]
                    else:
                        # تحديث كمية الصفقة المتبقية
                        position['quantity'] -= action['quantity']
                        
                else:
                    logger.error(f"❌ فشل تنفيذ الإجراء: {action['type']} للرمز {symbol}")
                    
        except Exception as e:
            logger.error(f"❌ خطأ في تنفيذ الإجراء: {e}")
    
    def _calculate_current_pnl(self, position: dict) -> dict:
        """حساب PnL الحالي للصفقة"""
        entry_price = position['entry_price']
        current_price = position['current_price']
        quantity = position['quantity']
        side = position['side']
        
        if side == 'LONG':
            pnl = (current_price - entry_price) * quantity
            pnl_percent = (current_price / entry_price - 1) * 100
        else:  # SHORT
            pnl = (entry_price - current_price) * quantity
            pnl_percent = (1 - current_price / entry_price) * 100
        
        return {
            'pnl': pnl,
            'pnl_percent': pnl_percent
        }
    
    def _update_performance_stats(self, action: dict, position: dict):
        """تحديث إحصائيات الأداء"""
        if action['type'] == 'TAKE_PROFIT':
            self.performance_stats['total_take_profits'] += 1
            self.performance_stats['winning_trades'] += 1
        elif action['type'] in ['PARTIAL_STOP_LOSS', 'FULL_STOP_LOSS']:
            self.performance_stats['total_stop_losses'] += 1
            if action['type'] == 'FULL_STOP_LOSS':
                self.performance_stats['losing_trades'] += 1
        
        # تحديث إجمالي PnL
        self.performance_stats['total_pnl'] += position.get('pnl', 0)
    
    async def _schedule_margin_monitoring(self):
        """مراقبة الهامش كل دقيقة"""
        logger.info("⏰ بدء جدولة مراقبة الهامش (كل دقيقة)")
        
        while self.is_running:
            try:
                await self._check_margin_health()
                await asyncio.sleep(60)  # انتظار دقيقة
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ خطأ في مراقبة الهامش: {e}")
                await asyncio.sleep(60)
    
    async def _check_margin_health(self):
        """فحص صحة الهامش"""
        try:
            margin_info = await self.binance.get_margin_info()
            
            if margin_info['margin_ratio'] > self.config['risk']['margin_risk_threshold']:
                warning_msg = (
                    f"🚨 تحذير هامش: نسبة الهامش {margin_info['margin_ratio']:.2f}% "
                    f"تجاوزت الحد {self.config['risk']['margin_risk_threshold']}%"
                )
                logger.warning(warning_msg)
                await self.notifier.send_message(warning_msg)
                
        except Exception as e:
            logger.error(f"❌ خطأ في فحص الهامش: {e}")
    
    async def _schedule_levels_check(self):
        """فحص مستويات وقف الخسارة وجني الأرباح كل 10 ثواني"""
        logger.info("⏰ بدء جدولة فحص المستويات (كل 10 ثواني)")
        
        while self.is_running:
            try:
                # فحص جميع الصفقات النشطة
                for symbol in list(self.active_positions.keys()):
                    await self._manage_single_position(symbol)
                
                await asyncio.sleep(10)  # انتظار 10 ثواني
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ خطأ في فحص المستويات: {e}")
                await asyncio.sleep(10)
    
    async def _schedule_performance_report(self):
        """تقرير الأداء كل 6 ساعات"""
        logger.info("⏰ بدء جدولة تقارير الأداء (كل 6 ساعات)")
        
        while self.is_running:
            try:
                await self._send_performance_report()
                await asyncio.sleep(6 * 60 * 60)  # انتظار 6 ساعات
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ خطأ في إرسال تقرير الأداء: {e}")
                await asyncio.sleep(6 * 60 * 60)
    
    async def _send_performance_report(self):
        """إرسال تقرير الأداء"""
        try:
            report = self._generate_performance_report()
            await self.notifier.send_performance_report(report)
        except Exception as e:
            logger.error(f"❌ خطأ في إنشاء تقرير الأداء: {e}")
    
    def _generate_performance_report(self) -> dict:
        """إنشاء تقرير الأداء"""
        total_trades = self.performance_stats['winning_trades'] + self.performance_stats['losing_trades']
        win_rate = (self.performance_stats['winning_trades'] / total_trades * 100) if total_trades > 0 else 0
        
        return {
            'timestamp': datetime.now(),
            'active_positions': len(self.active_positions),
            'total_managed': self.performance_stats['total_managed'],
            'winning_trades': self.performance_stats['winning_trades'],
            'losing_trades': self.performance_stats['losing_trades'],
            'win_rate': win_rate,
            'total_take_profits': self.performance_stats['total_take_profits'],
            'total_stop_losses': self.performance_stats['total_stop_losses'],
            'total_pnl': self.performance_stats['total_pnl'],
            'performance_breakdown': {
                'take_profit_ratio': (
                    self.performance_stats['total_take_profits'] / 
                    (self.performance_stats['total_take_profits'] + self.performance_stats['total_stop_losses'])
                    if (self.performance_stats['total_take_profits'] + self.performance_stats['total_stop_losses']) > 0 
                    else 0
                )
            }
        }
    
    async def _schedule_state_save(self):
        """حفظ حالة النظام كل 10 دقائق"""
        logger.info("⏰ بدء جدولة حفظ الحالة (كل 10 دقائق)")
        
        while self.is_running:
            try:
                await self._save_current_state()
                await asyncio.sleep(10 * 60)  # انتظار 10 دقائق
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ خطأ في حفظ الحالة: {e}")
                await asyncio.sleep(10 * 60)
    
    async def _save_current_state(self):
        """حفظ الحالة الحالية للنظام"""
        # هنا يمكن حفظ الحالة في ملف أو قاعدة بيانات
        state = {
            'timestamp': datetime.now(),
            'active_positions': self.active_positions,
            'performance_stats': self.performance_stats,
            'is_running': self.is_running
        }
        logger.debug("💾 تم حفظ حالة النظام")
    
    async def force_sync(self):
        """مزامنة يدوية مع Binance"""
        logger.info("🔃 بدء المزامنة اليدوية")
        await self._initial_sync()
    
    def get_status(self) -> dict:
        """الحصول على حالة النظام"""
        return {
            'is_running': self.is_running,
            'active_positions_count': len(self.active_positions),
            'performance_stats': self.performance_stats,
            'last_update': datetime.now()
        }

# نموذج إعدادات التشغيل
DEFAULT_CONFIG = {
    'symbols': ['BNBUSDT', 'ETHUSDT'],
    'binance': {
        'api_key': 'YOUR_API_KEY',
        'api_secret': 'YOUR_API_SECRET',
        'testnet': False  # استخدام testnet للتجربة
    },
    'risk': {
        'margin_risk_threshold': 70,
        'partial_stop_percent': 0.3,
        'partial_trigger_percent': 0.4,
        'min_stop_loss': 0.015,
        'max_stop_loss': 0.05,
        'take_profit_levels': [
            {'profit': 0.0025, 'close': 0.5},
            {'profit': 0.0030, 'close': 0.3},
            {'profit': 0.0035, 'close': 0.2}
        ]
    },
    'notifications': {
        'telegram_bot_token': 'YOUR_TELEGRAM_BOT_TOKEN',
        'telegram_chat_id': 'YOUR_CHAT_ID'
    }
}

async def main():
    """الدالة الرئيسية لتشغيل النظام"""
    manager = TradeManager(DEFAULT_CONFIG)
    
    try:
        await manager.start()
        
        # البقاء في حالة تشغيل
        while manager.is_running:
            await asyncio.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("⏹️  إيقاف النظام بواسطة المستخدم")
    except Exception as e:
        logger.error(f"❌ خطأ غير متوقع: {e}")
    finally:
        await manager.stop()

if __name__ == "__main__":
    asyncio.run(main())
