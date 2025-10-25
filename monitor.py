import os
import time
import logging
import threading
import requests
from datetime import datetime, timedelta
from binance.client import Client
from binance.exceptions import BinanceAPIException
import pandas as pd
import numpy as np
import telegram
from telegram.error import TelegramError
from flask import Flask, request, jsonify

# إعداد التسجيل
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)

# الإعدادات القابلة للتخصيص
# لجعل الكود جاهزًا للتشغيل، أضف قيمك الخاصة هنا أو استخدم متغيرات البيئة
API_KEY = os.getenv('BINANCE_API_KEY', 'your_binance_api_key_here')  # أضف API Key الخاص بك
API_SECRET = os.getenv('BINANCE_API_SECRET', 'your_binance_api_secret_here')  # أضف API Secret الخاص بك
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', 'your_telegram_bot_token_here')  # توكن Telegram bot
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', 'your_chat_id_here')  # Chat ID للإشعارات

SYMBOLS = ['BNBUSDT', 'ETHUSDT']  # العملات المدعومة
LEVERAGE = 50  # الرافعة المالية
BASE_POSITION_SIZE = 3  # حجم الصفقة الأساسي (غير مستخدم حاليًا لأن البوت يدير الصفقات الموجودة)
MAX_CONCURRENT_TRADES = 5  # عدد الصفقات المتزامنة القصوى
TRADE_DURATION_SECONDS = 3600  # مدة الصفقة: 1 ساعة (3600 ثانية)

MIN_STOP_LOSS_PCT = 1.5  # الحد الأدنى لوقف الخسارة (%)
MAX_STOP_LOSS_PCT = 5.0  # الحد الأقصى لوقف الخسارة (%)
VOLATILITY_MULTIPLIER = 1.5  # مضاعف التقلب

PARTIAL_STOP_LOSS_PCT = 40  # نسبة المسافة لوقف الخسارة الجزئي (%)
PARTIAL_CLOSE_PCT = 30  # نسبة الإغلاق الجزئي (% من الصفقة)

TAKE_PROFIT_LEVELS = [
    (0.25, 50),  # المستوى 1: 0.25% ربح - 50% من الصفقة
    (0.30, 30),  # المستوى 2: 0.30% ربح - 30% من الصفقة
    (0.35, 20)   # المستوى 3: 0.35% ربح - 20% من الصفقة
]

MARGIN_WARNING_THRESHOLD = 70  # عتبة تحذير الهامش (%)
CANDLE_TIMEFRAME = '15m'  # الإطار الزمني للشموع
NUM_CANDLES = 20  # عدد الشموع للدعم/المقاومة

TIMEZONE = 'Asia/Damascus'  # المنطقة الزمنية

# تهيئة عميل Binance
try:
    client = Client(API_KEY, API_SECRET)
    for symbol in SYMBOLS:
        client.futures_change_leverage(symbol=symbol, leverage=LEVERAGE)
    logging.info("تم تهيئة عميل Binance بنجاح.")
except Exception as e:
    logging.error(f"خطأ في تهيئة عميل Binance: {e}")
    exit(1)

# تهيئة Telegram bot
try:
    bot = telegram.Bot(token=TELEGRAM_TOKEN)
    logging.info("تم تهيئة Telegram bot بنجاح.")
except Exception as e:
    logging.error(f"خطأ في تهيئة Telegram bot: {e}")
    exit(1)

# تخزين الصفقات المدارة
managed_trades = {}  # {symbol: {'entry_price': float, 'quantity': float, 'side': str, 'stop_loss_partial': float, 'stop_loss_full': float, 'take_profits': list, 'entry_time': datetime}}

# وظيفة لإرسال إشعار عبر Telegram
def send_telegram_message(message):
    try:
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
        logging.info(f"إشعار مرسل: {message}")
    except TelegramError as e:
        logging.error(f"خطأ في إرسال الإشعار: {e}")

