import schedule
import time
import yfinance as yf
import numpy as np
from datetime import datetime
import requests
import pytz
import os
import logging
from flask import Flask, request
import threading
import signal
import pandas as pd

# ========== إعدادات الثوابت ==========
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

# تعريف الأصول التي تتابعها
ASSETS = ["BTC-USD", "ETH-USD", "BNB-USD", "XRP-USD", "ADA-USD"]

# تحديد توقيت دمشق
DAMASCUS_TZ = pytz.timezone('Asia/Damascus')

# إعدادات التوقيت
NOTIFICATION_COOLDOWN = 5  # 5 ثواني بين كل إشعارين
MAX_INACTIVITY = 3600  # 1 ساعة كحد أقصى للخمول

# إعدادات المؤشرات التقنية
RSI_PERIOD = 14
MA_SHORT_PERIOD = 20
MA_LONG_PERIOD = 50
SUPPORT_RESISTANCE_PERIOD = 20

# إعداد logging متقدم
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# التحقق من أننا على Render
ON_RENDER = os.environ.get('RENDER', False)

# إعدادات خاصة بـ Render
RENDER_SETTINGS = {
    "timeout": 20,
    "retries": 5,
    "backoff_factor": 1.5
}

# جلسة HTTP مشتركة
PERSISTENT_SESSION = None

# إنشاء تطبيق Flask
app = Flask(__name__)

# ========== الدوال المساعدة ==========
def create_persistent_session():
    """إنشاء جلسة HTTP متواصلة للأداء الأفضل"""
    session = requests.Session()
    # إعدادات مثلى للجلسة
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })
    return session

