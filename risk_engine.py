import logging
import math
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from binance_engine import BinanceEngine

logger = logging.getLogger(__name__)

class RiskEngine:
    """
    🛡️ محرك المخاطرة - العقل التحليلي لنظام إدارة الصفقات
    """

    def __init__(self, config: dict, binance_engine: BinanceEngine):
        self.config = config
        self.binance = binance_engine
        self.risk_settings = self._initialize_risk_settings()
        
        logger.info("✅ تم تهيئة Risk Engine")

    def _initialize_risk_settings(self) -> Dict:
        """تهيئة إعدادات المخاطرة"""
        return {
            # إعدادات وقف الخسارة المزدوج
            'partial_stop_percent': self.config.get('partial_stop_percent', 0.3),  # 30%
            'partial_trigger_percent': self.config.get('partial_trigger_percent', 0.4),  # 40% من المسافة
            'min_stop_loss': self.config.get('min_stop_loss', 0.015),  # 1.5% حد أدنى
            'max_stop_loss': self.config.get('max_stop_loss', 0.05),   # 5% حد أقصى
            'volatility_multiplier': self.config.get('volatility_multiplier', 1.5),  # مضاعف التقلب
            
            # إعدادات جني الأرباح متعدد المستويات
            'take_profit_levels': self.config.get('take_profit_levels', [
                {'profit': 0.0025, 'close': 0.5},  # 0.25% ربح - 50% من الصفقة
                {'profit': 0.0030, 'close': 0.3},  # 0.30% ربح - 30% من الصفقة  
                {'profit': 0.0035, 'close': 0.2}   # 0.35% ربح - 20% من الصفقة
            ]),
            
            # إعدادات عامة
            'margin_risk_threshold': self.config.get('margin_risk_threshold', 70),  # 70%
            'max_concurrent_trades': self.config.get('max_concurrent_trades', 1),
        }

    async def calculate_actions(self, position: Dict) -> List[Dict]:
        """
        حساب جميع الإجراءات المطلوبة للصفقة بناءً على حالتها الحالية
        """
        actions = []
        
        try:
            # 1. تحديث بيانات الصفقة بالمستويات الفنية
            await self._update_position_with_technical_levels(position)
            
            # 2. فحص وقف الخسارة (الأولوية القصوى)
            stop_loss_actions = await self._check_stop_loss(position)
            actions.extend(stop_loss_actions)
            
            # إذا كان هناك وقف خسارة كامل، نتوقف عن فحص باقي الإجراءات
            if any(action['type'] == 'FULL_STOP_LOSS' for action in actions):
                return actions
            
            # 3. فحص جني الأرباح
            take_profit_actions = await self._check_take_profit(position)
            actions.extend(take_profit_actions)
            
            # 4. فحص مستويات الوقف المتحرك (Trailing Stop)
            trailing_actions = await self._check_trailing_stop(position)
            actions.extend(trailing_actions)
            
            logger.debug(f"🔍 تم حساب {len(actions)} إجراء للرمز {position['symbol']}")
            return actions
            
        except Exception as e:
            logger.error(f"❌ خطأ في حساب الإجراءات لـ {position['symbol']}: {e}")
            return []

    async def _update_position_with_technical_levels(self, position: Dict):
        """تحديث الصفقة بالمستويات الفنية الحالية"""
        try:
            symbol = position['symbol']
            
            # جلب المستويات الفنية من Binance Engine
            tech_levels = await self.binance.calculate_technical_levels(symbol)
            
            # حساب مستويات وقف الخسارة
            stop_levels = await self._calculate_stop_loss_levels(position, tech_levels)
            
            # تحديث الصفقة بالمستويات
            position.update({
                'technical_levels': tech_levels,
                'stop_loss_levels': stop_levels,
                'atr': tech_levels['atr'],
                'support': tech_levels['support'],
                'resistance': tech_levels['resistance'],
                'last_technical_update': datetime.now()
            })
            
        except Exception as e:
            logger.error(f"❌ خطأ في تحديث المستويات الفنية لـ {position['symbol']}: {e}")

    async def _calculate_stop_loss_levels(self, position: Dict, tech_levels: Dict) -> Dict:
        """
        حساب مستويات وقف الخسارة المزدوج الديناميكي
        """
        try:
            entry_price = position['entry_price']
            current_price = position['current_price']
            side = position['side']
            atr = tech_levels['atr']
            
            # 1. حساب وقف الخسارة الكامل الأساسي
            base_full_stop = await self._calculate_base_stop_loss(
                entry_price, side, atr, tech_levels
            )
            
            # 2. تطبيق الحدود الدنيا والعليا
            adjusted_full_stop = self._apply_stop_loss_limits(
                entry_price, base_full_stop, side
            )
            
            # 3. حساب وقف الخسارة الجزئي (40% من مسافة الوقف الكامل)
            partial_stop_price = self._calculate_partial_stop_price(
                entry_price, adjusted_full_stop, side
            )
            
            stop_levels = {
                'full_stop': adjusted_full_stop,
                'partial_stop': partial_stop_price,
                'base_full_stop': base_full_stop,
                'atr_value': atr,
                'calculated_at': datetime.now()
            }
            
            logger.debug(f"🛡️ مستويات وقف الخسارة لـ {position['symbol']}: جزئي={partial_stop_price:.4f}, كامل={adjusted_full_stop:.4f}")
            return stop_levels
            
        except Exception as e:
            logger.error(f"❌ خطأ في حساب وقف الخسارة لـ {position['symbol']}: {e}")
            # قيم افتراضية في حالة الخطأ
            return self._get_default_stop_levels(entry_price, side)

    async def _calculate_base_stop_loss(self, entry_price: float, side: str, atr: float, tech_levels: Dict) -> float:
        """حساب وقف الخسارة الأساسي بناءً على ATR والدعم/المقاومة"""
        try:
            support = tech_levels['support']
            resistance = tech_levels['resistance']
            current_price = tech_levels['current_price']
            
            if side == 'LONG':
                # للصفقات الطويلة: استخدام الدعم و ATR
                stop_based_on_support = support * (1 - 0.001)  # هامش صغير تحت الدعم
                stop_based_on_atr = current_price - (atr * self.risk_settings['volatility_multiplier'])
                
                # أخذ أكثر المستويات تحفظاً (الأعلى لل long)
                base_stop = max(stop_based_on_support, stop_based_on_atr)
                
            else:  # SHORT
                # للصفقات القصيرة: استخدام المقاومة و ATR
                stop_based_on_resistance = resistance * (1 + 0.001)  # هامش صغير فوق المقاومة
                stop_based_on_atr = current_price + (atr * self.risk_settings['volatility_multiplier'])
                
                # أخذ أكثر المستويات تحفظاً (الأدنى لل short)
                base_stop = min(stop_based_on_resistance, stop_based_on_atr)
            
            return base_stop
            
        except Exception as e:
            logger.error(f"❌ خطأ في حساب وقف الخسارة الأساسي: {e}")
            # وقف افتراضي 2% في حالة الخطأ
            return entry_price * (0.98 if side == 'LONG' else 1.02)

    def _apply_stop_loss_limits(self, entry_price: float, base_stop: float, side: str) -> float:
        """تطبيق الحدود الدنيا والعليا على وقف الخسارة"""
        min_stop_distance = entry_price * self.risk_settings['min_stop_loss']
        max_stop_distance = entry_price * self.risk_settings['max_stop_loss']
        
        if side == 'LONG':
            # لل long: السعر يجب أن يكون تحت سعر الدخول
            min_stop_price = entry_price - max_stop_distance  # الحد الأدنى للمسافة = الحد الأقصى للخسارة
            max_stop_price = entry_price - min_stop_distance  # الحد الأقصى للمسافة = الحد الأدنى للخسارة
            
            # التأكد من أن الوقف بين الحدود
            adjusted_stop = max(base_stop, min_stop_price)  # لا نريد خسارة أكثر من الحد الأقصى
            adjusted_stop = min(adjusted_stop, max_stop_price)  # ولا أقل من الحد الأدنى
            
        else:  # SHORT
            # لل short: السعر يجب أن يكون فوق سعر الدخول
            min_stop_price = entry_price + max_stop_distance  # الحد الأدنى للمسافة = الحد الأقصى للخسارة
            max_stop_price = entry_price + min_stop_distance  # الحد الأقصى للمسافة = الحد الأدنى للخسارة
            
            # التأكد من أن الوقف بين الحدود
            adjusted_stop = min(base_stop, min_stop_price)  # لا نريد خسارة أكثر من الحد الأقصى
            adjusted_stop = max(adjusted_stop, max_stop_price)  # ولا أقل من الحد الأدنى
        
        return adjusted_stop

    def _calculate_partial_stop_price(self, entry_price: float, full_stop: float, side: str) -> float:
        """حساب سعر وقف الخسارة الجزئي"""
        if side == 'LONG':
            # المسافة الكاملة بين الدخول والوقف الكامل
            full_distance = entry_price - full_stop
            # وقف جزئي عند 40% من المسافة
            partial_distance = full_distance * self.risk_settings['partial_trigger_percent']
            partial_stop = entry_price - partial_distance
            
        else:  # SHORT
            # المسافة الكاملة بين الدخول والوقف الكامل
            full_distance = full_stop - entry_price
            # وقف جزئي عند 40% من المسافة
            partial_distance = full_distance * self.risk_settings['partial_trigger_percent']
            partial_stop = entry_price + partial_distance
        
        return partial_stop

    def _get_default_stop_levels(self, entry_price: float, side: str) -> Dict:
        """الحصول على مستويات وقف افتراضية في حالة الخطأ"""
        if side == 'LONG':
            full_stop = entry_price * 0.98  # 2% وقف افتراضي
            partial_stop = entry_price * 0.992  # 0.8% للوقف الجزئي
        else:
            full_stop = entry_price * 1.02  # 2% وقف افتراضي
            partial_stop = entry_price * 1.008  # 0.8% للوقف الجزئي
            
        return {
            'full_stop': full_stop,
            'partial_stop': partial_stop,
            'base_full_stop': full_stop,
            'atr_value': 0.01,
            'calculated_at': datetime.now()
        }

    async def _check_stop_loss(self, position: Dict) -> List[Dict]:
        """فحص مستويات وقف الخسارة"""
        actions = []
        
        try:
            current_price = position['current_price']
            stop_levels = position.get('stop_loss_levels', {})
            side = position['side']
            
            if not stop_levels:
                logger.warning(f"⚠️ لا توجد مستويات وقف خسارة لـ {position['symbol']}")
                return actions
            
            full_stop = stop_levels['full_stop']
            partial_stop = stop_levels['partial_stop']
            
            # فحص وقف الخسارة الجزئي
            if not position.get('partial_stop_hit', False):
                if self._should_trigger_stop(current_price, partial_stop, side):
                    actions.append({
                        'type': 'PARTIAL_STOP_LOSS',
                        'quantity': position['quantity'] * self.risk_settings['partial_stop_percent'],
                        'price': current_price,
                        'stop_price': partial_stop,
                        'reason': 'وقف خسارة جزئي - 40% من المسافة',
                        'timestamp': datetime.now()
                    })
                    position['partial_stop_hit'] = True
                    logger.info(f"🛡️触发 وقف خسارة جزئي لـ {position['symbol']} عند السعر {current_price}")
            
            # فحص وقف الخسارة الكامل
            if self._should_trigger_stop(current_price, full_stop, side):
                remaining_quantity = position['quantity']
                if position.get('partial_stop_hit', False):
                    # إذا كان الوقف الجزئي قد تم، نغلق الكمية المتبقية
                    remaining_quantity = position['quantity'] * (1 - self.risk_settings['partial_stop_percent'])
                
                actions.append({
                    'type': 'FULL_STOP_LOSS',
                    'quantity': remaining_quantity,
                    'price': current_price,
                    'stop_price': full_stop,
                    'reason': 'وقف خسارة كامل - الوصول للمستوى النهائي',
                    'timestamp': datetime.now()
                })
                logger.info(f"🔴触发 وقف خسارة كامل لـ {position['symbol']} عند السعر {current_price}")
            
            return actions
            
        except Exception as e:
            logger.error(f"❌ خطأ في فحص وقف الخسارة لـ {position['symbol']}: {e}")
            return []

    def _should_trigger_stop(self, current_price: float, stop_price: float, side: str) -> bool:
        """تحديد إذا كان يجب تنشيط وقف الخسارة"""
        if side == 'LONG':
            return current_price <= stop_price
        else:  # SHORT
            return current_price >= stop_price

    async def _check_take_profit(self, position: Dict) -> List[Dict]:
        """فحص مستويات جني الأرباح المتعددة"""
        actions = []
        
        try:
            current_price = position['current_price']
            entry_price = position['entry_price']
            side = position['side']
            take_profit_levels_hit = position.get('take_profit_levels_hit', set())
            
            for i, level in enumerate(self.risk_settings['take_profit_levels']):
                level_num = i + 1
                
                # إذا تم بالفعل جني الأرباح من هذا المستوى، نتخطاه
                if level_num in take_profit_levels_hit:
                    continue
                
                # حساب سعر جني الأرباح لهذا المستوى
                target_price = self._calculate_take_profit_price(
                    entry_price, level['profit'], side
                )
                
                # فحص إذا وصل السعر للمستوى
                if self._should_take_profit(current_price, target_price, side):
                    actions.append({
                        'type': 'TAKE_PROFIT',
                        'quantity': position['quantity'] * level['close'],
                        'price': current_price,
                        'target_price': target_price,
                        'level': level_num,
                        'profit_percent': level['profit'] * 100,
                        'close_percent': level['close'] * 100,
                        'reason': f'جني أرباح المستوى {level_num} - {level["profit"]*100:.2f}% ربح',
                        'timestamp': datetime.now()
                    })
                    
                    # وضع علامة أن هذا المستوى تم الوصول إليه
                    take_profit_levels_hit.add(level_num)
                    position['take_profit_levels_hit'] = take_profit_levels_hit
                    
                    logger.info(f"💰触发 جني أرباح المستوى {level_num} لـ {position['symbol']} عند السعر {current_price}")
            
            return actions
            
        except Exception as e:
            logger.error(f"❌ خطأ في فحص جني الأرباح لـ {position['symbol']}: {e}")
            return []

    def _calculate_take_profit_price(self, entry_price: float, profit_percent: float, side: str) -> float:
        """حساب سعر جني الأرباح"""
        if side == 'LONG':
            return entry_price * (1 + profit_percent)
        else:  # SHORT
            return entry_price * (1 - profit_percent)

    def _should_take_profit(self, current_price: float, target_price: float, side: str) -> bool:
        """تحديد إذا كان يجب جني الأرباح"""
        if side == 'LONG':
            return current_price >= target_price
        else:  # SHORT
            return current_price <= target_price

    async def _check_trailing_stop(self, position: Dict) -> List[Dict]:
        """فحص وتحديث وقف الخسارة المتحرك (Trailing Stop)"""
        # يمكن تطوير هذه الوظيفة لاحقاً لإضافة وقف خسارة متحرك
        return []

    async def calculate_position_size(self, symbol: str, risk_per_trade: float = 0.02) -> float:
        """
        حساب حجم الصفقة الأمثل بناءً على المخاطرة
        """
        try:
            # جلب معلومات الحساب
            margin_info = await self.binance.get_margin_info()
            account_balance = margin_info['total_wallet_balance']
            
            # جلب معلومات التداول للرمز
            exchange_info = await self.binance.get_exchange_info(symbol)
            
            if not exchange_info:
                return 0.0
            
            # حساب الحد الأقصى للمخاطرة
            max_risk_amount = account_balance * risk_per_trade
            
            # جلب السعر الحالي
            current_price = await self.binance.get_current_price(symbol)
            
            # حساب حجم الصفقة
            position_size = max_risk_amount / current_price
            
            # تطبيق حدود الرمز
            min_qty = exchange_info.get('min_qty', 0.001)
            step_size = exchange_info.get('step_size', 0.001)
            
            # تقريب الحجم ليتناسب مع step_size
            position_size = math.floor(position_size / step_size) * step_size
            position_size = max(position_size, min_qty)
            
            logger.debug(f"📏 حجم الصفقة المحسوب لـ {symbol}: {position_size}")
            return position_size
            
        except Exception as e:
            logger.error(f"❌ خطأ في حساب حجم الصفقة لـ {symbol}: {e}")
            return 0.0

    def get_risk_summary(self, position: Dict) -> Dict:
        """الحصول على ملخص المخاطرة للصفقة"""
        try:
            current_price = position['current_price']
            entry_price = position['entry_price']
            side = position['side']
            stop_levels = position.get('stop_loss_levels', {})
            
            # حساب المخاطرة الحالية
            if stop_levels:
                stop_price = stop_levels['full_stop']
                if side == 'LONG':
                    risk_percent = (entry_price - stop_price) / entry_price * 100
                    current_to_stop = (current_price - stop_price) / (entry_price - stop_price) * 100
                else:
                    risk_percent = (stop_price - entry_price) / entry_price * 100
                    current_to_stop = (stop_price - current_price) / (stop_price - entry_price) * 100
            else:
                risk_percent = 0
                current_to_stop = 0
            
            # حساب مستويات جني الأرباح
            take_profit_levels = []
            for i, level in enumerate(self.risk_settings['take_profit_levels']):
                target_price = self._calculate_take_profit_price(
                    entry_price, level['profit'], side
                )
                take_profit_levels.append({
                    'level': i + 1,
                    'target_price': target_price,
                    'profit_percent': level['profit'] * 100,
                    'close_percent': level['close'] * 100,
                    'hit': (i + 1) in position.get('take_profit_levels_hit', set())
                })
            
            return {
                'symbol': position['symbol'],
                'side': side,
                'entry_price': entry_price,
                'current_price': current_price,
                'risk_percent': risk_percent,
                'progress_to_stop': current_to_stop,
                'stop_loss_levels': stop_levels,
                'take_profit_levels': take_profit_levels,
                'partial_stop_hit': position.get('partial_stop_hit', False),
                'timestamp': datetime.now()
            }
            
        except Exception as e:
            logger.error(f"❌ خطأ في إنشاء ملخص المخاطرة لـ {position['symbol']}: {e}")
            return {}

