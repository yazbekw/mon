import schedule
import time
import yfinance as yf
import numpy as np
from datetime import datetime
import requests
import pytz
import os
import logging
from flask import Flask
import threading
import signal

# ========== إعدادات الثوابت ==========
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', "7925838105:AAF5HwcXewyhrtyEi3_EF4r2p_R4Q5iMBfg")
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', "1467259305")

# تعريف الأصول التي تتابعها
ASSETS = ["BTC-USD", "ETH-USD", "BNB-USD", "XRP-USD", "ADA-USD"]

# تحديد توقيت دمشق
DAMASCUS_TZ = pytz.timezone('Asia/Damascus')

# إعدادات التوقيت
NOTIFICATION_COOLDOWN = 5  # 5 ثواني بين كل إشعارين
MAX_INACTIVITY = 3600  # 1 ساعة كحد أقصى للخمول

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

# إنشاء تطبيق Flask
app = Flask(__name__)

# ========== الدوال المساعدة ==========
def send_telegram_message(message):
    """إرسال رسالة عبر Telegram مع معالجة الأخطاء"""
    # تقليل طول الرسالة إذا كانت طويلة جداً
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
            # إضافة تأخير بين الرسائل
            time.sleep(NOTIFICATION_COOLDOWN)
            return True
        else:
            logger.error(f"❌ خطأ في إرسال الرسالة: {response.status_code}")
            logger.error(f"📋 تفاصيل الخطأ: {response.text}")
            return False
    except Exception as e:
        logger.error(f"❌ خطأ في الاتصال: {e}")
        return False

