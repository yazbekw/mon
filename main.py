import os
import time
import logging
import threading
from datetime import datetime
from flask import Flask, jsonify
from dotenv import load_dotenv

from config.settings import AppSettings
from services.binance_client import BinanceClient
from services.notification import TelegramNotifier
from core.trade_manager import TradeManager

# تحميل المتغيرات البيئية
load_dotenv()

# إعداد التسجيل
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('trade_manager.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
settings = AppSettings()

class TradingBot:
    _instance = None
    
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def __init__(self):
        if TradingBot._instance is not None:
            raise Exception("هذه الفئة تستخدم نمط Singleton")
        
        # التحقق من المتغيرات البيئية
        self.api_key = os.environ.get('BINANCE_API_KEY')
        self.api_secret = os.environ.get('BINANCE_API_SECRET')
        self.telegram_token = os.environ.get('TELEGRAM_BOT_TOKEN')
        self.telegram_chat_id = os.environ.get('TELEGRAM_CHAT_ID')
        
        if not all([self.api_key, self.api_secret]):
            raise ValueError("❌ مفاتيح Binance مطلوبة")
        
        # تهيئة الخدمات
        self.binance_client = BinanceClient(self.api_key, self.api_secret)
        self.notifier = TelegramNotifier(self.telegram_token, self.telegram_chat_id)
        self.trade_manager = TradeManager(self.binance_client, self.notifier)
        
        TradingBot._instance = self
        logger.info("✅ تم تهيئة مدير الصفقات بنجاح")
    
    def start(self):
        """بدء تشغيل البوت"""
        try:
            # اختبار الاتصالات
            self._test_connections()
            
            # المزامنة الأولية مع الصفقات المفتوحة
            active_count = self.trade_manager.sync_with_binance()
            logger.info(f"🔄 بدء إدارة {active_count} صفقة نشطة")
            
            # إرسال إشعار البدء
            self._send_startup_notification(active_count)
            
            return True
            
        except Exception as e:
            logger.error(f"❌ خطأ في بدء التشغيل: {e}")
            return False
    
    def _test_connections(self):
        """اختبار جميع الاتصالات"""
        # اختبار Telegram
        telegram_ok = self.notifier.send_message("🧪 اختبار اتصال البوت - جاهز للعمل! ✅")
        if not telegram_ok:
            logger.warning("⚠️ تحذير: إشعارات Telegram لا تعمل")
        
        # اختبار الهامش
        margin_info = self.binance_client.get_margin_info()
        if margin_info:
            logger.info(f"✅ نسبة الهامش: {margin_info['margin_ratio']:.2%}")
    
    def _send_startup_notification(self, active_count: int):
        """إرسال إشعار بدء التشغيل"""
        message = (
            f"🚀 <b>بدء تشغيل مدير الصفقات المتكامل</b>\n"
            f"الوظيفة: إدارة وقف الخسارة وجني الأرباح تلقائياً\n"
            f"تقنية الوقف: ديناميكي مزدوج (جزئي + كامل)\n"
            f"المزامنة: تلقائية مع Binance\n"
            f"الصفقات النشطة: {active_count}\n"
            f"الحالة: جاهز للمراقبة ✅\n"
            f"الوقت: {datetime.now(settings.damascus_tz).strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        self.notifier.send_message(message)
    
    def run_management_loop(self):
        """حلقة الإدارة الرئيسية"""
        last_sync = datetime.now(settings.damascus_tz)
        last_margin_check = datetime.now(settings.damascus_tz)
        last_report = datetime.now(settings.damascus_tz)
        
        logger.info("🔄 بدء حلقة إدارة الصفقات...")
        
        while True:
            try:
                current_time = datetime.now(settings.damascus_tz)
                
                # فحص الصفقات المدارة
                self.trade_manager.check_managed_trades()
                
                # مراقبة الهامش كل دقيقة
                if (current_time - last_margin_check).seconds >= settings.margin_check_interval:
                    margin_info = self.binance_client.get_margin_info()
                    if margin_info and margin_info['is_risk_high']:
                        logger.warning(f"🚨 مستوى خطورة مرتفع: {margin_info['margin_ratio']:.2%}")
                    last_margin_check = current_time
                
                # مزامنة الصفقات كل 5 دقائق
                if (current_time - last_sync).seconds >= settings.sync_interval:
                    self.trade_manager.sync_with_binance()
                    last_sync = current_time
                
                # تقرير الأداء كل 6 ساعات
                if (current_time - last_report).seconds >= settings.report_interval:
                    self.trade_manager.send_performance_report()
                    last_report = current_time
                
                time.sleep(settings.check_interval)
                
            except KeyboardInterrupt:
                logger.info("⏹️ إيقاف البوت يدوياً...")
                break
            except Exception as e:
                logger.error(f"❌ خطأ في حلقة الإدارة: {e}")
                time.sleep(30)

# واجهات Flask API
@app.route('/')
def health_check():
    try:
        bot = TradingBot.get_instance()
        status = {
            'status': 'running',
            'managed_trades': len(bot.trade_manager.managed_trades),
            'performance_stats': bot.trade_manager.performance_stats,
            'timestamp': datetime.now(settings.damascus_tz).isoformat()
        }
        return jsonify(status)
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/api/status')
def get_status():
    try:
        bot = TradingBot.get_instance()
        status = {
            'managed_trades': list(bot.trade_manager.managed_trades.keys()),
            'performance_stats': bot.trade_manager.performance_stats,
            'timestamp': datetime.now(settings.damascus_tz).isoformat()
        }
        return jsonify(status)
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/api/sync', methods=['POST'])
def sync_positions():
    try:
        bot = TradingBot.get_instance()
        count = bot.trade_manager.sync_with_binance()
        return jsonify({
            'success': True,
            'message': f'تمت مزامنة {count} صفقة',
            'synced_positions': count
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

def run_flask():
    """تشغيل تطبيق Flask"""
    port = int(os.environ.get('PORT', 10001))
    app.run(host='0.0.0.0', port=port, debug=False)

def main():
    """الدالة الرئيسية"""
    try:
        # تهيئة البوت
        bot = TradingBot.get_instance()
        
        # بدء التشغيل
        if bot.start():
            # تشغيل Flask في thread منفصل
            flask_thread = threading.Thread(target=run_flask, daemon=True)
            flask_thread.start()
            
            logger.info("🚀 بدء تشغيل مدير الصفقات المتكامل...")
            logger.info(f"🌐 تطبيق Flask يعمل على المنفذ: {os.environ.get('PORT', 10001)}")
            
            # بدء حلقة الإدارة
            bot.run_management_loop()
        else:
            logger.error("❌ فشل بدء تشغيل البوت")
            
    except Exception as e:
        logger.error(f"❌ فشل تشغيل البوت: {e}")

if __name__ == "__main__":
    main()
