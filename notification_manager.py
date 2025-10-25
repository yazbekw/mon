import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.responses import JSONResponse
import aiohttp
import uvicorn

logger = logging.getLogger(__name__)

class NotificationManager:
    """
    📢 مدير الإشعارات والواجهة البرمجية - مسؤول عن التواصل مع العالم الخارجي
    """

    def __init__(self, config: dict):
        self.config = config
        self.telegram_bot_token = config.get('telegram_bot_token')
        self.telegram_chat_id = config.get('telegram_chat_id')
        self.api_keys = config.get('api_keys', [])
        self.session: Optional[aiohttp.ClientSession] = None
        self.app = FastAPI(title="Auto Trade Manager API", version="1.0.0")
        self._setup_api_routes()
        
        logger.info("✅ تم تهيئة Notification Manager")

    def _setup_api_routes(self):
        """إعداد مسارات واجهة API"""
        
        @self.app.get("/")
        async def root():
            return {
                "status": "running", 
                "service": "Auto Trade Manager",
                "timestamp": datetime.utcnow().isoformat(),
                "version": "1.0.0"
            }

        @self.app.get("/health")
        async def health_check():
            return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

        @self.app.get("/api/management/status")
        async def get_management_status(api_key: str = Depends(self._verify_api_key)):
            """الحصول على حالة نظام الإدارة"""
            try:
                from trade_manager import trade_manager
                status = trade_manager.get_status()
                return {
                    "success": True,
                    "data": status,
                    "timestamp": datetime.utcnow().isoformat()
                }
            except Exception as e:
                logger.error(f"❌ خطأ في جلب حالة الإدارة: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/management/sync")
        async def manual_sync(api_key: str = Depends(self._verify_api_key)):
            """مزامنة يدوية مع Binance"""
            try:
                from trade_manager import trade_manager
                await trade_manager.force_sync()
                return {
                    "success": True,
                    "message": "تمت المزامنة اليدوية بنجاح",
                    "timestamp": datetime.utcnow().isoformat()
                }
            except Exception as e:
                logger.error(f"❌ خطأ في المزامنة اليدوية: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/management/close/{symbol}")
        async def close_position(symbol: str, api_key: str = Depends(self._verify_api_key)):
            """إغلاق صفقة يدوياً"""
            try:
                from trade_manager import trade_manager
                
                if symbol not in trade_manager.active_positions:
                    raise HTTPException(status_code=404, detail=f"لا توجد صفقة مفتوحة للرمز {symbol}")
                
                position = trade_manager.active_positions[symbol]
                result = await trade_manager.binance.close_position(
                    symbol=symbol,
                    quantity=position['quantity'],
                    reason="MANUAL_CLOSE"
                )
                
                if result['success']:
                    del trade_manager.active_positions[symbol]
                    await self.send_message(f"🔄 إغلاق يدوي للصفقة {symbol}")
                
                return {
                    "success": result['success'],
                    "data": result,
                    "timestamp": datetime.utcnow().isoformat()
                }
                
            except Exception as e:
                logger.error(f"❌ خطأ في الإغلاق اليدوي: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get("/api/debug/positions")
        async def debug_positions(api_key: str = Depends(self._verify_api_key)):
            """تصحيح وإظهار المراكز الحالية"""
            try:
                from trade_manager import trade_manager
                return {
                    "success": True,
                    "data": {
                        "active_positions": trade_manager.active_positions,
                        "count": len(trade_manager.active_positions)
                    },
                    "timestamp": datetime.utcnow().isoformat()
                }
            except Exception as e:
                logger.error(f"❌ خطأ في تصحيح المراكز: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/debug/telegram-test")
        async def test_telegram(api_key: str = Depends(self._verify_api_key)):
            """اختبار إرسال رسالة Telegram"""
            try:
                result = await self.send_message("🔍 اختبار إشعار Telegram - البوت يعمل بشكل صحيح")
                return {
                    "success": True,
                    "message": "تم إرسال رسالة الاختبار",
                    "data": result,
                    "timestamp": datetime.utcnow().isoformat()
                }
            except Exception as e:
                logger.error(f"❌ خطأ في اختبار Telegram: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get("/api/performance/stats")
        async def get_performance_stats(api_key: str = Depends(self._verify_api_key)):
            """الحصول على إحصائيات الأداء"""
            try:
                from trade_manager import trade_manager
                stats = trade_manager.performance_stats
                
                # حساب معدل الربح
                total_trades = stats['winning_trades'] + stats['losing_trades']
                win_rate = (stats['winning_trades'] / total_trades * 100) if total_trades > 0 else 0
                
                performance_data = {
                    "total_managed_trades": stats['total_managed'],
                    "active_positions": len(trade_manager.active_positions),
                    "winning_trades": stats['winning_trades'],
                    "losing_trades": stats['losing_trades'],
                    "win_rate": round(win_rate, 2),
                    "total_take_profits": stats['total_take_profits'],
                    "total_stop_losses": stats['total_stop_losses'],
                    "total_pnl": round(stats['total_pnl'], 4),
                    "timestamp": datetime.utcnow().isoformat()
                }
                
                return {
                    "success": True,
                    "data": performance_data
                }
                
            except Exception as e:
                logger.error(f"❌ خطأ في جلب إحصائيات الأداء: {e}")
                raise HTTPException(status_code=500, detail=str(e))

    async def _verify_api_key(self, x_api_key: str = Header(...)):
        """التحقق من صحة API Key"""
        if not self.api_keys:
            return True  # لا توجد مصادقة إذا لم يتم تحديد API Keys
            
        if x_api_key in self.api_keys:
            return True
            
        logger.warning(f"⚠️ محاولة وصول غير مصرح بها باستخدام API Key: {x_api_key}")
        raise HTTPException(status_code=401, detail="API Key غير صالح")

    async def initialize(self):
        """تهيئة جلسة HTTP"""
        self.session = aiohttp.ClientSession()
        logger.info("✅ تم تهيئة جلسة HTTP للإشعارات")

    async def close(self):
        """إغلاق الجلسة"""
        if self.session:
            await self.session.close()
        logger.info("🔌 تم إغلاق جلسة الإشعارات")

    async def send_message(self, message: str) -> bool:
        """
        إرسال رسالة إلى Telegram
        """
        if not self.telegram_bot_token or not self.telegram_chat_id:
            logger.warning("⚠️ إعدادات Telegram غير مكتملة - تخطي الإرسال")
            return False

        try:
            url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
            payload = {
                "chat_id": self.telegram_chat_id,
                "text": message,
                "parse_mode": "HTML"
            }

            async with self.session.post(url, json=payload) as response:
                if response.status == 200:
                    logger.debug("✅ تم إرسال رسالة Telegram بنجاح")
                    return True
                else:
                    error_text = await response.text()
                    logger.error(f"❌ فشل إرسال رسالة Telegram: {error_text}")
                    return False

        except Exception as e:
            logger.error(f"❌ خطأ في إرسال رسالة Telegram: {e}")
            return False

    async def send_new_position_alert(self, position: Dict):
        """إرسال إشعار بصفقة جديدة"""
        emoji = "🟢" if position['side'] == 'LONG' else "🔴"
        side_text = "شراء" if position['side'] == 'LONG' else "بيع"
        
        message = f"""
{emoji} <b>بدء إدارة صفقة جديدة</b>

<b>الرمز:</b> {position['symbol']}
<b>النوع:</b> {side_text}
<b>سعر الدخول:</b> {position['entry_price']:.4f}
<b>الكمية:</b> {position['quantity']:.4f}
<b>الرافعة:</b> {position.get('leverage', 'N/A')}x

⏰ <b>الوقت:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        """
        
        await self.send_message(message)

    async def send_trade_update(self, position: Dict, action: Dict, result: Dict):
        """إرسال تحديث عن تنفيذ إجراء"""
        action_emojis = {
            'PARTIAL_STOP_LOSS': '🛡️',
            'FULL_STOP_LOSS': '🔴',
            'TAKE_PROFIT': '💰',
            'MANUAL_CLOSE': '🔄'
        }
        
        emoji = action_emojis.get(action['type'], '📊')
        
        # حساب PnL النهائي
        pnl = position.get('pnl', 0)
        pnl_percent = position.get('pnl_percent', 0)
        pnl_emoji = "📈" if pnl >= 0 else "📉"
        
        message = f"""
{emoji} <b>تنفيذ إجراء تداول</b>

<b>الرمز:</b> {position['symbol']}
<b>الإجراء:</b> {action['reason']}
<b>الكمية:</b> {action['quantity']:.4f}
<b>السعر:</b> {action.get('price', position['current_price']):.4f}

{pnl_emoji} <b>الأداء:</b>
- PnL: {pnl:.4f} USDT
- PnL %: {pnl_percent:.2f}%

<b>الحالة:</b> {"✅ ناجح" if result.get('success', False) else "❌ فاشل"}

⏰ <b>الوقت:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        """
        
        await self.send_message(message)

    async def send_performance_report(self, report: Dict):
        """إرسال تقرير أداء دوري"""
        try:
            win_rate = report.get('win_rate', 0)
            active_positions = report.get('active_positions', 0)
            total_pnl = report.get('total_pnl', 0)
            
            performance_emoji = "🎯" if win_rate >= 60 else "📊" if win_rate >= 40 else "⚠️"
            pnl_emoji = "💰" if total_pnl >= 0 else "💸"
            
            message = f"""
{performance_emoji} <b>تقرير الأداء الدوري</b>

<b>الصفقات النشطة:</b> {active_positions}
<b>إجمالي الصفقات المدارة:</b> {report.get('total_managed', 0)}
<b>الصفقات الرابحة:</b> {report.get('winning_trades', 0)}
<b>الصفقات الخاسرة:</b> {report.get('losing_trades', 0)}
<b>معدل الربح:</b> {win_rate:.1f}%

{pnl_emoji} <b>الأداء المالي:</b>
- جني الأرباح: {report.get('total_take_profits', 0)} مرة
- وقف الخسارة: {report.get('total_stop_losses', 0)} مرة
- PnL الإجمالي: {total_pnl:.4f} USDT

⏰ <b>الفترة:</b> {report.get('timestamp', datetime.now()).strftime('%Y-%m-%d %H:%M')}
            """
            
            await self.send_message(message)
            
        except Exception as e:
            logger.error(f"❌ خطأ في إرسال تقرير الأداء: {e}")

    async def send_margin_alert(self, margin_info: Dict):
        """إرسال تحذير هامش"""
        margin_ratio = margin_info.get('margin_ratio', 0)
        alert_emoji = "🚨" if margin_ratio > 80 else "⚠️"
        
        message = f"""
{alert_emoji} <b>تحذير مستوى الهامش</b>

<b>نسبة استخدام الهامش:</b> {margin_ratio:.1f}%
<b>الرصيد المتاح:</b> {margin_info.get('available_balance', 0):.2f} USDT
<b>الرصيد الإجمالي:</b> {margin_info.get('total_margin_balance', 0):.2f} USDT

💡 <b>التوصية:</b> تقليل المراكز أو إضافة هامش

⏰ <b>الوقت:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        """
        
        await self.send_message(message)

    async def send_error_alert(self, error: str, context: str = ""):
        """إرسال تنبيه خطأ"""
        message = f"""
❌ <b>خطأ في النظام</b>

<b>السياق:</b> {context}
<b>الخطأ:</b> <code>{error}</code>

🔧 <b>الإجراء:</b> الفحص العاجل مطلوب

⏰ <b>الوقت:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        """
        
        await self.send_message(message)

    async def send_system_alert(self, title: str, message: str, alert_type: str = "INFO"):
        """إرسال تنبيه عام للنظام"""
        emojis = {
            "INFO": "ℹ️",
            "WARNING": "⚠️", 
            "ERROR": "❌",
            "SUCCESS": "✅"
        }
        
        emoji = emojis.get(alert_type, "📢")
        
        formatted_message = f"""
{emoji} <b>{title}</b>

{message}

⏰ <b>الوقت:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        """
        
        await self.send_message(formatted_message)

    def start_api_server(self, host: str = "0.0.0.0", port: int = 8000):
        """بدء خادم واجهة API"""
        try:
            logger.info(f"🌐 بدء خادم API على {host}:{port}")
            uvicorn.run(self.app, host=host, port=port, log_level="info")
        except Exception as e:
            logger.error(f"❌ خطأ في بدء خادم API: {e}")

# نموذج إعدادات التشغيل
DEFAULT_NOTIFICATION_CONFIG = {
    'telegram_bot_token': 'YOUR_TELEGRAM_BOT_TOKEN',
    'telegram_chat_id': 'YOUR_CHAT_ID',
    'api_keys': ['your-api-key-here'],  # قائمة API Keys المسموح بها
    'api_host': '0.0.0.0',
    'api_port': 8000
}

async def main():
    """اختبار نظام الإشعارات"""
    config = DEFAULT_NOTIFICATION_CONFIG
    
    notifier = NotificationManager(config)
    await notifier.initialize()
    
    try:
        # اختبار إرسال رسالة
        await notifier.send_message("🔍 اختبار نظام الإشعارات - البوت يعمل بشكل صحيح")
        
        # اختبار إشعار صفقة افتراضية
        test_position = {
            'symbol': 'BNBUSDT',
            'side': 'LONG',
            'entry_price': 300.0,
            'quantity': 0.1,
            'leverage': 50,
            'current_price': 305.0,
            'pnl': 0.5,
            'pnl_percent': 1.67
        }
        
        await notifier.send_new_position_alert(test_position)
        
        print("✅ تم اختبار نظام الإشعارات بنجاح")
        
    except Exception as e:
        print(f"❌ خطأ في اختبار الإشعارات: {e}")
    finally:
        await notifier.close()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
