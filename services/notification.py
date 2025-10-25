import requests
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{token}"
        self._test_connection()
    
    def _test_connection(self) -> bool:
        try:
            if not self.token or not self.chat_id:
                logger.error("❌ مفاتيح Telegram غير موجودة")
                return False
            
            response = requests.get(f"{self.base_url}/getMe", timeout=10)
            if response.status_code == 200:
                logger.info("✅ اتصال Telegram نشط")
                return True
            return False
        except Exception as e:
            logger.error(f"❌ خطأ في اختبار Telegram: {e}")
            return False
    
    def send_message(self, message: str, message_type: str = 'info') -> bool:
        try:
            if not message or len(message.strip()) == 0:
                return False
            
            if len(message) > 4096:
                message = message[:4090] + "..."
            
            payload = {
                'chat_id': self.chat_id,
                'text': message,
                'parse_mode': 'HTML',
                'disable_web_page_preview': True
            }
            
            response = requests.post(f"{self.base_url}/sendMessage", json=payload, timeout=15)
            return response.status_code == 200
            
        except Exception as e:
            logger.error(f"❌ خطأ في إرسال رسالة Telegram: {e}")
            return False
