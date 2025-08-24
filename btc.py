import schedule
import time
import yfinance as yf
import numpy as np
import pandas as pd
from datetime import datetime
import requests
import pytz
import os
import logging
from flask import Flask
import threading
import gc
from functools import lru_cache
import signal

# إعداد logging متقدم
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('trading_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# استخدام متغيرات البيئة فقط للأمان
TELEGRAM_BOT_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
TELEGRAM_CHAT_ID = os.environ['TELEGRAM_CHAT_ID']

# تعريف الأصول التي تتابعها
ASSETS = ["BTC-USD", "ETH-USD", "BNB-USD", "XRP-USD", "ADA-USD"]

# تحديد توقيت دمشق
DAMASCUS_TZ = pytz.timezone('Asia/Damascus')

# ========== أوقات الشراء المثلى ==========
BUY_TIMES = [
    {"days": ["tuesday", "wednesday", "thursday"], "start": "01:00"},
    {"days": ["tuesday", "wednesday", "thursday"], "start": "15:00"},
    {"days": ["monday", "friday"], "start": "13:00"},
    {"days": ["sunday", "saturday"], "start": "01:00"},
    {"days": ["saturday"], "start": "16:00"}
]

# ========== أوقات البيع المثلى ==========
SELL_TIMES = [
    {"days": ["sunday", "monday"], "start": "17:00"},
    {"days": ["monday"], "start": "00:00"},
    {"days": ["monday"], "start": "07:00"},
    {"days": ["friday"], "start": "00:00"},
    {"days": ["friday"], "start": "05:00"},
    {"days": ["saturday"], "start": "21:00"},
    {"days": ["tuesday", "wednesday", "thursday"], "start": "08:00"}
]

# التحقق من أننا على Render
ON_RENDER = os.environ.get('RENDER', False)

# إنشاء تطبيق Flask
app = Flask(__name__)

# متغيرات التتبع
system_uptime = time.time()
error_count = 0
successful_cycles = 0

def send_telegram_message(message):
    """إرسال رسالة عبر Telegram مع معالجة الأخطاء"""
    if len(message) > 4000:
        message = message[:4000] + "...\n\n📋 الرسالة طويلة جداً، تم تقصيرها"
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            logger.info("✅ تم إرسال الإشعار إلى Telegram")
            return True
        else:
            logger.error(f"❌ خطأ في إرسال الرسالة: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        logger.error(f"❌ خطأ في الاتصال: {e}")
        return False

def calculate_rsi(prices, period=14):
    """حساب مؤشر RSI باستخدام pandas لتحسين الدقة"""
    if len(prices) < period + 1:
        return np.array([50] * len(prices))
    
    # تحويل إلى سلسلة pandas للاستفادة من الدوال المضمنة
    prices_series = pd.Series(prices)
    deltas = prices_series.diff()
    
    gains = deltas.where(deltas > 0, 0)
    losses = -deltas.where(deltas < 0, 0)
    
    # حساب المتوسطات الأسية
    avg_gains = gains.ewm(alpha=1/period, min_periods=period).mean()
    avg_losses = losses.ewm(alpha=1/period, min_periods=period).mean()
    
    # تجنب القسمة على الصفر
    avg_losses = avg_losses.where(avg_losses > 0, 0.0001)
    
    rs = avg_gains / avg_losses
    rsi = 100 - (100 / (1 + rs))
    
    # ملء القيم الأولى بالقيمة المحايدة
    rsi[:period] = 50
    
    return rsi.values

def calculate_macd(prices, fast=12, slow=26, signal=9):
    """حساب مؤشر MACD"""
    prices_series = pd.Series(prices)
    
    exp1 = prices_series.ewm(span=fast).mean()
    exp2 = prices_series.ewm(span=slow).mean()
    
    macd = exp1 - exp2
    signal_line = macd.ewm(span=signal).mean()
    histogram = macd - signal_line
    
    return macd.iloc[-1], signal_line.iloc[-1], histogram.iloc[-1]

def calculate_volatility(prices, period=20):
    """حساب التقلب باستخدام الانحراف المعياري"""
    if len(prices) < period:
        return 0
    
    returns = np.diff(prices) / prices[:-1]
    return np.std(returns) * np.sqrt(252)  # التقلب السنوي

def calculate_risk_ratio(rsi, volatility):
    """حساب نسبة المخاطرة بناءً على RSI والتقلب"""
    # معادلة مبسطة لحساب نسبة المخاطرة
    rsi_factor = abs(rsi - 50) / 50  # 0 إلى 1 (كلما ابتعد RSI عن 50 زادت المخاطرة)
    risk_ratio = rsi_factor * volatility * 100  # تحويل إلى نسبة مئوية
    
    return min(risk_ratio, 100)  # الحد الأقصى 100%

@lru_cache(maxsize=32)
def get_cached_market_data(symbol, timestamp):
    """الحصول على بيانات السوق مع التخزين المؤقت"""
    # timestamp يستخدم لضمان تحديث البيانات بشكل دوري
    return get_market_data(symbol)

def get_market_data(symbol):
    """جلب بيانات السوق مع معالجة الأخطاء"""
    try:
        stock = yf.Ticker(symbol)
        hist = stock.history(period="1mo", interval="1d")
        
        if len(hist) < 15:
            logger.warning(f"⚠️ بيانات غير كافية لـ {symbol}")
            return None, None, None, None, None
            
        current_price = hist['Close'].iloc[-1]
        rsi_values = calculate_rsi(hist['Close'].values)
        current_rsi = rsi_values[-1]
        
        # سعر الأمس للتغيير
        prev_price = hist['Close'].iloc[-2] if len(hist) > 1 else current_price
        price_change = ((current_price - prev_price) / prev_price) * 100
        
        # حساب التقلب
        volatility = calculate_volatility(hist['Close'].values)
        
        # حساب MACD
        macd, signal, histogram = calculate_macd(hist['Close'].values)
        
        return current_price, current_rsi, price_change, volatility, macd
        
    except Exception as e:
        logger.error(f"❌ خطأ في جلب بيانات {symbol}: {e}")
        return None, None, None, None, None

def get_rsi_recommendation(rsi, is_buy_time, volatility):
    """الحصول على توصية بناءً على RSI والتقلب"""
    risk_ratio = calculate_risk_ratio(rsi, volatility)
    
    if is_buy_time:
        if rsi < 30:
            return "إشارة شراء قوية جداً", "🎯", "🟢", risk_ratio
        elif rsi < 35:
            return "إشارة شراء قوية", "👍", "🟢", risk_ratio
        elif rsi < 40:
            return "إشارة شراء جيدة", "📈", "🟡", risk_ratio
        else:
            return "تجنب الشراء (RSI مرتفع)", "⚠️", "🔴", risk_ratio
    else:
        if rsi > 70:
            return "إشارة بيع قوية جداً", "🎯", "🟢", risk_ratio
        elif rsi > 65:
            return "إشارة بيع قوية", "👍", "🟢", risk_ratio
        elif rsi > 60:
            return "إشارة بيع جيدة", "📈", "🟡", risk_ratio
        else:
            return "تجنب البيع (RSI منخفض)", "⚠️", "🔴", risk_ratio

def check_trading_opportunity(is_buy_time):
    """فحص فرص التداول وإرسال الإشعارات"""
    action = "شراء" if is_buy_time else "بيع"
    action_emoji = "🟢" if is_buy_time else "🔴"
    
    current_time = datetime.now(DAMASCUS_TZ).strftime("%Y-%m-%d %H:%M")
    timestamp = int(time.time() // 3600)  # تحديث كل ساعة للتخزين المؤقت
    
    message = f"{action_emoji} <b>إشعار تداول - وقت {action}</b>\n"
    message += f"⏰ <i>{current_time}</i>\n"
    message += "─" * 30 + "\n\n"
    
    assets_analyzed = 0
    
    for symbol in ASSETS:
        data = get_cached_market_data(symbol, timestamp)
        if all(x is not None for x in data):
            price, rsi, change, volatility, macd = data
            assets_analyzed += 1
            
            rec_text, rec_emoji, color_emoji, risk_ratio = get_rsi_recommendation(rsi, is_buy_time, volatility)
            change_emoji = "📈" if change >= 0 else "📉"
            change_sign = "+" if change >= 0 else ""
            
            message += f"{color_emoji} <b>{symbol}</b>\n"
            message += f"💰 السعر: ${price:,.2f} {change_emoji} {change_sign}{change:.2f}%\n"
            message += f"📊 RSI: {rsi:.1f}\n"
            message += f"📈 MACD: {macd:.4f}\n"
            message += f"🌪️ التقلب: {volatility:.2%}\n"
            message += f"⚠️ نسبة المخاطرة: {risk_ratio:.1f}%\n"
            message += f"📋 {rec_emoji} {rec_text}\n"
            message += "─" * 20 + "\n"
    
    if assets_analyzed > 0:
        message += f"\n📋 <i>تم تحليل {assets_analyzed} أصل</i>"
        
        # تقسيم الرسالة إذا كانت طويلة
        if len(message) > 2000:
            parts = [message[i:i+2000] for i in range(0, len(message), 2000)]
            for part in parts:
                send_telegram_message(part)
                time.sleep(1)
        else:
            send_telegram_message(message)
    else:
        logger.warning("⚠️ لا توجد بيانات متاحة للتحليل")
        send_telegram_message("⚠️ <b>لا توجد بيانات متاحة للتحليل حالياً</b>")

def send_daily_report():
    """إرسال تقرير يومي مختصر"""
    current_time = datetime.now(DAMASCUS_TZ).strftime("%Y-%m-%d %H:%M")
    timestamp = int(time.time() // 3600)  # تحديث كل ساعة للتخزين المؤقت
    
    message = f"📊 <b>التقرير اليومي المختصر</b>\n"
    message += f"⏰ <i>{current_time}</i>\n"
    message += "─" * 30 + "\n\n"
    
    assets_analyzed = 0
    
    for symbol in ASSETS:
        data = get_cached_market_data(symbol, timestamp)
        if all(x is not None for x in data):
            price, rsi, change, volatility, macd = data
            assets_analyzed += 1
            
            status = "🟢 منخفض" if rsi < 35 else "🔴 مرتفع" if rsi > 65 else "🟡 متعادل"
            change_emoji = "📈" if change >= 0 else "📉"
            
            message += f"• {status} <b>{symbol}</b>: ${price:,.2f} {change_emoji}\n"
    
    if assets_analyzed > 0:
        message += f"\n📈 RSI < 35: فرصة شراء\n"
        message += f"📉 RSI > 65: فرصة بيع\n"
        message += f"📋 {assets_analyzed} أصل تم تحليله"
        
        send_telegram_message(message)
    else:
        send_telegram_message("⚠️ <b>لا توجد بيانات للتقرير اليومي</b>")

def cleanup_memory():
    """تنظيف الذاكرة"""
    gc.collect()
    logger.info("🧹 تم تنظيف الذاكرة")

def schedule_notifications():
    """جدولة جميع الإشعارات مع منع التكرار"""
    if hasattr(schedule_notifications, 'executed'):
        return
    
    schedule_notifications.executed = True
    
    # جدولة أوقات الشراء
    for time_slot in BUY_TIMES:
        for day in time_slot["days"]:
            getattr(schedule.every(), day).at(time_slot["start"]).do(
                lambda: check_trading_opportunity(True)
            ).tag('trading', 'buy')

    # جدولة أوقات البيع
    for time_slot in SELL_TIMES:
        for day in time_slot["days"]:
            getattr(schedule.every(), day).at(time_slot["start"]).do(
                lambda: check_trading_opportunity(False)
            ).tag('trading', 'sell')

    # تقرير يومي الساعة 8 مساءً
    schedule.every().day.at("20:00").do(send_daily_report).tag('report', 'daily')
    
    # تنظيف الذاكرة يومياً الساعة 2 صباحاً
    schedule.every().day.at("02:00").do(cleanup_memory).tag('maintenance')

@app.route('/')
def home():
    return '''
    <h1>✅ Crypto Trading Bot is Running</h1>
    <p>Service: Active</p>
    <p>Type: Background Worker + Health Check</p>
    <p>Uptime: {} minutes</p>
    <p>Successful Cycles: {}</p>
    <p>Error Count: {}</p>
    '''.format(
        int((time.time() - system_uptime) / 60),
        successful_cycles,
        error_count
    )

@app.route('/health')
def health():
    return {
        'status': 'healthy',
        'service': 'crypto-trading-bot',
        'timestamp': datetime.now(DAMASCUS_TZ).isoformat(),
        'assets': ASSETS,
        'uptime_minutes': int((time.time() - system_uptime) / 60),
        'successful_cycles': successful_cycles,
        'error_count': error_count
    }

def run_web_server():
    """تشغيل خادم الويب للـ health checks"""
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"🌐 خادم الويب يعمل على port {port}")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

def graceful_shutdown(signum, frame):
    """إيقاف النظام بشكل آمن"""
    global successful_cycles, error_count
    
    stop_time = datetime.now(DAMASCUS_TZ)
    runtime = (stop_time - start_time)
    
    shutdown_msg = f"""⏹️ <b>إيقاف النظام</b>
⏰ وقت البدء: {start_time.strftime('%Y-%m-%d %H:%M')}
⏰ وقت الإيقاف: {stop_time.strftime('%Y-%m-%d %H:%M')}
⏱️ مدة التشغيل: {runtime}
✅ الدورات الناجحة: {successful_cycles}
❌ الأخطاء: {error_count}"""

    send_telegram_message(shutdown_msg)
    logger.info("⏹️ تم إيقاف النظام")
    exit(0)

def main():
    global start_time, successful_cycles, error_count
    
    try:
        # بدء التشغيل فوراً
        start_time = datetime.now(DAMASCUS_TZ)
        print("=" * 60)
        print("🚀 بدء تشغيل البوت على Render")
        print("=" * 60)
        
        # اختبار بسيط للمتغيرات
        if not TELEGRAM_BOT_TOKEN:
            print("❌ TELEGRAM_BOT_TOKEN غير موجود")
            return
        if not TELEGRAM_CHAT_ID:
            print("❌ TELEGRAM_CHAT_ID غير موجود")
            return
        
        # إرسال رسالة اختبار بسيطة
        test_msg = "🔍 اختبار اتصال من Render"
        success = send_telegram_message(test_msg)
        
        if success:
            print("✅ تم إرسال الرسالة بنجاح")
            # إرسال رسالة البدء الكاملة
            startup_msg = f"""🚀 <b>بدء تشغيل النظام</b>
⏰ الوقت: {start_time.strftime('%Y-%m-%d %H:%M:%S')}
✅ الحالة: يعمل بنجاح"""
            send_telegram_message(startup_msg)
        else:
            print("❌ فشل إرسال الرسالة")
            
    except Exception as e:
        print(f"💥 خطأ في التشغيل: {e}")
        


if __name__ == "__main__":
    main()