# وظيفة للحصول على بيانات الشموع
def get_candles(symbol, timeframe, limit):
    try:
        candles = client.futures_klines(symbol=symbol, interval=timeframe, limit=limit)
        df = pd.DataFrame(candles, columns=['open_time', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'])
        df['open'] = pd.to_numeric(df['open'])
        df['high'] = pd.to_numeric(df['high'])
        df['low'] = pd.to_numeric(df['low'])
        df['close'] = pd.to_numeric(df['close'])
        return df
    except BinanceAPIException as e:
        logging.error(f"خطأ في الحصول على الشموع لـ {symbol}: {e}")
        return None

# حساب ATR
def calculate_atr(df):
    df['high_low'] = df['high'] - df['low']
    df['high_close'] = abs(df['high'] - df['close'].shift())
    df['low_close'] = abs(df['low'] - df['close'].shift())
    df['tr'] = df[['high_low', 'high_close', 'low_close']].max(axis=1)
    atr = df['tr'].rolling(window=14).mean().iloc[-1]
    return atr

# حساب مستويات الدعم/المقاومة
def calculate_support_resistance(df):
    support = df['low'].min()
    resistance = df['high'].max()
    return support, resistance

# حساب وقف الخسارة الكامل
def calculate_stop_loss(entry_price, side, atr, support, resistance):
    if side == 'LONG':
        stop_loss = max(entry_price * (1 - MAX_STOP_LOSS_PCT / 100), min(entry_price * (1 - MIN_STOP_LOSS_PCT / 100), support - atr * VOLATILITY_MULTIPLIER))
    else:  # SHORT
        stop_loss = min(entry_price * (1 + MAX_STOP_LOSS_PCT / 100), max(entry_price * (1 + MIN_STOP_LOSS_PCT / 100), resistance + atr * VOLATILITY_MULTIPLIER))
    return stop_loss

# إغلاق جزئي أو كامل للصفقة
def close_position(symbol, quantity_pct=100, reduce_only=True):
    try:
        position = client.futures_position_information(symbol=symbol)[0]
        qty = float(position['positionAmt'])
        if qty == 0:
            logging.info(f"لا توجد مراكز مفتوحة لـ {symbol}")
            return
        side = 'SELL' if qty > 0 else 'BUY'
        close_qty = abs(qty) * (quantity_pct / 100)
        close_qty = round(close_qty, 3)  # تقريب الكمية لتجنب الأخطاء
        order = client.futures_create_order(
            symbol=symbol,
            side=side,
            type='MARKET',
            quantity=close_qty,
            reduceOnly=reduce_only
        )
        logging.info(f"إغلاق {quantity_pct}% من الصفقة {symbol}: {order}")
        send_telegram_message(f"إغلاق {quantity_pct}% من الصفقة {symbol}")
    except BinanceAPIException as e:
        logging.error(f"خطأ في إغلاق الصفقة {symbol}: {e}")

# كشف الصفقات الجديدة
def detect_new_trades():
    try:
        positions = client.futures_position_information()
        current_trades = {p['symbol']: p for p in positions if float(p['positionAmt']) != 0 and p['symbol'] in SYMBOLS}
        
        if len(current_trades) + len(managed_trades) > MAX_CONCURRENT_TRADES:
            logging.warning(f"تجاوز الحد الأقصى للصفقات المتزامنة: {len(current_trades)}")
            send_telegram_message(f"تحذير: تجاوز الحد الأقصى للصفقات المتزامنة! ({len(current_trades)} > {MAX_CONCURRENT_TRADES})")
            return
        
        for symbol, pos in current_trades.items():
            if symbol not in managed_trades:
                entry_price = float(pos['entryPrice'])
                quantity = float(pos['positionAmt'])
                side = 'LONG' if quantity > 0 else 'SHORT'
                
                df = get_candles(symbol, CANDLE_TIMEFRAME, NUM_CANDLES + 14)  # إضافة شموع إضافية لـ ATR
                if df is None:
                    continue
                
                atr = calculate_atr(df)
                support, resistance = calculate_support_resistance(df)
                stop_loss_full = calculate_stop_loss(entry_price, side, atr, support, resistance)
                
                # حساب وقف الخسارة الجزئي
                distance = stop_loss_full - entry_price if side == 'SHORT' else entry_price - stop_loss_full
                partial_distance = distance * (PARTIAL_STOP_LOSS_PCT / 100)
                stop_loss_partial = entry_price + partial_distance if side == 'SHORT' else entry_price - partial_distance
                
                # حساب مستويات جني الأرباح
                take_profits = []
                for pct, qty_pct in TAKE_PROFIT_LEVELS:
                    tp_price = entry_price * (1 + pct/100) if side == 'LONG' else entry_price * (1 - pct/100)
                    take_profits.append((tp_price, qty_pct))
                
                managed_trades[symbol] = {
                    'entry_price': entry_price,
                    'quantity': abs(quantity),
                    'side': side,
                    'stop_loss_partial': stop_loss_partial,
                    'stop_loss_full': stop_loss_full,
                    'take_profits': take_profits,
                    'entry_time': datetime.now()
                }
                
                logging.info(f"بدء إدارة صفقة جديدة: {symbol} - {side} عند {entry_price}")
                send_telegram_message(f"بدء إدارة صفقة جديدة: {symbol} - {side} عند {entry_price}\nوقف خسارة جزئي: {stop_loss_partial}\nوقف خسارة كامل: {stop_loss_full}")
    except BinanceAPIException as e:
        logging.error(f"خطأ في كشف الصفقات: {e}")

# مراقبة المستويات (وقف خسارة، جني أرباح، مدة الصفقة)
def monitor_levels():
    for symbol, trade in list(managed_trades.items()):
        try:
            ticker = client.futures_ticker(symbol=symbol)
            price = float(ticker['lastPrice'])
            side = trade['side']
            is_long = side == 'LONG'
            
            # التحقق من مدة الصفقة
            if (datetime.now() - trade['entry_time']).total_seconds() > TRADE_DURATION_SECONDS:
                close_position(symbol, 100)
                send_telegram_message(f"إغلاق صفقة {symbol} بسبب انتهاء المدة (أكثر من ساعة)")
                del managed_trades[symbol]
                continue
            
            # وقف الخسارة الجزئي
            if (is_long and price <= trade['stop_loss_partial']) or (not is_long and price >= trade['stop_loss_partial']):
                close_position(symbol, PARTIAL_CLOSE_PCT)
                trade['quantity'] *= (1 - PARTIAL_CLOSE_PCT / 100)
                send_telegram_message(f"وقف خسارة جزئي لـ {symbol} عند {price}")
            
            # وقف الخسارة الكامل
            if (is_long and price <= trade['stop_loss_full']) or (not is_long and price >= trade['stop_loss_full']):
                close_position(symbol, 100)
                send_telegram_message(f"وقف خسارة كامل لـ {symbol} عند {price}")
                del managed_trades[symbol]
                continue
            
            # جني الأرباح
            remaining_tp = []
            for tp_price, tp_pct in trade['take_profits']:
                if (is_long and price >= tp_price) or (not is_long and price <= tp_price):
                    close_position(symbol, tp_pct)
                    trade['quantity'] *= (1 - tp_pct / 100)
                    send_telegram_message(f"جني أرباح ({tp_pct}%) لـ {symbol} عند {price}")
                else:
                    remaining_tp.append((tp_price, tp_pct))
            trade['take_profits'] = remaining_tp
            
            if trade['quantity'] <= 0:
                del managed_trades[symbol]
        except BinanceAPIException as e:
            logging.error(f"خطأ في مراقبة المستويات لـ {symbol}: {e}")
        except Exception as e:
            logging.error(f"خطأ عام في مراقبة {symbol}: {e}")

# مراقبة الهامش
def monitor_margin():
    try:
        account = client.futures_account()
        total_maint_margin = float(account['totalMaintMargin'])
        total_margin_balance = float(account['totalMarginBalance'])
        if total_margin_balance == 0:
            return
        margin_ratio = (total_maint_margin / total_margin_balance) * 100
        if margin_ratio > MARGIN_WARNING_THRESHOLD:
            logging.warning(f"تحذير: نسبة الهامش {margin_ratio:.2f}% > {MARGIN_WARNING_THRESHOLD}%")
            send_telegram_message(f"تحذير: نسبة الهامش عالية {margin_ratio:.2f}%")
            if margin_ratio > 90:  # عتبة خطر عالي
                if managed_trades:
                    oldest_symbol = min(managed_trades, key=lambda k: managed_trades[k]['entry_time'])
                    close_position(oldest_symbol, 100)
                    del managed_trades[oldest_symbol]
                    send_telegram_message(f"إغلاق أقدم صفقة {oldest_symbol} لتقليل المخاطرة")
    except BinanceAPIException as e:
        logging.error(f"خطأ في مراقبة الهامش: {e}")

# تقارير الأداء (كل 6 ساعات)
def performance_report():
    try:
        # يمكن توسيع هذا للحصول على إحصائيات أكثر تفصيلاً من Binance
        account = client.futures_account()
        total_pnl = float(account['totalUnrealizedProfit'])
        total_trades = len(managed_trades)
        message = f"تقرير أداء:\nإجمالي الصفقات المدارة: {total_trades}\nPnL غير محقق: {total_pnl:.2f} USDT"
        send_telegram_message(message)
        logging.info(message)
    except Exception as e:
        logging.error(f"خطأ في إنشاء تقرير الأداء: {e}")

# جدولة المهام باستخدام خيوط
def scheduler_detect_trades():
    while True:
        detect_new_trades()
        time.sleep(30)  # كل 30 ثانية

def scheduler_monitor_levels():
    while True:
        monitor_levels()
        time.sleep(10)  # كل 10 ثواني

def scheduler_monitor_margin():
    while True:
        monitor_margin()
        time.sleep(60)  # كل دقيقة

def scheduler_performance_report():
    while True:
        performance_report()
        time.sleep(6 * 3600)  # كل 6 ساعات

# إعداد Flask لـ REST API
app = Flask(__name__)

@app.route('/', methods=['GET'])
def health_check():
    return jsonify({"status": "running"}), 200

@app.route('/api/management/status', methods=['GET'])
def get_status():
    return jsonify(managed_trades), 200

@app.route('/api/management/sync', methods=['POST'])
def manual_sync():
    detect_new_trades()
    return jsonify({"message": "مزامنة يدوية تمت"}), 200

@app.route('/api/management/close/<symbol>', methods=['POST'])
def close_trade(symbol):
    if symbol in managed_trades:
        close_position(symbol, 100)
        del managed_trades[symbol]
        return jsonify({"message": f"تم إغلاق {symbol}"}), 200
    return jsonify({"error": "الصفقة غير موجودة"}), 404

@app.route('/api/debug/positions', methods=['GET'])
def debug_positions():
    positions = client.futures_position_information()
    return jsonify(positions), 200

@app.route('/api/debug/telegram-test', methods=['GET'])
def test_telegram():
    send_telegram_message("اختبار إشعار من API")
    return jsonify({"message": "إشعار اختباري مرسل"}), 200

# للأمان، أضف مصادقة بسيطة (مثال: API Key في الرأس)
@app.before_request
def check_auth():
    if request.path.startswith('/api/'):
        api_key = request.headers.get('X-API-KEY')
        if api_key != 'your_secret_api_key':  # أضف مفتاحك السري هنا
            return jsonify({"error": "غير مصرح"}), 401

if __name__ == '__main__':
    # مزامنة فورية عند البدء
    send_telegram_message("البوت قد بدأ التشغيل!")
    detect_new_trades()
    
    # بدء الخيوط للجدولة
    threading.Thread(target=scheduler_detect_trades, daemon=True).start()
    threading.Thread(target=scheduler_monitor_levels, daemon=True).start()
    threading.Thread(target=scheduler_monitor_margin, daemon=True).start()
    threading.Thread(target=scheduler_performance_report, daemon=True).start()
    
    # تشغيل Flask API على المنفذ 5000
    app.run(host='0.0.0.0', port=5000, debug=False)
