
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

TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', "8134471132:AAEdQo6TaKSEhB7BBmZ-Kl4K7IYookjNe0s")
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', "1467259305")


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

# إعداد logging للتحقق من عمل البرنامج
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# التحقق من أننا على Render
ON_RENDER = os.environ.get('RENDER', False)


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
            print("✅ تم إرسال الإشعار إلى Telegram")
            return True
        else:
            print(f"❌ خطأ في إرسال الرسالة: {response.status_code}")
            print(f"📋 تفاصيل الخطأ: {response.text}")
            return False
    except Exception as e:
        print(f"❌ خطأ في الاتصال: {e}")
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
            print(f"⚠️ بيانات غير كافية لـ {symbol}")
            return None, None, None
            
        current_price = hist['Close'].iloc[-1]
        rsi_values = calculate_rsi(hist['Close'].values)
        current_rsi = rsi_values[-1]
        
        # سعر الأمس للتغيير
        prev_price = hist['Close'].iloc[-2] if len(hist) > 1 else current_price
        price_change = ((current_price - prev_price) / prev_price) * 100
        
        return current_price, current_rsi, price_change
        
    except Exception as e:
        print(f"❌ خطأ في جلب بيانات {symbol}: {e}")
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
        print("⚠️ لا توجد بيانات متاحة للتحليل")
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

# إنشاء تطبيق Flask
app = Flask(__name__)

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

def run_web_server():
    """تشغيل خادم الويب للـ health checks"""
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

def main():
    """الدالة الرئيسية المحدثة مع سجلات متقدمة ومراقبة"""
    try:
        # تسجيل بدء التشغيل
        web_thread = threading.Thread(target=run_web_server, daemon=True)
        web_thread.start()
        print("🌐 خادم الويب يعمل على port 5000")
        start_time = datetime.now(DAMASCUS_TZ)
        print("=" * 60)
        print("🚀 بدء تشغيل نظام التداول المتقدم على Render")
        print(f"⏰ وقت البدء: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"🌐 نوع التشغيل: {'Render' if ON_RENDER else 'Local'}")
        print("=" * 60)
        
        # إرسال إشعار بدء التشغيل
        startup_msg = f"""🚀 <b>بدء تشغيل نظام التداول</b>
⏰ الوقت: {start_time.strftime('%Y-%m-%d %H:%M:%S')}
🌐 البيئة: {'Render' if ON_RENDER else 'محلي'}
📊 الأصول: {len(ASSETS)} عملة
✅ الحالة: تم التفعيل بنجاح"""

        send_telegram_message(startup_msg)
        
        # اختبار اتصال Telegram
        print("📡 اختبار اتصال Telegram...")
        test_msg = send_telegram_message("🔍 <b>اختبار اتصال - النظام يعمل</b>")
        
        if test_msg:
            print("✅ اتصال Telegram ناجح!")
        else:
            print("⚠️ تحذير: هناك مشكلة في اتصال Telegram")
            if ON_RENDER:
                print("ℹ️ المتابعة رغم المشكلة للتشغيل المستمر")
        
        # جدولة الإشعارات
        print("📅 جاري جدولة المهام...")
        schedule_notifications()
        
        # عرض المهام المجدولة
        print("\n📋 المهام المجدولة:")
        for job in schedule.jobs:
            print(f"   ⏰ {job.next_run.strftime('%Y-%m-%d %H:%M')} - {job}")
        
        # إرسال تقرير الجدولة
        schedule_report = f"""📅 <b>تقرير الجدولة</b>
🛒 أوقات الشراء: {len(BUY_TIMES)} فترة
💰 أوقات البيع: {len(SELL_TIMES)} فترة
📊 التقرير اليومي: 20:00 يومياً
✅ تم جدولة جميع المهام"""

        send_telegram_message(schedule_report)
        
        print("\n" + "=" * 60)
        print("🎯 النظام يعمل بنجاح! الإشعارات مجدولة")
        print("⏰ سيتم إرسال الإشعارات تلقائياً حسب الأوقات المحددة")
        print("📊 لمراقبة السجلات: لوحة تحكم Render → Logs")
        print("=" * 60 + "\n")
        
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
                
                shutdown_msg = f"""⏹️ <b>إيقاف النظام يدوياً</b>
⏰ وقت البدء: {start_time.strftime('%Y-%m-%d %H:%M')}
⏰ وقت الإيقاف: {stop_time.strftime('%Y-%m-%d %H:%M')}
⏱️ مدة التشغيل: {runtime}
✅ الدورات الناجحة: {successful_cycles}
❌ الأخطاء: {error_count}"""

                send_telegram_message(shutdown_msg)
                print("\n⏹️ تم إيقاف النظام يدوياً")
                break
                
            except Exception as e:
                error_count += 1
                error_time = datetime.now(DAMASCUS_TZ)
                
                print(f"❌ خطأ في الدورة الرئيسية: {e}")
                print(f"⏰ وقت الخطأ: {error_time.strftime('%Y-%m-%d %H:%M:%S')}")
                
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
        print(f"💥 خطأ فادح: {e}")
        
        if ON_RENDER:
            # على Render، نعيد المحاولة بعد 5 دقائق
            print("🔄 إعادة المحاولة بعد 5 دقائق...")
            time.sleep(300)
            main()

if __name__ == "__main__":

    main()



