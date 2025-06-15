import sqlite3
import uuid
import asyncio
import json
import csv
from io import StringIO
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes,
    filters, ConversationHandler, CallbackQueryHandler
)

TOKEN = '7500577126:AAGiInqScJ37mWgEPK1SX4RpaUbJUf0mfUk'
CHANNEL_ID = -1002558282695
MAIN_MASTER_ID = 177969495

ASK_NAME = 0
CONFIRM_MASTER = 1

MASTER_NAMES = {
    1001: "Аня",
    1002: "Полина",
    1003: "Юля"
}

MASTER_GENITIVE = {
    1001: "Ане",
    1002: "Полине",
    1003: "Юле"
}

FEEDBACK_QUESTIONS = [
    ("Качество работы", "quality"),
    ("Скорость работы", "speed"),
    ("Вежливость", "politeness"),
    ("Чистота", "cleanliness"),
    ("Готова ли ты порекомендовать мастера друзьям?", "recommendation")
]

ongoing_surveys = {}
pending_refs = {}  # chat_id -> {'type': 'text/photo', 'content': ..., 'caption': ...}
def init_db():
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS clients (
            chat_id INTEGER PRIMARY KEY,
            client_id TEXT UNIQUE,
            name TEXT,
            last_selected_master INTEGER
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id TEXT,
            type TEXT,
            content TEXT,
            caption TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS masters (
            user_id INTEGER PRIMARY KEY,
            name TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            quality INTEGER,
            speed INTEGER,
            politeness INTEGER,
            cleanliness INTEGER,
            recommendation INTEGER,
            master_id INTEGER
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pending_surveys (
            chat_id INTEGER PRIMARY KEY,
            client_id TEXT,
            step INTEGER,
            answers_json TEXT,
            message_id INTEGER
        )
    ''')
    conn.commit()
    conn.close()

def migrate_db():
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    try: cursor.execute('ALTER TABLE clients ADD COLUMN last_selected_master INTEGER')
    except: pass
    try: cursor.execute('ALTER TABLE feedback ADD COLUMN master_id INTEGER')
    except: pass
    conn.commit()
    conn.close()

def add_master(user_id, name):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO masters (user_id, name) VALUES (?, ?)', (user_id, name))
    conn.commit()
    conn.close()

def is_master(user_id):
    if user_id == MAIN_MASTER_ID:
        return True
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM masters WHERE user_id = ?', (user_id,))
    return cursor.fetchone() is not None

def set_client_master(chat_id, master_id):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE clients SET last_selected_master = ? WHERE chat_id = ?', (master_id, chat_id))
    conn.commit()
    conn.close()

def set_or_update_master(user_id: int, name: str):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO masters (user_id, name) VALUES (?, ?)', (user_id, name))
    conn.commit()
    conn.close()

def get_all_masters():
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, name FROM masters ORDER BY user_id')
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_client_master(chat_id):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('SELECT last_selected_master FROM clients WHERE chat_id = ?', (chat_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

def add_client(chat_id, client_id, name=None):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO clients (chat_id, client_id, name) VALUES (?, ?, ?)', (chat_id, client_id, name))
    if name:
        cursor.execute('UPDATE clients SET name = ? WHERE chat_id = ?', (name, chat_id))
    conn.commit()
    conn.close()

def get_client_by_chat(chat_id):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('SELECT client_id, name FROM clients WHERE chat_id = ?', (chat_id,))
    return cursor.fetchone()

def add_message(client_id, msg_type, content, caption=None):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('INSERT INTO messages (client_id, type, content, caption) VALUES (?, ?, ?, ?)', (client_id, msg_type, content, caption))
    conn.commit()
    conn.close()

def save_feedback(client_id, feedback_dict, master_id):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO feedback (client_id, quality, speed, politeness, cleanliness, recommendation, master_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (
        client_id,
        feedback_dict.get('quality'),
        feedback_dict.get('speed'),
        feedback_dict.get('politeness'),
        feedback_dict.get('cleanliness'),
        feedback_dict.get('recommendation'),
        master_id
    ))
    conn.commit()
    conn.close()

def save_pending_survey(chat_id, client_id, step, answers, message_id):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('''
        REPLACE INTO pending_surveys (chat_id, client_id, step, answers_json, message_id)
        VALUES (?, ?, ?, ?, ?)
    ''', (chat_id, client_id, step, json.dumps(answers), message_id))
    conn.commit()
    conn.close()

def load_pending_survey(chat_id):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('SELECT client_id, step, answers_json, message_id FROM pending_surveys WHERE chat_id = ?', (chat_id,))
    row = cursor.fetchone()
    if row:
        return {
            'client_id': row[0],
            'step': row[1],
            'answers': json.loads(row[2]),
            'message_id': row[3]
        }
    return None

def delete_pending_survey(chat_id):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('DELETE FROM pending_surveys WHERE chat_id = ?', (chat_id,))
    conn.commit()
    conn.close()
from telegram.constants import ParseMode

async def ask_master_choice(chat_id, context):
    keyboard = [[InlineKeyboardButton(name, callback_data=f"master_{mid}")] for mid, name in MASTER_NAMES.items()]
    await context.bot.send_message(
        chat_id=chat_id,
        text="Какому мастеру ты хочешь отправить референсы? 🌿",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def master_choice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    master_id = int(query.data.replace("master_", ""))
    set_client_master(chat_id, master_id)

    pending = pending_refs.get(chat_id)
    if pending:
        await send_ref_to_channel(chat_id, context, pending['type'], pending['content'], pending.get('caption'))
        await context.bot.send_message(chat_id, "Фото передано мастеру 🌱")
        del pending_refs[chat_id]
    else:
        await query.edit_message_text(f"{MASTER_NAMES.get(master_id)} ждёт твои референсы 🌱")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id

    if user_id == MAIN_MASTER_ID:
        await update.message.reply_text(
            "Привет, Полина! 💛\nВот доступные тебе команды:\n\n"
            "🔍 /get_feedback — посмотреть средние оценки по всем мастерам\n"
            "📋 /clients — список всех клиентов с именами и ID\n"
            "🗂 /feedback_raw <client_id> — все отзывы, оставленные этим клиентом\n"
            "📤 /export_feedback — экспорт всех отзывов в виде CSV-файла\n"
            "🧑‍🎨 /set_master <ID> <Имя> — добавить или переименовать мастера вручную\n"
            "📜 /list_masters — список всех зарегистрированных мастеров и их ID\n"
            "📦 /history <client_id> — история отправленных сообщений от клиента\n\n"
            "Ты можешь в любой момент снова ввести команду /start, чтобы увидеть этот список 💬"
        )
        return ConversationHandler.END

    # Обычный клиентский путь
    client = get_client_by_chat(chat_id)
    if client is None:
        client_id = str(uuid.uuid4())[:8]
        add_client(chat_id, client_id)
        await update.message.reply_text("Как к тебе обращаться? 🌻")
        return ASK_NAME
    else:
        await ask_master_choice(chat_id, context)
        return ConversationHandler.END


async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    name = update.message.text.strip()
    client = get_client_by_chat(chat_id)
    if client:
        add_client(chat_id, client[0], name)
        await ask_master_choice(chat_id, context)
    return ConversationHandler.END
from telegram.ext import CallbackContext

async def forward_to_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    client = get_client_by_chat(chat_id)
    if not client:
        await update.message.reply_text("Сначала напиши /start")
        return

    client_id, name = client
    name = name or "Без имени"

    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        caption = update.message.caption or ""
        pending_refs[chat_id] = {'type': 'photo', 'content': file_id, 'caption': caption}
    elif update.message.text:
        msg = update.message.text
        pending_refs[chat_id] = {'type': 'text', 'content': msg}

    master_id = get_client_master(chat_id)
    if not master_id:
        await ask_master_choice(chat_id, context)
        return

    gen_name = MASTER_GENITIVE.get(master_id, "мастеру")
    keyboard = [
        [InlineKeyboardButton("✅ Да", callback_data="confirm_master_yes"),
         InlineKeyboardButton("❌ Нет", callback_data="confirm_master_no")]
    ]
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"Фото отправляется {gen_name}. Верно? 🪴",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def confirm_master_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    if query.data == "confirm_master_yes":
        ref = pending_refs.get(chat_id)
        if ref:
            await send_ref_to_channel(chat_id, context, ref['type'], ref['content'], ref.get('caption'))
            await context.bot.send_message(chat_id, "Фото передано мастеру 🌱")
            del pending_refs[chat_id]
    elif query.data == "confirm_master_no":
        await ask_master_choice(chat_id, context)
        await query.edit_message_text("Выбери другого мастера 🌿")

async def send_ref_to_channel(chat_id, context, msg_type, content, caption=None):
    client = get_client_by_chat(chat_id)
    if not client:
        return
    client_id, name = client
    name = name or "Без имени"
    master_id = get_client_master(chat_id)

    if msg_type == "photo":
        add_message(client_id, 'photo', content, caption)
        await context.bot.send_photo(
            chat_id=CHANNEL_ID,
            photo=content,
            caption=f"📷 От {name} (ID: {client_id})\n{caption or ''}",
            parse_mode=ParseMode.MARKDOWN
        )
    elif msg_type == "text":
        add_message(client_id, 'text', content)
        await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=f"✉️ От {name} (ID: {client_id}):\n{content}",
            parse_mode=ParseMode.MARKDOWN
        )

    context.application.create_task(schedule_feedback(client_id, chat_id, context))

async def schedule_feedback(client_id, chat_id, context):
    await asyncio.sleep(5)  # В проде заменить на 10800 (3 часа)
    ongoing_surveys[chat_id] = {'client_id': client_id, 'answers': {}, 'step': 0}
    await send_feedback_question(chat_id, context)

async def send_feedback_question(chat_id, context, edit=False):
    survey = ongoing_surveys.get(chat_id)
    if not survey:
        return
    step = survey['step']
    question, key = FEEDBACK_QUESTIONS[step]

    if key == "recommendation":
        keyboard = [[InlineKeyboardButton("Да", callback_data='5'),
                     InlineKeyboardButton("Нет", callback_data='0')]]
    else:
        keyboard = [[InlineKeyboardButton(str(i), callback_data=str(i)) for i in range(1, 6)]]

    reply_markup = InlineKeyboardMarkup(keyboard)
    text = f"Спасибо, что выбрала нас. Пожалуйста, оцени мастера по параметрам ниже. Все ответы анонимные 🌻\n*{question}*"

    if edit:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=survey['message_id'],
            text=text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
        survey['message_id'] = msg.message_id
        save_pending_survey(chat_id, survey['client_id'], step, survey['answers'], msg.message_id)

async def feedback_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    survey = ongoing_surveys.get(chat_id)
    if not survey:
        return

    step = survey['step']
    key = FEEDBACK_QUESTIONS[step][1]
    survey['answers'][key] = int(query.data)
    survey['step'] += 1

    if survey['step'] >= len(FEEDBACK_QUESTIONS):
        master_id = get_client_master(chat_id)
        save_feedback(survey['client_id'], survey['answers'], master_id)
        delete_pending_survey(chat_id)
        del ongoing_surveys[chat_id]
        await query.edit_message_text("Спасибо за оценку! Ждём новых референсов 💛")
    else:
        save_pending_survey(chat_id, survey['client_id'], survey['step'], survey['answers'], survey['message_id'])
        await send_feedback_question(chat_id, context, edit=True)

def get_feedback_summary_by_master():
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT master_id, AVG(quality), AVG(speed), AVG(politeness), AVG(cleanliness), AVG(recommendation)
        FROM feedback
        GROUP BY master_id
    ''')
    return cursor.fetchall()

async def get_feedback_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != MAIN_MASTER_ID:
        await update.message.reply_text("Команда доступна только главному мастеру.")
        return

    summary = get_feedback_summary_by_master()
    if not summary:
        await update.message.reply_text("Пока нет отзывов.")
        return

    text = "📊 *Оценки по мастерам:*\n\n"
    for mid, q, s, p, c, r in summary:
        name = MASTER_NAMES.get(mid, f"ID {mid}")
        text += (
            f"👩‍🎨 *{name}*:\n"
            f"  • Качество: {q:.2f} / 5\n"
            f"  • Скорость: {s:.2f} / 5\n"
            f"  • Вежливость: {p:.2f} / 5\n"
            f"  • Чистота: {c:.2f} / 5\n"
            f"  • Рекомендуют: {r * 20:.1f}%\n\n"
        )

    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
def get_all_clients():
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('SELECT client_id, name FROM clients ORDER BY name')
    rows = cursor.fetchall()
    conn.close()
    return rows

async def clients_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != MAIN_MASTER_ID:
        await update.message.reply_text("Команда доступна только главному мастеру.")
        return

    clients = get_all_clients()
    if not clients:
        await update.message.reply_text("Клиенты не найдены.")
        return

    text = "📋 *Список клиентов:*\n\n"
    for client_id, name in clients:
        name_display = name or "Без имени"
        text += f"• `{client_id}` — {name_display}\n"

    await update.message.reply_text(text, parse_mode='Markdown')
def get_feedback_by_client(client_id):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT timestamp, quality, speed, politeness, cleanliness, recommendation
        FROM feedback
        WHERE client_id = ?
        ORDER BY timestamp DESC
    ''', (client_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows

async def feedback_raw_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != MAIN_MASTER_ID:
        await update.message.reply_text("Команда доступна только главному мастеру.")
        return

    if not context.args:
        await update.message.reply_text("Использование: /feedback_raw <client_id>")
        return

    client_id = context.args[0]
    feedbacks = get_feedback_by_client(client_id)
    if not feedbacks:
        await update.message.reply_text("Нет отзывов от этого клиента.")
        return

    text = f"📋 *Отзывы от клиента `{client_id}`:*\n\n"
    for fb in feedbacks:
        ts, q, s, p, c, r = fb
        text += (
            f"🕓 {ts}\n"
            f"  • Качество: {q}\n"
            f"  • Скорость: {s}\n"
            f"  • Вежливость: {p}\n"
            f"  • Чистота: {c}\n"
            f"  • Рекомендует: {'Да' if r >= 3 else 'Нет'}\n\n"
        )

    await update.message.reply_text(text, parse_mode='Markdown')
def export_feedback_csv():
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT client_id, master_id, timestamp, quality, speed, politeness, cleanliness, recommendation
        FROM feedback
    ''')
    rows = cursor.fetchall()
    conn.close()

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(['client_id', 'master_id', 'timestamp', 'quality', 'speed', 'politeness', 'cleanliness', 'recommendation'])
    for row in rows:
        writer.writerow(row)
    output.seek(0)
    return output