# مثال على الاستخدام
async def main():
    """اختبار محرك المخاطرة"""
    from binance_engine import BinanceEngine
    
    # إعدادات الاختبار
    binance_config = {
        'api_key': 'YOUR_API_KEY',
        'api_secret': 'YOUR_API_SECRET',
        'testnet': True
    }
    
    risk_config = {
        'partial_stop_percent': 0.3,
        'partial_trigger_percent': 0.4,
        'min_stop_loss': 0.015,
        'max_stop_loss': 0.05,
        'volatility_multiplier': 1.5,
        'margin_risk_threshold': 70
    }
    
    try:
        # تهيئة المحركات
        binance_engine = BinanceEngine(binance_config)
        await binance_engine.initialize()
        
        risk_engine = RiskEngine(risk_config, binance_engine)
        
        # مثال على صفقة اختبارية
        test_position = {
            'symbol': 'BNBUSDT',
            'quantity': 0.1,
            'side': 'LONG',
            'entry_price': 300.0,
            'current_price': 305.0
        }
        
        # حساب الإجراءات
        actions = await risk_engine.calculate_actions(test_position)
        print(f"الإجراءات المحسوبة: {len(actions)}")
        
        # ملخص المخاطرة
        risk_summary = risk_engine.get_risk_summary(test_position)
        print(f"ملخص المخاطرة: {risk_summary}")
        
    except Exception as e:
        print(f"❌ خطأ: {e}")
    finally:
        await binance_engine.close()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