def verify_telegram_connection():
    """التحقق من اتصال وصحة توكن Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe"
    
    try:
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

def calculate_rsi(prices, period=14):
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

def get_market_data(symbol):
    """جلب بيانات السوق مع معالجة الأخطاء"""
    try:
        stock = yf.Ticker(symbol)
        hist = stock.history(period="1mo", interval="1d")
        
        if len(hist) < 15:
            logger.warning(f"⚠️ بيانات غير كافية لـ {symbol}")
            return None, None, None
            
        current_price = hist['Close'].iloc[-1]
        rsi_values = calculate_rsi(hist['Close'].values)
        current_rsi = rsi_values[-1]
        
        # سعر الأمس للتغيير
        prev_price = hist['Close'].iloc[-2] if len(hist) > 1 else current_price
        price_change = ((current_price - prev_price) / prev_price) * 100
        
        return current_price, current_rsi, price_change
        
    except Exception as e:
        logger.error(f"❌ خطأ في جلب بيانات {symbol}: {e}")
        return None, None, None

def get_rsi_recommendation(rsi, is_buy_time):
    """الحصول على توصية بناءً على RSI"""
    if is_buy_time:
        if rsi < 30:
            return "إشارة شراء قوية جداً", "🎯", "🟢"
        elif rsi < 35:
            return "إشارة شراء قوية", "👍", "🟢"
        elif rsi < 40:
            return "إشارة شراء جيدة", "📈", "🟡"
        else:
            return "تجنب الشراء (RSI مرتفع)", "⚠️", "🔴"
    else:
        if rsi > 70:
            return "إشارة بيع قوية جداً", "🎯", "🟢"
        elif rsi > 65:
            return "إشارة بيع قوية", "👍", "🟢"
        elif rsi > 60:
            return "إشارة بيع جيدة", "📈", "🟡"
        else:
            return "تجنب البيع (RSI منخفض)", "⚠️", "🔴"

def check_trading_opportunity(is_buy_time):
    """فحص فرص التداول وإرسال الإشعارات"""
    action = "شراء" if is_buy_time else "بيع"
    action_emoji = "🟢" if is_buy_time else "🔴"
    
    current_time = datetime.now(DAMASCUS_TZ).strftime("%Y-%m-%d %H:%M")
    
    message = f"{action_emoji} <b>إشعار تداول - وقت {action}</b>\n"
    message += f"⏰ <i>{current_time}</i>\n"
    message += "─" * 30 + "\n\n"
    
    assets_analyzed = 0
    
    for symbol in ASSETS:
        data = get_market_data(symbol)
        if all(x is not None for x in data):
            price, rsi, change = data
            assets_analyzed += 1
            
            rec_text, rec_emoji, color_emoji = get_rsi_recommendation(rsi, is_buy_time)
            change_emoji = "📈" if change >= 0 else "📉"
            change_sign = "+" if change >= 0 else ""
            
            message += f"{color_emoji} <b>{symbol}</b>\n"
            message += f"💰 السعر: ${price:,.2f} {change_emoji} {change_sign}{change:.2f}%\n"
            message += f"📊 RSI: {rsi:.1f} - {rec_emoji} {rec_text}\n"
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
        if all(x is not None for x in data):
            price, rsi, change = data
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

def send_final_prices():
    """إرسال الأسعار النهائية عند توقف البوت"""
    current_time = datetime.now(DAMASCUS_TZ).strftime("%Y-%m-%d %H:%M")
    
    message = f"🛑 <b>إشعار توقف البوت - الأسعار النهائية</b>\n"
    message += f"⏰ <i>{current_time}</i>\n"
    message += "─" * 40 + "\n\n"
    
    assets_analyzed = 0
    
    for symbol in ASSETS:
        data = get_market_data(symbol)
        if all(x is not None for x in data):
            price, rsi, change = data
            assets_analyzed += 1
            
            change_emoji = "📈" if change >= 0 else "📉"
            change_sign = "+" if change >= 0 else ""
            
            message += f"💰 <b>{symbol}</b>\n"
            message += f"   السعر: ${price:,.2f} {change_emoji} {change_sign}{change:.2f}%\n"
            message += f"   RSI: {rsi:.1f}\n"
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
    
    # الحصول على وقت الإشعار التالي
    next_job = schedule.next_run()
    if next_job:
        status_message += f"\n⏰下次 إشعار: {next_job.astimezone(DAMASCUS_TZ).strftime('%Y-%m-%d %H:%M')}"
    else:
        status_message += "\n⏰下次 إشعار: لا يوجد"
    
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

def schedule_notifications():
    """جدولة جميع الإشعارات"""
    
    # جدولة أوقات الشراء
    for time_slot in BUY_TIMES:
        for day in time_slot["days"]:
            getattr(schedule.every(), day).at(time_slot["start"]).do(
                lambda: check_trading_opportunity(True)
            )

    # جدولة أوقات البيع
    for time_slot in SELL_TIMES:
        for day in time_slot["days"]:
            getattr(schedule.every(), day).at(time_slot["start"]).do(
                lambda: check_trading_opportunity(False)
            )

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
    '''

@app.route('/health')
def health():
    return {
        'status': 'healthy',
        'service': 'crypto-trading-bot',
        'timestamp': datetime.now(DAMASCUS_TZ).isoformat(),
        'assets': ASSETS
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
        if all(x is not None for x in data):
            price, rsi, change = data
            change_emoji = "📈" if change >= 0 else "📉"
            change_sign = "+" if change >= 0 else ""
            message += f"• {symbol}: ${price:,.2f} {change_emoji} {change_sign}{change:.2f}%\n"
    
    send_telegram_message(message)
    return "تم إرسال الأسعار الحالية"

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

# تسجيل معالج الإشارات
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def run_web_server():
    """تشغيل خادم الويب للـ health checks"""
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"🌐 خادم الويب يعمل على port {port}")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

def main():
    """الدالة الرئيسية المحدثة مع سجلات متقدمة ومراقبة"""
    try:
        # بدء خادم الويب في خيط منفصل
        web_thread = threading.Thread(target=run_web_server, daemon=True)
        web_thread.start()
        
        # تسجيل بدء التشغيل
        start_time = datetime.now(DAMASCUS_TZ)
        logger.info("=" * 60)
        logger.info("🚀 بدء تشغيل نظام التداول المتقدم على Render")
        logger.info(f"⏰ وقت البدء: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"🌐 نوع التشغيل: {'Render' if ON_RENDER else 'Local'}")
        logger.info("=" * 60)
        
        # التحقق من صلاحية التوكن
        logger.info("🔍 التحقق من صلاحية توكن Telegram...")
        if not verify_telegram_connection():
            error_msg = """❌ <b>خطأ في توكن Telegram</b>
⚠️ البوت لا يستطيع الاتصال
🔍 الرجاء التحقق من:
1. صحة التوكن
2. صحة Chat ID
3. أن البوت ليس محظوراً"""
            send_telegram_message(error_msg)
            return
        
        # إرسال إشعار بدء التشغيل
        startup_msg = f"""🚀 <b>بدء تشغيل نظام التداول</b>
⏰ الوقت: {start_time.strftime('%Y-%m-%d %H:%M:%S')}
🌐 البيئة: {'Render' if ON_RENDER else 'محلي'}
📊 الأصول: {len(ASSETS)} عملة
✅ الحالة: تم التفعيل بنجاح"""

        send_telegram_message(startup_msg)
        
        # جدولة الإشعارات
        logger.info("📅 جاري جدولة المهام...")
        schedule_notifications()
        
        # بدء نظام المراقبة في خيط منفصل
        monitor_thread = threading.Thread(target=monitor_and_recover, daemon=True)
        monitor_thread.start()
        logger.info("✅ نظام المراقبة يعمل")
        
        # عرض المهام المجدولة
        logger.info("\n📋 المهام المجدولة:")
        for job in schedule.jobs:
            logger.info(f"   ⏰ {job.next_run.astimezone(DAMASCUS_TZ).strftime('%Y-%m-%d %H:%M')} - {job}")
        
        # إرسال تقرير الجدولة
        schedule_report = f"""📅 <b>تقرير الجدولة</b>