async def set_master_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != MAIN_MASTER_ID:
        await update.message.reply_text("Команда доступна только главному мастеру.")
        return

    if len(context.args) < 2:
        await update.message.reply_text("Использование: /set_master <ID> <Имя>")
        return

    try:
        user_id = int(context.args[0])
        name = ' '.join(context.args[1:])
        set_or_update_master(user_id, name)
        await update.message.reply_text(f"Мастер с ID `{user_id}` теперь записан как *{name}*", parse_mode='Markdown')
    except ValueError:
        await update.message.reply_text("ID должен быть числом.")

async def list_masters_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != MAIN_MASTER_ID:
        await update.message.reply_text("Команда доступна только главному мастеру.")
        return

    masters = get_all_masters()
    if not masters:
        await update.message.reply_text("Нет зарегистрированных мастеров.")
        return

    text = "🧑‍🎨 Зарегистрированные мастера:\n\n"
    for uid, name in masters:
        text += f"• `{uid}` — {name}\n"

    await update.message.reply_text(text, parse_mode='Markdown')

async def export_feedback_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != MAIN_MASTER_ID:
        await update.message.reply_text("Команда доступна только главному мастеру.")
        return

    csv_buffer = export_feedback_csv()
    await context.bot.send_document(
        chat_id=update.message.chat_id,
        document=csv_buffer,
        filename="feedback_export.csv",
        caption="📤 Все отзывы в CSV"
    )


def main():
    init_db()
    migrate_db()

    for mid, name in MASTER_NAMES.items():
        add_master(mid, name)

    app = ApplicationBuilder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name)],
        },
        fallbacks=[]
    )

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("get_feedback", get_feedback_cmd))
    app.add_handler(CommandHandler("clients", clients_cmd))
    app.add_handler(CallbackQueryHandler(master_choice_handler, pattern="^master_"))
    app.add_handler(CallbackQueryHandler(confirm_master_handler, pattern="^confirm_master_"))
    app.add_handler(CallbackQueryHandler(feedback_button_handler))
    app.add_handler(CommandHandler("feedback_raw", feedback_raw_cmd))
    app.add_handler(CommandHandler("export_feedback", export_feedback_cmd))
    app.add_handler(CommandHandler("set_master", set_master_cmd))
    app.add_handler(CommandHandler("list_masters", list_masters_cmd))
    app.add_handler(MessageHandler(filters.PHOTO | (filters.TEXT & ~filters.COMMAND), forward_to_channel))

    print("Бот запущен...")
    app.run_polling()

if __name__ == '__main__':
    main()
