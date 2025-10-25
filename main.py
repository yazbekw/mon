# main_render.py (بديل لـ main.py)
import os
import time
import logging
import multiprocessing
from datetime import datetime
from flask import Flask, jsonify
from dotenv import load_dotenv

from config.settings import AppSettings
from services.binance_client import BinanceClient
from services.notification import TelegramNotifier
from core.trade_manager import TradeManager

load_dotenv()

# إعداد التسجيل
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()  # في Render نستخدم StreamHandler فقط
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
        self.api_key = os.environ.get('BINANCE_API_KEY')
        self.api_secret = os.environ.get('BINANCE_API_SECRET')
        self.telegram_token = os.environ.get('TELEGRAM_BOT_TOKEN')
        self.telegram_chat_id = os.environ.get('TELEGRAM_CHAT_ID')
        
        if not all([self.api_key, self.api_secret]):
            logger.error("❌ مفاتيح Binance مطلوبة")
            return
        
        try:
            self.binance_client = BinanceClient(self.api_key, self.api_secret)
            self.notifier = TelegramNotifier(self.telegram_token, self.telegram_chat_id)
            self.trade_manager = TradeManager(self.binance_client, self.notifier)
            
            TradingBot._instance = self
            logger.info("✅ تم تهيئة مدير الصفقات بنجاح")
        except Exception as e:
            logger.error(f"❌ فشل تهيئة البوت: {e}")
    
    def start(self):
        try:
            active_count = self.trade_manager.sync_with_binance()
            logger.info(f"🔄 بدء إدارة {active_count} صفقة نشطة")
            
            # إرسال إشعار البدء
            message = f"🚀 بدء تشغيل البوت على Render - الصفقات النشطة: {active_count}"
            self.notifier.send_message(message)
            
            return True
        except Exception as e:
            logger.error(f"❌ فشل بدء البوت: {e}")
            return False
    
    def run_management_loop(self):
        """حلقة إدارة مبسطة للاستقرار"""
        logger.info("🔄 بدء حلقة إدارة الصفقات...")
        
        while True:
            try:
                self.trade_manager.check_managed_trades()
                time.sleep(10)  # فحص كل 10 ثواني
            except Exception as e:
                logger.error(f"❌ خطأ في حلقة الإدارة: {e}")
                time.sleep(30)  # انتظار أطول عند الخطأ

def run_bot():
    """تشغيل البوت في process منفصل"""
    bot = TradingBot.get_instance()
    if bot and bot.start():
        bot.run_management_loop()

def run_flask():
    """تشغيل Flask"""
    port = int(os.environ.get('PORT', 10001))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

@app.route('/')
def home():
    return jsonify({
        'status': 'running',
        'service': 'Trade Manager Bot',
        'timestamp': datetime.now(settings.damascus_tz).isoformat()
    })

@app.route('/health')
def health():
    try:
        bot = TradingBot.get_instance()
        return jsonify({
            'status': 'healthy',
            'managed_trades': len(bot.trade_manager.managed_trades) if bot else 0
        })
    except:
        return jsonify({'status': 'unhealthy'}), 500

if __name__ == "__main__":
    # في Render، نبدأ كل شيء في processes منفصلة
    bot_process = multiprocessing.Process(target=run_bot)
    bot_process.daemon = True
    bot_process.start()
    
    # تشغيل Flask في Process الرئيسي
    run_flask()
