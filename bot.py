import sqlite3
import uuid
import asyncio
import json
import csv
import os
from io import StringIO
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes,
    filters, ConversationHandler, CallbackQueryHandler
)
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1002558282695"))
MAIN_MASTER_ID = int(os.getenv("MAIN_MASTER_ID", "177969495"))

ASK_NAME = 0
CONFIRM_MASTER = 1

MASTER_NAMES = {1001: "Анна", 1002: "Полина", 1003: "Александра"}
MASTER_GENITIVE = {1001: "Анне", 1002: "Полине", 1003: "Александре"}

FEEDBACK_QUESTIONS = [
    ("Качество работы", "quality"),
    ("Скорость работы", "speed"),
    ("Вежливость", "politeness"),
    ("Чистота", "cleanliness"),
    ("Готова ли ты порекомендовать мастера друзьям?", "recommendation")
]

ongoing_surveys = {}
pending_refs = {}
media_groups = {}
media_timeouts = {}

def init_db():
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS clients (
            chat_id INTEGER PRIMARY KEY,
            client_id TEXT UNIQUE,
            name TEXT,
            last_selected_master INTEGER
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id TEXT,
            type TEXT,
            content TEXT,
            caption TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS masters (
            user_id INTEGER PRIMARY KEY,
            name TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            quality INTEGER,
            speed INTEGER,
            politeness INTEGER,
            cleanliness INTEGER,
            recommendation INTEGER,
            master_id INTEGER,
            text_feedback TEXT
        )
    ''')
    c.execute('''
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
    c = conn.cursor()
    try: c.execute('ALTER TABLE clients ADD COLUMN last_selected_master INTEGER')
    except: pass
    try: c.execute('ALTER TABLE feedback ADD COLUMN master_id INTEGER')
    except: pass
    try: c.execute('ALTER TABLE feedback ADD COLUMN text_feedback TEXT')
    except: pass
    conn.commit()
    conn.close()
def add_client(chat_id, client_id, name=None):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO clients (chat_id, client_id, name) VALUES (?, ?, ?)',
              (chat_id, client_id, name))
    if name:
        c.execute('UPDATE clients SET name = ? WHERE chat_id = ?', (name, chat_id))
    conn.commit()
    conn.close()

def get_client_by_chat(chat_id):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute('SELECT client_id, name FROM clients WHERE chat_id = ?', (chat_id,))
    row = c.fetchone()
    conn.close()
    return row

def set_client_master(chat_id, master_id):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute('UPDATE clients SET last_selected_master = ? WHERE chat_id = ?', (master_id, chat_id))
    conn.commit()
    conn.close()