def send_telegram_message(message, max_retries=3):
    """إرسال رسالة عبر Telegram مع معالجة الأخطاء ومحاولات متعددة"""
    global PERSISTENT_SESSION
    
    # تقليل طول الرسالة إذا كانت طويلة جداً
    if len(message) > 4000:
        message = message[:4000] + "...\n\n📋 الرسالة طويلة جداً، تم تقصيرها"
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    
    for attempt in range(max_retries):
        try:
            # على Render، نستخدم وقت انتظار أطول
            timeout = 15 if ON_RENDER else 10
            
            if PERSISTENT_SESSION:
                response = PERSISTENT_SESSION.post(url, json=payload, timeout=timeout)
            else:
                response = requests.post(url, json=payload, timeout=timeout)
            
            if response.status_code == 200:
                logger.info("✅ تم إرسال الإشعار إلى Telegram")
                # إضافة تأخير بين الرسائل
                time.sleep(NOTIFICATION_COOLDOWN)
                return True
            else:
                logger.warning(f"⚠️ محاولة {attempt + 1}: خطأ {response.status_code}")
                if attempt < max_retries - 1:
                    time.sleep(2)  # انتظار قبل المحاولة التالية
                    
        except requests.exceptions.Timeout:
            logger.warning(f"⚠️ محاولة {attempt + 1}: انتهى الوقت")
            if attempt < max_retries - 1:
                time.sleep(3)
        except requests.exceptions.ConnectionError:
            logger.warning(f"⚠️ محاولة {attempt + 1}: خطأ اتصال")
            if attempt < max_retries - 1:
                time.sleep(5)
        except Exception as e:
            logger.error(f"❌ محاولة {attempt + 1}: خطأ غير متوقع - {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
    
    logger.error("❌ فشل جميع محاولات الإرسال")
    return False

def verify_telegram_connection():
    """التحقق من اتصال وصحة توكن Telegram"""
    global PERSISTENT_SESSION
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe"
    
    try:
        if PERSISTENT_SESSION:
            response = PERSISTENT_SESSION.get(url, timeout=10)
        else:
            response = requests.get(url, timeout=10)
            
        if response.status_code == 200:
            data = response.json()
            if data.get("ok"):
                logger.info("✅ التوكن صالح - البوت: @" + data["result"]["username"])
                return True
            else:
                logger.error("❌ التوكن غير صالح")
                return False
        else:
            logger.error(f"❌ خطأ في التحقق: {response.status_code}")
            return False
    except Exception as e:
        logger.error(f"❌ خطأ في الاتصال: {e}")
        return False

def diagnose_connection_issues():
    """تشخيص مشاكل الاتصال على Render"""
    global PERSISTENT_SESSION
    
    logger.info("🔍 بدء تشخيص مشاكل الاتصال...")
    
    tests = {
        "Telegram API": "https://api.telegram.org",
        "Yahoo Finance": "https://finance.yahoo.com",
        "Google": "https://www.google.com"
    }
    
    results = []
    
    for name, url in tests.items():
        try:
            if PERSISTENT_SESSION:
                response = PERSISTENT_SESSION.get(url, timeout=10)
            else:
                response = requests.get(url, timeout=10)
                
            if response.status_code == 200:
                results.append(f"✅ {name}: متصل")
            else:
                results.append(f"⚠️ {name}: خطأ {response.status_code}")
        except Exception as e:
            results.append(f"❌ {name}: فشل ({str(e)})")
    
    # اختبار الاتصال بـ Telegram بشكل خاص
    telegram_test = "❌ فشل اختبار Telegram"
    try:
        test_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe"
        if PERSISTENT_SESSION:
            response = PERSISTENT_SESSION.get(test_url, timeout=10)
        else:
            response = requests.get(test_url, timeout=10)
            
        if response.status_code == 200:
            telegram_test = "✅ اتصال Telegram: ناجح"
        else:
            telegram_test = f"⚠️ اتصال Telegram: خطأ {response.status_code}"
    except Exception as e:
        telegram_test = f"❌ اتصال Telegram: فشل ({str(e)})"
    
    results.append(telegram_test)
    
    # إرسال نتائج التشخيص
    diagnosis_message = "🔍 <b>تقرير تشخيص الاتصال</b>\n\n"
    diagnosis_message += "\n".join(results)
    diagnosis_message += f"\n\n🌐 البيئة: {'Render' if ON_RENDER else 'محلي'}"
    
    send_telegram_message(diagnosis_message)
    return diagnosis_message

def check_render_environment():
    """التحقق من إعدادات Render المحددة"""
    env_vars = {
        "RENDER": os.environ.get('RENDER', 'غير مضبوط'),
        "PORT": os.environ.get('PORT', 'غير مضبوط'),
        "PYTHON_VERSION": os.environ.get('PYTHON_VERSION', 'غير مضبوط'),
    }
    
    logger.info("🔍 التحقق من إعدادات Render:")
    for key, value in env_vars.items():
        logger.info(f"   {key}: {value}")
    
    return env_vars

def calculate_rsi(prices, period=RSI_PERIOD):
    """حساب مؤشر RSI بشكل دقيق ومعالجة الأخطاء"""
    if len(prices) < period + 1:
        return np.array([50] * len(prices))
    
    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    
    avg_gains = np.zeros_like(prices)
    avg_losses = np.zeros_like(prices)
    
    # القيم الأولية
    avg_gains[period] = np.mean(gains[:period])
    avg_losses[period] = np.mean(losses[:period])
    
    # معالجة حالة الصفر في الخسائر
    if avg_losses[period] == 0:
        avg_losses[period] = 0.0001  # تجنب القسمة على الصفر
    
    for i in range(period + 1, len(prices)):
        avg_gains[i] = (avg_gains[i-1] * (period-1) + gains[i-1]) / period
        avg_losses[i] = (avg_losses[i-1] * (period-1) + losses[i-1]) / period
        
        # تجنب القسمة على الصفر
        if avg_losses[i] == 0:
            avg_losses[i] = 0.0001
    
    rs = avg_gains / avg_losses
    rsi = 100 - (100 / (1 + rs))
    rsi[:period] = 50
    
    return rsi

def calculate_moving_averages(prices, short_period=MA_SHORT_PERIOD, long_period=MA_LONG_PERIOD):
    """حساب المتوسطات المتحركة"""
    if len(prices) < long_period:
        return None, None
    
    ma_short = np.convolve(prices, np.ones(short_period)/short_period, mode='valid')
    ma_long = np.convolve(prices, np.ones(long_period)/long_period, mode='valid')
    
    # جعل المصفوفات بنفس الطول
    if len(ma_short) > len(ma_long):
        ma_short = ma_short[-len(ma_long):]
    elif len(ma_long) > len(ma_short):
        ma_long = ma_long[-len(ma_short):]
    
    return ma_short, ma_long

def calculate_support_resistance(prices, period=SUPPORT_RESISTANCE_PERIOD):
    """حساب مستويات الدعم والمقاومة"""
    if len(prices) < period:
        return None, None
    
    # حساب الدعم والمقاومة باستخدام أعلى وأقل الأسعار في الفترة
    support = np.min(prices[-period:])
    resistance = np.max(prices[-period:])
    
    return support, resistance

def get_market_data(symbol):
    """جلب بيانات السوق مع معالجة الأخطاء"""
    global PERSISTENT_SESSION
    
    try:
        stock = yf.Ticker(symbol)
        hist = stock.history(period="2mo", interval="1d")  # زيادة الفترة للحصول على بيانات كافية
        
        if len(hist) < max(RSI_PERIOD, MA_LONG_PERIOD, SUPPORT_RESISTANCE_PERIOD) + 1:
            logger.warning(f"⚠️ بيانات غير كافية لـ {symbol}")
            return None, None, None, None, None, None, None
            
        current_price = hist['Close'].iloc[-1]
        rsi_values = calculate_rsi(hist['Close'].values)
        current_rsi = rsi_values[-1] if len(rsi_values) > 0 else 50
        
        # حساب المتوسطات المتحركة
        ma_short, ma_long = calculate_moving_averages(hist['Close'].values)
        current_ma_short = ma_short[-1] if ma_short is not None and len(ma_short) > 0 else current_price
        current_ma_long = ma_long[-1] if ma_long is not None and len(ma_long) > 0 else current_price
        
        # حساب الدعم والمقاومة
        support, resistance = calculate_support_resistance(hist['Close'].values)
        
        # سعر الأمس للتغيير
        prev_price = hist['Close'].iloc[-2] if len(hist) > 1 else current_price
        price_change = ((current_price - prev_price) / prev_price) * 100
        
        return current_price, current_rsi, price_change, current_ma_short, current_ma_long, support, resistance
        
    except Exception as e:
        logger.error(f"❌ خطأ في جلب بيانات {symbol}: {e}")
        return None, None, None, None, None, None, None

def get_trading_recommendation(price, rsi, ma_short, ma_long, support, resistance):
    """الحصول على توصية تداول بناءً على مؤشرات متعددة"""
    recommendations = []
    signals = []
    emojis = []
    
    # تحليل RSI
    if rsi < 30:
        recommendations.append("إشارة شراء قوية (RSI منخفض)")
        signals.append("شراء")
        emojis.append("🟢")
    elif rsi < 40:
        recommendations.append("إشارة شراء جيدة (RSI منخفض)")
        signals.append("شراء")
        emojis.append("🟡")
    elif rsi > 70:
        recommendations.append("إشارة بيع قوية (RSI مرتفع)")
        signals.append("بيع")
        emojis.append("🔴")
    elif rsi > 60:
        recommendations.append("إشارة بيع جيدة (RSI مرتفع)")
        signals.append("بيع")
        emojis.append("🟠")
    else:
        recommendations.append("RSI في منطقة محايدة")
        signals.append("محايد")
        emojis.append("⚪")
    
    # تحليل المتوسطات المتحركة
    if ma_short is not None and ma_long is not None:
        if ma_short > ma_long:
            recommendations.append("المتوسط القصير فوق الطويل (إيجابي)")
            signals.append("شراء")
            emojis.append("🟢")
        else:
            recommendations.append("المتوسط القصير تحت الطويل (سلبي)")
            signals.append("بيع")
            emojis.append("🔴")
    
    # تحليل الدعم والمقاومة
    if support is not None and resistance is not None:
        distance_to_support = abs(price - support) / price * 100
        distance_to_resistance = abs(price - resistance) / price * 100
        
        if distance_to_support < 2:  # قريب من الدعم
            recommendations.append("السعر قريب من مستوى الدعم")
            signals.append("شراء")
            emojis.append("🟢")
        elif distance_to_resistance < 2:  # قريب من المقاومة
            recommendations.append("السعر قريب من مستوى المقاومة")
            signals.append("بيع")
            emojis.append("🔴")
    
    # تحديد التوصية النهائية بناءً على الإشارات
    buy_signals = signals.count("شراء")
    sell_signals = signals.count("بيع")
    
    if buy_signals > sell_signals:
        final_recommendation = "توصية شراء"
        final_emoji = "🎯"
        final_color = "🟢"
    elif sell_signals > buy_signals:
        final_recommendation = "توصية بيع"
        final_emoji = "🎯"
        final_color = "🔴"
    else:
        final_recommendation = "محايد - الانتظار"
        final_emoji = "⚠️"
        final_color = "🟡"
    
    # تجميع التوصيات
    detailed_recommendation = " | ".join(recommendations)
    
    return final_recommendation, detailed_recommendation, final_emoji, final_color

def check_trading_opportunity():
    """فحص فرص التداول وإرسال الإشعارات"""
    current_time = datetime.now(DAMASCUS_TZ).strftime("%Y-%m-%d %H:%M")
    
    message = f"📊 <b>تحليل السوق الشامل</b>\n"
    message += f"⏰ <i>{current_time}</i>\n"
    message += "─" * 30 + "\n\n"
    
    assets_analyzed = 0
    
    for symbol in ASSETS:
        data = get_market_data(symbol)
        if all(x is not None for x in data[:3]):  # التحقق من البيانات الأساسية على الأقل
            price, rsi, change, ma_short, ma_long, support, resistance = data
            assets_analyzed += 1
            
            # الحصول على التوصية
            rec_text, detailed_rec, rec_emoji, color_emoji = get_trading_recommendation(
                price, rsi, ma_short, ma_long, support, resistance
            )
            
            change_emoji = "📈" if change >= 0 else "📉"
            change_sign = "+" if change >= 0 else ""
            
            message += f"{color_emoji} <b>{symbol}</b>\n"
            message += f"💰 السعر: ${price:,.2f} {change_emoji} {change_sign}{change:.2f}%\n"
            message += f"📊 RSI: {rsi:.1f}\n"
            
            if ma_short is not None and ma_long is not None:
                message += f"📈 المتوسطات: {ma_short:.2f} / {ma_long:.2f}\n"
            
            if support is not None and resistance is not None:
                message += f"⚖️ الدعم/المقاومة: {support:.2f} / {resistance:.2f}\n"
            
            message += f"🎯 {rec_text}: {detailed_rec}\n"
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
    
    message = f"📊 <b>التقرير اليومي المختصر</b>\n"
    message += f"⏰ <i>{current_time}</i>\n"
    message += "─" * 30 + "\n\n"
    
    assets_analyzed = 0
    
    for symbol in ASSETS:
        data = get_market_data(symbol)
        if all(x is not None for x in data[:3]):  # التحقق من البيانات الأساسية على الأقل
            price, rsi, change, ma_short, ma_long, support, resistance = data
            assets_analyzed += 1
            
            # الحصول على التوصية المختصرة
            rec_text, _, _, color_emoji = get_trading_recommendation(
                price, rsi, ma_short, ma_long, support, resistance
            )
            
            change_emoji = "📈" if change >= 0 else "📉"
            
            message += f"• {color_emoji} <b>{symbol}</b>: ${price:,.2f} {change_emoji} - {rec_text}\n"
    
    if assets_analyzed > 0:
        message += f"\n📋 <i>تم تحليل {assets_analyzed} أصل</i>"
        
        send_telegram_message(message)
    else:
        send_telegram_message("⚠️ <b>لا توجد بيانات للتقرير اليومي</b>")

def send_final_prices():
    """إرسال الأسعار النهائية عند توقف البوت"""
    current_time = datetime.now(DAMASCUS_TZ).strftime("%Y-%m-%d %H:%M")
    
    message = f"🛑 <b>إشعار توقف البوت - الأسعار النهائية</b>\n"
    message += f"⏰ <i>{current_time}</i>\n"
    message += "─" * 40 + "\n\n"
    
    assets_analyzed = 0
    
    for symbol in ASSETS:
        data = get_market_data(symbol)
        if all(x is not None for x in data[:3]):  # التحقق من البيانات الأساسية على الأقل
            price, rsi, change, ma_short, ma_long, support, resistance = data
            assets_analyzed += 1
            
            change_emoji = "📈" if change >= 0 else "📉"
            change_sign = "+" if change >= 0 else ""
            
            message += f"💰 <b>{symbol}</b>\n"
            message += f"   السعر: ${price:,.2f} {change_emoji} {change_sign}{change:.2f}%\n"
            message += f"   RSI: {rsi:.1f}\n"
            
            if ma_short is not None and ma_long is not None:
                message += f"   المتوسطات: {ma_short:.2f} / {ma_long:.2f}\n"
            
            if support is not None and resistance is not None:
                message += f"   الدعم/المقاومة: {support:.2f} / {resistance:.2f}\n"
            
            message += "─" * 20 + "\n"
    
    if assets_analyzed > 0:
        message += f"\n📋 <i>تم تحليل {assets_analyzed} أصل</i>"
        send_telegram_message(message)
    else:
        send_telegram_message("⚠️ <b>لا توجد بيانات متاحة لإرسال الأسعار النهائية</b>")

def check_bot_status():
    """التحقق من حالة البوت وإرسال تقرير"""
    current_time = datetime.now(DAMASCUS_TZ)
    status_message = f"""🤖 <b>تقرير حالة البوت</b>
⏰ الوقت: {current_time.strftime('%Y-%m-%d %H:%M:%S')}
📊 الحالة: يعمل بشكل طبيعي
🔗 Render: {'نعم' if ON_RENDER else 'لا'}
📡 اتصال Telegram: جاري الاختبار..."""

    # اختبار إرسال رسالة
    test_result = send_telegram_message("🔍 <b>اختبار اتصال - البوت يعمل</b>")
    
    if test_result:
        status_message += "\n✅ اتصال Telegram: نشط"
    else:
        status_message += "\n❌ اتصال Telegram: فشل في الإرسال"
    
    # إضافة معلومات إضافية
    status_message += f"\n📋 الأصول: {len(ASSETS)} عملة"
    status_message += f"\n📊 المؤشرات: RSI, المتوسطات المتحركة, الدعم/المقاومة"
    
    # الحصول على وقت الإشعار التالي
    next_job = schedule.next_run()
    if next_job:
        status_message += f"\n⏰ الإشعار القادم: {next_job.astimezone(DAMASCUS_TZ).strftime('%Y-%m-%d %H:%M')}"
    else:
        status_message += "\n⏰ الإشعار القادم: لا يوجد"
    
    send_telegram_message(status_message)

def monitor_and_recover():
    """مراقبة النظام واستعادته عند التوقف"""
    last_active_time = time.time()
    
    while True:
        try:
            current_time = time.time()
            
            # إذا مرت مدة طويلة بدون نشاط
            if current_time - last_active_time > MAX_INACTIVITY:
                error_msg = f"""⚠️ <b>تحذير: البوت غير نشط</b>
⏰ آخر نشاط: {datetime.fromtimestamp(last_active_time).strftime('%Y-%m-%d %H:%M:%S')}
🔄 جاري إعادة التشغيل التلقائي..."""
                
                send_telegram_message(error_msg)
                # إعادة تشغيل المهام
                schedule.clear()
                schedule_notifications()
                last_active_time = current_time
            
            # تحديث وقت النشاط عند كل دورة
            last_active_time = current_time
            time.sleep(300)  # التحقق كل 5 دقائق
            
        except Exception as e:
            logger.error(f"❌ خطأ في المراقبة: {e}")
            time.sleep(60)

def handle_telegram_command(command, chat_id):
    """معالجة الأوامر الواردة من Telegram"""
    command = command.lower().strip()
    
    if command == '/start' or command == '/help':
        message = """🤖 <b>أوامر البوت المتاحة:</b>
        
/help - عرض هذه المساعدة
/prices - الأسعار الحالية لجميع الأصول
/analyze - تحليل فوري للسوق
/status - حالة البوت والمعلومات
/diagnose - تشخيص مشاكل الاتصال
/btc - تحليل مفصل للبيتكوين
/eth - تحليل مفصل للإيثيريوم
/bnb - تحليل مفصل للـ BNB
/xrp - تحليل مفصل للـ XRP
/ada - تحليل مفصل للـ ADA
        
📊 <i>الإشعارات التلقائية تعمل كل 4 ساعات</i>"""
        send_telegram_message(message)
    
    elif command == '/prices':
        get_current_prices()
    
    elif command == '/analyze':
        check_trading_opportunity()
    
    elif command == '/status':
        check_bot_status()
    
    elif command == '/diagnose':
        diagnose_connection_issues()
    
    elif command == '/btc':
        analyze_specific_asset("BTC-USD")
    
    elif command == '/eth':
        analyze_specific_asset("ETH-USD")
    
    elif command == '/bnb':
        analyze_specific_asset("BNB-USD")
    
    elif command == '/xrp':
        analyze_specific_asset("XRP-USD")
    
    elif command == '/ada':
        analyze_specific_asset("ADA-USD")
    
    else:
        send_telegram_message("⚠️ <b>أمر غير معروف</b>\n\nاكتب /help لرؤية الأوامر المتاحة")

def analyze_specific_asset(symbol):
    """تحليل مفصل لأصل معين"""
    data = get_market_data(symbol)
    
    if all(x is not None for x in data[:3]):
        price, rsi, change, ma_short, ma_long, support, resistance = data
        
        # الحصول على التوصية
        rec_text, detailed_rec, rec_emoji, color_emoji = get_trading_recommendation(
            price, rsi, ma_short, ma_long, support, resistance
        )
        
        change_emoji = "📈" if change >= 0 else "📉"
        change_sign = "+" if change >= 0 else ""
        
        message = f"🔍 <b>تحليل مفصل - {symbol}</b>\n\n"
        message += f"{color_emoji} <b>السعر الحالي:</b> ${price:,.2f} {change_emoji} {change_sign}{change:.2f}%\n"
        message += f"📊 <b>RSI:</b> {rsi:.1f} "
        
        # إضافة حالة RSI
        if rsi < 30:
            message += "(تشبع بيع 🔻)"
        elif rsi < 40:
            message += "(منخفض 🟡)"
        elif rsi > 70:
            message += "(تشبع شراء 🔺)"
        elif rsi > 60:
            message += "(مرتفع 🟠)"
        else:
            message += "(طبيعي ⚪)"
        
        message += f"\n\n📈 <b>المتوسطات المتحركة:</b>\n"
        message += f"   • قصير المدى (20): ${ma_short:.2f}\n"
        message += f"   • طويل المدى (50): ${ma_long:.2f}\n"
        
        if ma_short > ma_long:
            message += f"   → <b>إيجابي</b> (القصير فوق الطويل) 🟢\n"
        else:
            message += f"   → <b>سلبي</b> (القصير تحت الطويل) 🔴\n"
        
        message += f"\n⚖️ <b>مستويات رئيسية:</b>\n"
        message += f"   • الدعم: ${support:.2f}\n"
        message += f"   • المقاومة: ${resistance:.2f}\n"
        
        # حساب المسافة للنسب المئوية
        dist_to_support = ((price - support) / price) * 100
        dist_to_resistance = ((resistance - price) / price) * 100
        
        message += f"   → {abs(dist_to_support):.1f}% من الدعم | {abs(dist_to_resistance):.1f}% من المقاومة\n"
        
        message += f"\n🎯 <b>التوصية:</b> {rec_text}\n"
        message += f"📋 <b>التفاصيل:</b> {detailed_rec}\n"
        
        # إشارة تداول واضحة
        if "شراء" in rec_text:
            message += f"\n✅ <b>إشارة تداول: BUY</b> 🟢"
        elif "بيع" in rec_text:
            message += f"\n❌ <b>إشارة تداول: SELL</b> 🔴"
        else:
            message += f"\n⚠️ <b>إشارة تداول: HOLD</b> 🟡"
        
        send_telegram_message(message)
    else:
        send_telegram_message(f"⚠️ <b>لا توجد بيانات لـ {symbol}</b>\n\nجاري محاولة أخرى...")

def setup_telegram_webhook():
    """إعداد webhook لاستقبال الأوامر"""
    webhook_url = f"https://https://mon-1.onrender.com/webhook"
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook?url={webhook_url}"
    
    try:
        response = requests.get(url)
        if response.status_code == 200:
            logger.info("✅ تم إعداد webhook بنجاح")
            return True
        else:
            logger.warning("⚠️ فشل إعداد webhook")
            return False
    except Exception as e:
        logger.error(f"❌ خطأ في إعداد webhook: {e}")
        return False

def schedule_notifications():
    """جدولة جميع الإشعارات"""
    
    # جدولة تحليل السوق كل 4 ساعات
    schedule.every(4).hours.do(check_trading_opportunity)
    
    # تقرير يومي الساعة 8 مساءً
    schedule.every().day.at("20:00").do(send_daily_report)
    
    # تقرير حالة كل 6 ساعات
    schedule.every(6).hours.do(check_bot_status)

# ========== routes Flask ==========
@app.route('/')
def home():
    return '''
    <h1>✅ Crypto Trading Bot is Running</h1>
    <p>Service: Active</p>
    <p>Type: Background Worker + Health Check</p>
    <p>Indicators: RSI, Moving Averages, Support/Resistance</p>
    '''

@app.route('/health')
def health():
    return {
        'status': 'healthy',
        'service': 'crypto-trading-bot',
        'timestamp': datetime.now(DAMASCUS_TZ).isoformat(),
        'assets': ASSETS,
        'indicators': ['RSI', 'Moving Averages', 'Support/Resistance']
    }

@app.route('/test')
def test_notification():
    """مسار لاختبار الإشعارات"""
    send_telegram_message("🔔 <b>اختبار إشعار</b>\nهذه رسالة اختبار من البوت")
    check_bot_status()
    return "تم إرسال اختبار الإشعار"

@app.route('/prices')
def get_current_prices():
    """الحصول على الأسعار الحالية"""
    message = "📊 <b>الأسعار الحالية</b>\n\n"
    for symbol in ASSETS:
        data = get_market_data(symbol)
        if all(x is not None for x in data[:3]):  # التحقق من البيانات الأساسية على الأقل
            price, rsi, change, ma_short, ma_long, support, resistance = data
            change_emoji = "📈" if change >= 0 else "📉"
            change_sign = "+" if change >= 0 else ""
            message += f"• {symbol}: ${price:,.2f} {change_emoji} {change_sign}{change:.2f}%\n"
    
    send_telegram_message(message)
    return "تم إرسال الأسعار الحالية"

@app.route('/diagnose')
def diagnose():
    """مسار لتشخيص المشاكل"""
    result = diagnose_connection_issues()
    return f"<pre>{result}</pre>"

@app.route('/analyze')
def analyze_market():
    """مسار لتحليل السوق فوراً"""
    check_trading_opportunity()
    return "تم إجراء تحليل السوق وإرسال النتائج"

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    """استقبال الأوامر من Telegram"""
    try:
        data = request.get_json()
        
        if 'message' in data and 'text' in data['message']:
            chat_id = data['message']['chat']['id']
            command = data['message']['text']
            
            # التحقق من أن الأمر من المستخدم المسموح
            if str(chat_id) == TELEGRAM_CHAT_ID:
                handle_telegram_command(command, chat_id)
            
        return 'OK'
    except Exception as e:
        logger.error(f"❌ خطأ في webhook: {e}")
        return 'Error'

# ========== معالجة الإشارات ==========
def signal_handler(sig, frame):
    """معالجة إشارات النظام للتوقف"""
    print('🛑 تم استقبال إشارة توقف...')
    send_final_prices()
    
    # إرسال رسالة توقف
    stop_time = datetime.now(DAMASCUS_TZ)
    shutdown_msg = f"""⏹️ <b>إيقاف النظام</b>
⏰ وقت الإيقاف: {stop_time.strftime('%Y-%m-%d %H:%M:%S')}
🛑 السبب: إشارة نظام"""
    send_telegram_message(shutdown_msg)
    
    exit(0)

# ========== الدالة الرئيسية ==========
def main():
    global PERSISTENT_SESSION
    
    # تسجيل إشارات التوقف
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # إنشاء جلسة HTTP متواصلة
    PERSISTENT_SESSION = create_persistent_session()
    
    # بدء البوت
    start_time = datetime.now(DAMASCUS_TZ)
    logger.info(f"🚀 بدء تشغيل البوت - {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # التحقق من متغيرات البيئة
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("❌ لم يتم تعيين TELEGRAM_BOT_TOKEN أو TELEGRAM_CHAT_ID")
        return
    
    # التحقق من اتصال Telegram
    if not verify_telegram_connection():
        logger.error("❌ فشل التحقق من توكن Telegram")
        return
    
    # التحقق من إعدادات Render
    render_env = check_render_environment()
    
    # إعداد webhook لاستقبال الأوامر
    if ON_RENDER:
        logger.info("🔧 جاري إعداد webhook للأوامر...")
        setup_telegram_webhook()
    
    # إرسال رسالة بدء التشغيل
    startup_msg = f"""🚀 <b>بدء تشغيل البوت</b>
⏰ الوقت: {start_time.strftime('%Y-%m-%d %H:%M:%S')}
🌐 البيئة: {'Render' if ON_RENDER else 'محلي'}
📊 الأصول: {len(ASSETS)} عملة رقمية
📈 المؤشرات: RSI, المتوسطات المتحركة, الدعم/المقاومة
🔔 الإشعارات: كل 4 ساعات"""
    send_telegram_message(startup_msg)
    
    # جدولة المهام
    schedule_notifications()
    
    # بدء مراقبة النظام في خلفية منفصلة
    monitor_thread = threading.Thread(target=monitor_and_recover, daemon=True)
    monitor_thread.start()
    
    # تشغيل المهام المجدولة
    while True:
        try:
            schedule.run_pending()
            time.sleep(1)
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"❌ خطأ في تشغيل المهام: {e}")
            time.sleep(60)

# ========== نقطة الدخول ==========
if __name__ == "__main__":
    # تشغيل خادم Flask في خيط منفصل إذا كنا على Render
    if ON_RENDER:
        flask_thread = threading.Thread(
            target=lambda: app.run(
                host='0.0.0.0',
                port=int(os.environ.get('PORT', 5000)),
                debug=False,
                use_reloader=False
            ),
            daemon=True
        )
        flask_thread.start()
    
    # تشغيل البوت الرئيسي
    main()
