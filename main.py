import asyncio
import logging
import os
from trade_manager import TradeManager
from binance_engine import BinanceEngine
from risk_engine import RiskEngine
from notification_manager import NotificationManager

# إعداد التسجيل
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('auto_trade_manager.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# إعدادات التطبيق
APP_CONFIG = {
    'symbols': ['BNBUSDT', 'ETHUSDT'],
    
    'binance': {
        'api_key': os.getenv('BINANCE_API_KEY', ''),
        'api_secret': os.getenv('BINANCE_API_SECRET', ''),
        'testnet': os.getenv('BINANCE_TESTNET', 'true').lower() == 'true'
    },
    
    'risk': {
        'partial_stop_percent': 0.3,
        'partial_trigger_percent': 0.4,
        'min_stop_loss': 0.015,
        'max_stop_loss': 0.05,
        'volatility_multiplier': 1.5,
        'margin_risk_threshold': 70,
        'take_profit_levels': [
            {'profit': 0.0025, 'close': 0.5},
            {'profit': 0.0030, 'close': 0.3},
            {'profit': 0.0035, 'close': 0.2}
        ]
    },
    
    'notifications': {
        'telegram_bot_token': os.getenv('TELEGRAM_BOT_TOKEN', ''),
        'telegram_chat_id': os.getenv('TELEGRAM_CHAT_ID', ''),
        'api_keys': os.getenv('API_KEYS', '').split(',') if os.getenv('API_KEYS') else [],
        'api_host': os.getenv('API_HOST', '0.0.0.0'),
        'api_port': int(os.getenv('API_PORT', '8000'))
    }
}

async def main():
    """الدالة الرئيسية لتشغيل النظام المتكامل"""
    logger.info("🚀 بدء تشغيل نظام مدير الصفقات التلقائي")
    
    # تعريف المتغيرات خارج try block
    trade_manager = None
    binance_engine = None
    risk_engine = None
    notification_manager = None
    
    try:
        # 1. تهيئة محرك Binance أولاً
        logger.info("🔧 تهيئة محرك Binance...")
        binance_engine = BinanceEngine(APP_CONFIG['binance'])
        if not await binance_engine.initialize():
            logger.error("❌ فشل تهيئة محرك Binance")
            return
        
        # 2. اختبار اتصال Binance
        if not await binance_engine.test_connection():
            logger.error("❌ فشل اختبار اتصال Binance")
            return

        # 3. تهيئة محرك المخاطرة مع تمرير binance_engine
        logger.info("🔧 تهيئة محرك المخاطرة...")
        risk_engine = RiskEngine(config=APP_CONFIG['risk'], binance_engine=binance_engine)
        
        # 4. تهيئة مدير الإشعارات
        logger.info("🔧 تهيئة مدير الإشعارات...")
        notification_manager = NotificationManager(APP_CONFIG['notifications'])
        await notification_manager.initialize()
        
        # 5. إنشاء مدير الصفقات الرئيسي
        logger.info("🔧 تهيئة مدير الصفقات...")
        trade_manager = TradeManager(APP_CONFIG)
        
        # 6. حقن التبعيات في trade_manager
        trade_manager.binance = binance_engine
        trade_manager.risk = risk_engine
        trade_manager.notifier = notification_manager
        
        # 7. بدء النظام
        logger.info("🚀 بدء تشغيل النظام...")
        await trade_manager.start()
        
        logger.info("✅ تم بدء جميع مكونات النظام بنجاح")
        
        # 8. البقاء في حالة تشغيل
        while trade_manager.is_running:
            await asyncio.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("⏹️  إيقاف النظام بواسطة المستخدم")
    except Exception as e:
        logger.error(f"❌ خطأ غير متوقع: {e}")
    finally:
        # 9. التنظيف الآمن
        logger.info("🧹 تنظيف الموارد...")
        try:
            if trade_manager:
                await trade_manager.stop()
        except Exception as e:
            logger.error(f"❌ خطأ في إيقاف trade_manager: {e}")
        
        try:
            if binance_engine:
                await binance_engine.close()
        except Exception as e:
            logger.error(f"❌ خطأ في إغلاق binance_engine: {e}")
        
        try:
            if notification_manager:
                await notification_manager.close()
        except Exception as e:
            logger.error(f"❌ خطأ في إغلاق notification_manager: {e}")

if __name__ == "__main__":
    asyncio.run(main())