def get_client_master(chat_id):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute('SELECT last_selected_master FROM clients WHERE chat_id = ?', (chat_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def add_message(client_id, msg_type, content, caption=None):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute('INSERT INTO messages (client_id, type, content, caption) VALUES (?, ?, ?, ?)',
              (client_id, msg_type, content, caption))
    conn.commit()
    conn.close()

def save_feedback(client_id, feedback_dict, master_id, text_feedback=None):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute('''
        INSERT INTO feedback (client_id, quality, speed, politeness, cleanliness, recommendation, master_id, text_feedback)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        client_id,
        feedback_dict.get('quality'),
        feedback_dict.get('speed'),
        feedback_dict.get('politeness'),
        feedback_dict.get('cleanliness'),
        feedback_dict.get('recommendation'),
        master_id,
        text_feedback
    ))
    conn.commit()
    conn.close()

def save_pending_survey(chat_id, client_id, step, answers, message_id):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute('''
        REPLACE INTO pending_surveys (chat_id, client_id, step, answers_json, message_id)
        VALUES (?, ?, ?, ?, ?)
    ''', (chat_id, client_id, step, json.dumps(answers), message_id))
    conn.commit()
    conn.close()

def load_pending_survey(chat_id):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute('SELECT client_id, step, answers_json, message_id FROM pending_surveys WHERE chat_id = ?', (chat_id,))
    row = c.fetchone()
    conn.close()
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
    c = conn.cursor()
    c.execute('DELETE FROM pending_surveys WHERE chat_id = ?', (chat_id,))
    conn.commit()
    conn.close()

def set_or_update_master(user_id, name):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO masters (user_id, name) VALUES (?, ?)', (user_id, name))
    conn.commit()
    conn.close()

def get_all_masters():
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute('SELECT user_id, name FROM masters ORDER BY user_id')
    rows = c.fetchall()
    conn.close()
    return rows
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id

    if user_id == MAIN_MASTER_ID:
        await update.message.reply_text(
            "Привет, Полина! 💛\nВот доступные тебе команды:\n\n"
            "🔍 /get_feedback — посмотреть средние оценки по всем мастерам\n"
            "📋 /clients — список всех клиентов с именами и ID\n"
            "🗂 /feedback_raw <client_id> — все оценки клиента\n"
            "📤 /export_feedback — экспорт всех оценок в CSV\n"
            "📝 /text_feedbacks [Имя] — текстовые отзывы (и CSV)\n"
            "🧑‍🎨 /set_master <ID> <Имя> — добавить или переименовать мастера\n"
            "📜 /list_masters — список всех мастеров\n\n"
            "Ты можешь в любой момент снова ввести команду /start, чтобы увидеть этот список 💬"
        )
        return ConversationHandler.END

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
        await send_ref_to_channel(chat_id, context, pending['type'], pending['content'])
        await context.bot.send_message(chat_id, "Фото передано мастеру 🌙")
        del pending_refs[chat_id]
    else:
        await query.edit_message_text(f"{MASTER_NAMES.get(master_id)} ждёт твои референсы 🌱")
async def forward_to_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id

    if chat_id in ongoing_surveys and ongoing_surveys[chat_id].get("waiting_for_text"):
        text = update.message.text.strip()
        data = ongoing_surveys.pop(chat_id)
        save_feedback(data["client_id"], data["answers"], get_client_master(chat_id), text_feedback=text)
        delete_pending_survey(chat_id)
        await update.message.reply_text("Спасибо большое за отзыв! 🌟")
        return

    client = get_client_by_chat(chat_id)
    if not client:
        await update.message.reply_text("Сначала напиши /start")
        return

    client_id, name = client
    name = name or "Без имени"

    if update.message.photo:
        mgid = update.message.media_group_id
        fid = update.message.photo[-1].file_id
        cap = update.message.caption or ""
        if mgid:
            grp = media_groups.get(chat_id)
            if not grp or grp['media_group_id'] != mgid:
                media_groups[chat_id] = {'media_group_id': mgid, 'items': [(fid, cap)]}
            else:
                media_groups[chat_id]['items'].append((fid, cap))
            if chat_id in media_timeouts:
                media_timeouts[chat_id].cancel()
            media_timeouts[chat_id] = context.application.create_task(send_album_delayed(chat_id, context.application))

            return
        else:
            pending_refs[chat_id] = {'type': 'photo', 'content': [(fid, cap)]}

    elif update.message.text:
        pending_refs[chat_id] = {'type': 'text', 'content': update.message.text.strip()}

    else:
        await update.message.reply_text("Можно отправлять только фото или текст 🌿")
        return

    m_id = get_client_master(chat_id)
    if not m_id:
        await ask_master_choice(chat_id, context)
        return

    gen = MASTER_GENITIVE.get(m_id, "мастеру")
    kb = [[InlineKeyboardButton("✅ Да", callback_data="confirm_master_yes"),
           InlineKeyboardButton("❌ Нет", callback_data="confirm_master_no")]]
    await context.bot.send_message(chat_id=chat_id, text=f"Фото отправляется {gen}. Верно? 🪴", reply_markup=InlineKeyboardMarkup(kb))

async def send_album_delayed(chat_id, app):
    await asyncio.sleep(2.5)
    grp = media_groups.pop(chat_id, None)
    if not grp:
        return
    pending_refs[chat_id] = {'type': 'photo', 'content': grp['items']}
    m_id = get_client_master(chat_id)
    if not m_id:
        await ask_master_choice(chat_id, app)
        return
    gen = MASTER_GENITIVE.get(m_id, "мастеру")
    kb = [[InlineKeyboardButton("✅ Да", callback_data="confirm_master_yes"),
           InlineKeyboardButton("❌ Нет", callback_data="confirm_master_no")]]
    await app.bot.send_message(chat_id=chat_id, text=f"Фото отправляется {gen}. Верно? 🪴", reply_markup=InlineKeyboardMarkup(kb))


async def confirm_master_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cid = query.message.chat_id
    if query.data == "confirm_master_yes":
        if cid in media_groups:
            grp = media_groups.pop(cid)
            pending_refs[cid] = {'type': 'photo', 'content': grp['items']}
        ref = pending_refs.get(cid)
        if ref:
            await send_ref_to_channel(cid, context, ref['type'], ref['content'])
            await context.bot.send_message(cid, "Фото передано мастеру 🌙")
            del pending_refs[cid]
    else:
        await ask_master_choice(cid, context)
        await query.edit_message_text("Выбери другого мастера 🌿")
async def send_ref_to_channel(chat_id, context, msg_type, content):
    client = get_client_by_chat(chat_id)
    if not client:
        return
    cid, name = client
    name = name or "Без имени"

    if msg_type == "photo":
        if isinstance(content, list):
            media = []
            for i, (fid, cap) in enumerate(content[:10]):
                caption = f"📷 От {name} (ID: {cid})\n{cap}" if i == 0 else None
                media.append(InputMediaPhoto(media=fid, caption=caption, parse_mode=ParseMode.MARKDOWN))
                add_message(cid, 'photo', fid, cap if i == 0 else None)
            await context.bot.send_media_group(chat_id=CHANNEL_ID, media=media)
        else:
            fid, cap = content
            add_message(cid, 'photo', fid, cap)
            await context.bot.send_photo(chat_id=CHANNEL_ID, photo=fid,
                                         caption=f"📷 От {name} (ID: {cid})\n{cap}", parse_mode=ParseMode.MARKDOWN)
    else:
        add_message(cid, 'text', content)
        await context.bot.send_message(chat_id=CHANNEL_ID,
                                       text=f"✉️ От {name} (ID: {cid}):\n{content}",
                                       parse_mode=ParseMode.MARKDOWN)

    context.application.create_task(schedule_feedback(cid, chat_id, context))

async def schedule_feedback(client_id, chat_id, context):
    await asyncio.sleep(10800)
    ongoing_surveys[chat_id] = {'client_id': client_id, 'answers': {}, 'step': 0}
    await send_feedback_question(chat_id, context)

async def send_feedback_question(chat_id, context, edit=False):
    survey = ongoing_surveys.get(chat_id)
    if not survey:
        return
    step = survey['step']
    question, key = FEEDBACK_QUESTIONS[step]
    kb = [[InlineKeyboardButton("Да", callback_data='5'),
           InlineKeyboardButton("Нет", callback_data='0')]] if key == "recommendation" else \
         [[InlineKeyboardButton(str(i), callback_data=str(i)) for i in range(1, 6)]]
    text = f"Спасибо, что выбрала нас 🌻\n\n*{question}*"
    if edit:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=survey['message_id'],
                                            text=text, reply_markup=InlineKeyboardMarkup(kb),
                                            parse_mode=ParseMode.MARKDOWN)
    else:
        msg = await context.bot.send_message(chat_id=chat_id, text=text,
                                             reply_markup=InlineKeyboardMarkup(kb),
                                             parse_mode=ParseMode.MARKDOWN)
        survey['message_id'] = msg.message_id
        save_pending_survey(chat_id, survey['client_id'], step, survey['answers'], msg.message_id)

async def feedback_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cid = query.message.chat_id
    survey = ongoing_surveys.get(cid)
    if not survey:
        return
    step = survey['step']
    key = FEEDBACK_QUESTIONS[step][1]
    survey['answers'][key] = int(query.data)
    survey['step'] += 1

    if survey['step'] >= len(FEEDBACK_QUESTIONS):
        await query.edit_message_text(
            "Пожалуйста, оставь небольшой текстовый отзыв 🫶\n"
            "Напиши его прямо сюда — это очень поможет мастерам развиваться"
        )
        survey['waiting_for_text'] = True
    else:
        save_pending_survey(cid, survey['client_id'], survey['step'], survey['answers'], survey['message_id'])
        await send_feedback_question(cid, context, edit=True)


async def text_feedbacks_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != MAIN_MASTER_ID:
        return await update.message.reply_text("💡 Нет доступа")

    name_filter = " ".join(context.args) if context.args else None
    mid_filter = None
    if name_filter:
        for mid, name in MASTER_NAMES.items():
            if name.lower() == name_filter.lower():
                mid_filter = mid
                break

    conn = sqlite3.connect('bot_data.db');
    c = conn.cursor()
    if mid_filter:
        c.execute(
            'SELECT client_id,master_id,text_feedback,timestamp FROM feedback WHERE text_feedback IS NOT NULL AND master_id = ? ORDER BY timestamp DESC',
            (mid_filter,))
    else:
        c.execute(
            'SELECT client_id,master_id,text_feedback,timestamp FROM feedback WHERE text_feedback IS NOT NULL ORDER BY timestamp DESC')
    rows = c.fetchall();
    conn.close()

    if not rows:
        return await update.message.reply_text("Текстовые отзывы ещё не оставляли.")

    text = "📝 *Текстовые отзывы:*\n\n"
    for cid, mid, txt, ts in rows:
        name = MASTER_NAMES.get(mid, f"ID {mid}")
        text += f"— `{cid}` → {name}:\n```\n{txt.strip()}\n```\n"

    if len(text) > 4000:
        chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)]
        for chunk in chunks:
            await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def export_text_feedbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != MAIN_MASTER_ID:
        return await update.message.reply_text("💡 Нет доступа")

    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute('SELECT client_id, master_id, text_feedback, timestamp FROM feedback WHERE text_feedback IS NOT NULL')
    rows = c.fetchall()
    conn.close()

    if not rows:
        return await update.message.reply_text("Нет текстовых отзывов для экспорта.")

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(['client_id', 'master_id', 'master_name', 'timestamp', 'text_feedback'])
    for cid, mid, txt, ts in rows:
        writer.writerow([cid, mid, MASTER_NAMES.get(mid, f"ID {mid}"), ts, txt.strip()])
    output.seek(0)

    await context.bot.send_document(
        chat_id=update.message.chat_id,
        document=output,
        filename="text_feedbacks.csv",
        caption="📄 Экспорт текстовых отзывов"
    )
def get_feedback_summary_by_master():
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT master_id,
               AVG(quality),
               AVG(speed),
               AVG(politeness),
               AVG(cleanliness),
               AVG(recommendation)
        FROM feedback
        GROUP BY master_id
    ''')
    rows = cursor.fetchall()
    conn.close()
    return rows


async def get_feedback_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != MAIN_MASTER_ID:
        return await update.message.reply_text("💡 Нет доступа")

    summary = get_feedback_summary_by_master()
    if not summary:
        return await update.message.reply_text("Нет отзывов.")

    text = "📊 *Средние оценки по мастерам:*\n"
    for mid, q, s, p, c, r in summary:
        name = MASTER_NAMES.get(mid, f"ID {mid}")
        text += f"👩‍🎨 *{name}*:\n"
        text += f"  • Качество: {q:.2f}\n"
        text += f"  • Скорость: {s:.2f}\n"
        text += f"  • Вежливость: {p:.2f}\n"
        text += f"  • Чистота: {c:.2f}\n"
        text += f"  • Рекомендуют: {r * 20:.1f}%\n\n"

    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


def main():
    init_db()
    migrate_db()
    for mid, name in MASTER_NAMES.items():
        set_or_update_master(mid, name)

    app = ApplicationBuilder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name)]},
        fallbacks=[]
    )

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("get_feedback", get_feedback_cmd))
    app.add_handler(CommandHandler("text_feedbacks", text_feedbacks_cmd))
    app.add_handler(CommandHandler("export_text_feedbacks", export_text_feedbacks))
    app.add_handler(CallbackQueryHandler(master_choice_handler, pattern="^master_"))
    app.add_handler(CallbackQueryHandler(confirm_master_handler, pattern="^confirm_master_"))
    app.add_handler(CallbackQueryHandler(feedback_button_handler))
    app.add_handler(MessageHandler(filters.PHOTO | (filters.TEXT & ~filters.COMMAND), forward_to_channel))

    print("Бот запущен...")
    app.run_polling()


if __name__ == '__main__':
    main()