🛒 أوقات الشراء: {len(BUY_TIMES)} فترة
💰 أوقات البيع: {len(SELL_TIMES)} فترة
📊 التقرير اليومي: 20:00 يومياً
📡 تقرير الحالة: كل 6 ساعات
✅ تم جدولة جميع المهام"""

        send_telegram_message(schedule_report)
        
        logger.info("\n" + "=" * 60)
        logger.info("🎯 النظام يعمل بنجاح! الإشعارات مجدولة")
        logger.info("⏰ سيتم إرسال الإشعارات تلقائياً حسب الأوقات المحددة")
        logger.info("📊 لمراقبة السجلات: لوحة تحكم Render → Logs")
        logger.info("=" * 60 + "\n")
        
        # إرسال رسالة تأكيد التشغيل
        send_telegram_message("✅ <b>النظام يعمل بشكل طبيعي وجاهز للإشعارات</b>")
        
        # عداد لمراقبة أداء النظام
        system_uptime = time.time()
        error_count = 0
        successful_cycles = 0
        
        # الحلقة الرئيسية مع مراقبة متقدمة
        while True:
            try:
                current_time = datetime.now(DAMASCUS_TZ)
                
                # تشغيل المهام المجدولة
                schedule.run_pending()
                
                # إرسال نبضة حياة كل ساعة (للمراقبة فقط)
                if current_time.minute == 0 and current_time.second == 0:
                    uptime_minutes = (time.time() - system_uptime) / 60
                    status_msg = f"""❤️ <b>نبضة حياة - النظام يعمل</b>
⏰ الوقت: {current_time.strftime('%H:%M:%S')}
🔄 وقت التشغيل: {uptime_minutes:.1f} دقيقة
✅ الدورات الناجحة: {successful_cycles}
❌ الأخطاء: {error_count}
📊 الحالة: ممتازة"""

                    if ON_RENDER:  # إرسال النبضات فقط على Render
                        send_telegram_message(status_msg)
                
                successful_cycles += 1
                
                # تقليل استهلاك الموارد على Render
                time.sleep(30)  # انتظار 30 ثانية بين الدورات
                
            except KeyboardInterrupt:
                # إيقاف يدوي
                stop_time = datetime.now(DAMASCUS_TZ)
                runtime = (stop_time - start_time)
                
                # إرسال الأسعار النهائية قبل التوقف
                send_final_prices()
                
                shutdown_msg = f"""⏹️ <b>إيقاف النظام يدوياً</b>
⏰ وقت البدء: {start_time.strftime('%Y-%m-%d %H:%M')}
⏰ وقت الإيقاف: {stop_time.strftime('%Y-%m-%d %H:%M')}
⏱️ مدة التشغيل: {runtime}
✅ الدورات الناجحة: {successful_cycles}
❌ الأخطاء: {error_count}"""

                send_telegram_message(shutdown_msg)
                logger.info("\n⏹️ تم إيقاف النظام يدوياً")
                break
                
            except Exception as e:
                error_count += 1
                error_time = datetime.now(DAMASCUS_TZ)
                
                logger.error(f"❌ خطأ في الدورة الرئيسية: {e}")
                logger.error(f"⏰ وقت الخطأ: {error_time.strftime('%Y-%m-%d %H:%M:%S')}")
                
                # إرسال إشعار خطأ فقط إذا كانت الأخطاء متتالية
                if error_count % 5 == 0:
                    error_msg = f"""⚠️ <b>تحذير: أخطاء متعددة</b>
⏰ الوقت: {error_time.strftime('%H:%M:%S')}
❌ عدد الأخطاء: {error_count}
📋 آخر خطأ: {str(e)[:100]}...
🔄 النظام يستمر في المحاولة"""

                    send_telegram_message(error_msg)
                
                # انتظار أطول بين المحاولات عند الأخطاء
                time.sleep(60)
                
    except Exception as e:
        # خطأ فادح في بدء التشغيل
        crash_time = datetime.now(DAMASCUS_TZ)
        crash_msg = f"""💥 <b>خطأ فادح في النظام</b>
⏰ الوقت: {crash_time.strftime('%Y-%m-%d %H:%M:%S')}
📋 الخطأ: {str(e)}
❌ النظام توقف"""

        send_telegram_message(crash_msg)
        logger.error(f"💥 خطأ فادح: {e}")
        
        if ON_RENDER:
            # على Render، نعيد المحاولة بعد 5 دقائق
            logger.info("🔄 إعادة المحاولة بعد 5 دقائق...")
            time.sleep(300)
            main()

if __name__ == "__main__":
    main()

