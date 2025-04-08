import logging
import sqlite3
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError
from telethon.tl.functions.channels import JoinChannelRequest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR
from dotenv import load_dotenv
import os

load_dotenv()

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

scheduler = AsyncIOScheduler()

conn = sqlite3.connect("sessions.db")
cursor = conn.cursor()

cursor.execute("CREATE TABLE IF NOT EXISTS groups (group_id INTEGER PRIMARY KEY AUTOINCREMENT, group_username TEXT UNIQUE)")
cursor.execute("CREATE TABLE IF NOT EXISTS sessions (user_id INTEGER PRIMARY KEY, session_string TEXT)")
cursor.execute("CREATE TABLE IF NOT EXISTS broadcasts ( id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, group_id INTEGER, session_string TEXT, broadcast_text TEXT, interval_minutes INTEGER, is_active BOOLEAN,FOREIGN KEY (user_id) REFERENCES users(id),FOREIGN KEY (group_id) REFERENCES groups(id));")
conn.commit()

bot = TelegramClient("bot", API_ID, API_HASH).start(bot_token=BOT_TOKEN)

auto_client = TelegramClient(StringSession(), API_ID, API_HASH)

@bot.on(events.NewMessage(pattern="/start"))
async def start(event):
    if event.sender_id == ADMIN_ID:
        buttons = [
            [Button.inline("➕ Добавить аккаунты", b"add_account")],
            [Button.inline("📢 Добавить группы", b"groups")],
            [Button.inline("👤 Мои аккаунты", b"my_accounts")],
            [Button.inline("📑 Мои группы", b"my_groups")]
        ]
        await event.respond("👋 Добро пожаловать, Админ!", buttons=buttons)
    else:
        await event.respond("⛔ Запрещено!")

phone_waiting = {}
code_waiting = {}
password_waiting = {}
user_clients = {}

client = TelegramClient(StringSession(), API_ID, API_HASH)

@bot.on(events.CallbackQuery(data=b"add_account"))
async def add_account(event):
    user_id = event.sender_id
    phone_waiting[user_id] = True
    await event.respond("📲 Напишите номер телефона аккаунта в формате: `+79998887766`")

@bot.on(events.NewMessage(func=lambda e: e.sender_id in phone_waiting and e.text.startswith("+") and e.text[1:].isdigit()))
async def get_phone(event):
    user_id = event.sender_id
    phone_number = event.text.strip()

    user_clients[user_id] = TelegramClient(StringSession(), API_ID, API_HASH)
    await user_clients[user_id].connect()

    await event.respond("⏳ Отправляю код подтверждения...")

    try:
        await user_clients[user_id].send_code_request(phone_number)
        code_waiting[user_id] = phone_number
        del phone_waiting[user_id]
        await event.respond("✅ Код отправлен! Введите его сюда:")
    except Exception as e:
        phone_waiting.pop(user_id, None)
        user_clients.pop(user_id, None)
        await event.respond(f"⚠ Произошла ошибка: {e}\nПопробуйте снова, нажав 'Добавить аккаунт'.")

@bot.on(events.NewMessage(func=lambda e: e.sender_id in code_waiting and e.text.isdigit()))
async def get_code(event):
    code = event.text.strip()
    user_id = event.sender_id
    phone_number = code_waiting[user_id]

    try:
        await user_clients[user_id].sign_in(phone_number, code)
        session_string = user_clients[user_id].session.save()
        me = await user_clients[user_id].get_me()

        cursor.execute("INSERT INTO sessions (user_id, session_string) VALUES (?, ?)", (me.id, session_string))
        conn.commit()

        del code_waiting[user_id]
        del user_clients[user_id]
        await event.respond("✅ Авторизация прошла успешно!")
    except SessionPasswordNeededError:
        password_waiting[user_id] = {"waiting": True, "last_message_id": event.message.id}
        await event.respond("⚠ Этот аккаунт защищен паролем. Отправьте пароль:")
    except Exception as e:
        del code_waiting[user_id]
        user_clients.pop(user_id, None)
        await event.respond(f"❌ Неверный код или ошибка: {e}\nПопробуйте снова, нажав 'Добавить аккаунт'.")

@bot.on(events.NewMessage(func=lambda e: e.sender_id in password_waiting and e.sender_id not in user_states))
async def get_password(event):
    user_id = event.sender_id

    if password_waiting[user_id]["waiting"] and event.message.id > password_waiting[user_id]["last_message_id"]:
        password = event.text.strip()
        try:
            await user_clients[user_id].sign_in(password=password)
            me = await user_clients[user_id].get_me()
            session_string = user_clients[user_id].session.save()

            cursor.execute("INSERT INTO sessions (user_id, session_string) VALUES (?, ?)", (me.id, session_string))
            conn.commit()

            del password_waiting[user_id]
            del user_clients[user_id]
            await event.respond("✅ Авторизация с паролем прошла успешно!")
        except Exception as e:
            await event.respond(f"⚠ Ошибка при вводе пароля: {e}\nПопробуйте снова, нажав 'Добавить аккаунт'.")

user_sessions_account_spam = {}
active_spam = {}

active_broadcasts = {}

@bot.on(events.CallbackQuery(data=b"my_accounts"))
async def my_accounts(event):
    cursor.execute("SELECT user_id, session_string FROM sessions")
    accounts = cursor.fetchall()

    if not accounts:
        await event.respond("❌ У вас нет добавленных аккаунтов.")
        return

    buttons = []
    for user_id, session_string in accounts:
        session = StringSession(session_string)
        client = TelegramClient(session, API_ID, API_HASH)
        await client.connect()
        try:
            me = await client.get_me()
            username = me.first_name if me.first_name else "Без ника"
            buttons.append([Button.inline(f"👤 {username}", f"account_info_{user_id}")])
        except Exception as e:
            buttons.append([Button.inline(f"⚠ Ошибка при загрузке аккаунта", f"error_{user_id}")])

    await event.respond("📱 **Список ваших аккаунтов:**", buttons=buttons)

@bot.on(events.CallbackQuery(data=lambda data: data.decode().startswith("account_info_")))
async def handle_account_button(event):
    data = event.data.decode()
    user_id = int(data.split("_")[2])

    cursor.execute("SELECT session_string FROM sessions WHERE user_id = ?", (user_id,))
    session_string_row = cursor.fetchone()
    session_string = session_string_row[0] if session_string_row else None

    if session_string:
        session = StringSession(session_string)
        client = TelegramClient(session, API_ID, API_HASH)
        await client.connect()
        try:
            me = await client.get_me()
            username = me.first_name if me.first_name else "Без имени"
            phone = me.phone if me.phone else "Не указан"

            group_buttons = [Button.inline("📋 Список групп", f"listOfgroups_{user_id}")]

            dialogs = await client.get_dialogs()
            groups = [dialog.name for dialog in dialogs if dialog.is_group]

            if groups:
                group_list = "\n".join(groups)
            else:
                group_list = "У пользователя нет групп."

            await event.respond(
                f"📢 **Меню для аккаунта {username}:**\n"
                f"📌 **Имя:** {username}\n"
                f"📞 **Номер:** `+{phone}`\n\n"
                f"📝 **Список групп:**\n{group_list}",
                buttons=[group_buttons]
            )

        except Exception as e:
            await event.respond(f"⚠ Ошибка при загрузке информации: {e}")
    else:
        await event.respond("⚠ Не удалось найти аккаунт.")

@bot.on(events.CallbackQuery(data=lambda data: data.decode().startswith("listOfgroups_")))
async def handle_groups_list(event):
    data = event.data.decode()
    user_id = int(data.split("_")[1])

    cursor.execute("SELECT session_string FROM sessions WHERE user_id = ?", (user_id,))
    session_string_row = cursor.fetchone()
    session_string = session_string_row[0] if session_string_row else None

    if session_string:
        session = StringSession(session_string)
        client = TelegramClient(session, API_ID, API_HASH)
        await client.connect()
        try:
            dialogs = await client.get_dialogs()

            group_buttons = []

            for dialog in dialogs:
                if dialog.is_group:
                    group_buttons.append(Button.inline(f"📱 {dialog.name}", f"group_info_{user_id}_{dialog.id}"))

            if group_buttons:
                rows = [[button] for button in group_buttons]
                await event.respond(
                    "📋 **Список групп, в которых вы состоите:**",
                    buttons=rows
                )
            else:
                await event.respond("⚠ Вы не состоите в группах.")
        except Exception as e:
            await event.respond(f"⚠ Ошибка при загрузке списка групп: {e}")
    else:
        await event.respond("⚠ Не удалось найти аккаунт.")

broadcast_jobs = {}

@bot.on(events.CallbackQuery(data=lambda data: data.decode().startswith("group_info_")))
async def handle_group_info(event):
    data = event.data.decode()
    user_id, group_id = map(int, data.split("_")[2:])

    cursor.execute("SELECT session_string FROM sessions WHERE user_id = ?", (user_id,))
    session_string_row = cursor.fetchone()
    session_string = session_string_row[0] if session_string_row else None

    if session_string:
        session = StringSession(session_string)
        client = TelegramClient(session, API_ID, API_HASH)
        await client.connect()
        try:
            group = await client.get_entity(group_id)

            me = await client.get_me()
            account_name = me.first_name if me.first_name else "Без имени"

            cursor.execute("SELECT broadcast_text, interval_minutes FROM broadcasts WHERE user_id = ? AND group_id = ?", (user_id, group_id))
            broadcast_data = cursor.fetchone()

            if broadcast_data:
                broadcast_text, interval_minutes = broadcast_data
                text_display = f"📩 **Текущий текст рассылки:**\n{broadcast_text}\n⏳ **Интервал:** {interval_minutes} минут"
            else:
                text_display = "📩 **Текущий текст рассылки:** ❌ Не задан\n⏳ **Интервал:** ❌ Не задан"

            broadcast_job = None
            for job in scheduler.get_jobs():
                if job.id == f"broadcast_{user_id}_{group_id}":
                    broadcast_job = job
                    break

            if broadcast_job and broadcast_job.next_run_time:
                status = "✅ Активна"
            else:
                status = "⛔ Остановлена"

            keyboard = [
                [Button.inline("📝 Текст и Интервал рассылки", f"broadcasttextinterval_{user_id}_{group_id}")],
                [Button.inline("✅ Начать/возобновить рассылку", f"startresumebroadcast_{user_id}_{group_id}")],
                [Button.inline("⛔ Остановить рассылку", f"stop_accountbroadcast_{user_id}_{group_id}")]
            ]

            await event.respond(
                f"📢 **Меню рассылки для группы {group.title} от аккаунта {account_name}:**\n\n"
                f"{text_display}\n"
                f"🟢 **Статус рассылки:** {status}",
                buttons=keyboard
            )
        except Exception as e:
            await event.respond(f"⚠ Ошибка при получении информации о группе: {e}")
    else:
        await event.respond("⚠ Не удалось найти аккаунт.")

user_states = {}

@bot.on(events.CallbackQuery(data=lambda data: data.decode().startswith("broadcasttextinterval_")))
async def handle_broadcast_text_interval(event):
    data = event.data.decode()
    user_id, group_id = map(int, data.split("_")[1:])

    async with bot.conversation(event.sender_id) as conv:
        user_states[event.sender_id] = "text_and_interval_waiting"

        await event.respond("📝 Пожалуйста, отправьте текст для рассылки.")
        new_broadcast_text_event = await conv.wait_event(events.NewMessage(from_users=event.sender_id))
        new_broadcast_text = new_broadcast_text_event.text

        await event.respond("⏳ Пожалуйста, отправьте интервал рассылки в минут.")
        try:
            new_interval_minutes_event = await conv.wait_event(events.NewMessage(from_users=event.sender_id))
            new_interval_minutes = int(new_interval_minutes_event.text)

            cursor.execute("SELECT * FROM broadcasts WHERE user_id = ? AND group_id = ?", (user_id, group_id))
            existing_row = cursor.fetchone()

            if existing_row:
                update_broadcast_data(user_id, group_id, new_broadcast_text, new_interval_minutes)
                await event.respond(f"✅ Текст рассылки успешно обновлен на: {new_broadcast_text}\n⏳ Интервал рассылки обновлен на {new_interval_minutes} минут.")
            else:
                create_broadcast_data(user_id, group_id, new_broadcast_text, new_interval_minutes)
                await event.respond(f"✅ Текст рассылки и интервал были успешно добавлены:\n{new_broadcast_text}\n⏳ Интервал рассылки — {new_interval_minutes} минут.")

            del user_states[event.sender_id]

        except ValueError:
            await event.respond("⚠ Пожалуйста, введите корректное число минут для интервала.")

            del user_states[event.sender_id]

def create_broadcast_data(user_id, group_id, broadcast_text, interval_minutes):
    cursor.execute("""
        INSERT INTO broadcasts (user_id, group_id, broadcast_text, interval_minutes, is_active)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, group_id, broadcast_text, interval_minutes, False))
    conn.commit()

def update_broadcast_data(user_id, group_id, broadcast_text, interval_minutes):
    cursor.execute("""
        UPDATE broadcasts
        SET broadcast_text = ?, interval_minutes = ?
        WHERE user_id = ? AND group_id = ?
    """, (broadcast_text, interval_minutes, user_id, group_id))
    conn.commit()

@bot.on(events.CallbackQuery(data=lambda data: data.decode().startswith("startresumebroadcast_")))
async def start_resume_broadcast(event):
    data = event.data.decode()
    parts = data.split("_")

    if len(parts) < 3:
        await event.respond("⚠ Произошла ошибка при обработке данных. Попробуйте еще раз.")
        return

    try:
        user_id = int(parts[1])
        group_id = int(parts[2])
    except ValueError as e:
        await event.respond(f"⚠ Ошибка при извлечении данных: {e}")
        return

    job_id = f"broadcast_{user_id}_{group_id}"
    existing_job = scheduler.get_job(job_id)

    if existing_job:
        await event.respond("⚠ Рассылка уже активна для этой группы.")
        return

    cursor.execute("SELECT broadcast_text, interval_minutes FROM broadcasts WHERE user_id = ? AND group_id = ?", (user_id, group_id))
    row = cursor.fetchone()

    if row:
        broadcast_text, interval_minutes = row
        if not broadcast_text or not interval_minutes or interval_minutes <= 0:
            await event.respond("⚠ Пожалуйста, убедитесь, что текст рассылки и корректный интервал установлены.")
            return

        session_string_row = cursor.execute("SELECT session_string FROM sessions WHERE user_id = ?", (user_id,)).fetchone()
        if not session_string_row:
            await event.respond("⚠ Ошибка: не найден session_string для аккаунта.")
            return

        session_string = session_string_row[0]
        session = StringSession(session_string)
        client = TelegramClient(session, API_ID, API_HASH)

        await client.connect()

        try:
            group = await client.get_entity(group_id)
            group_title = group.title
        except Exception as e:
            await event.respond(f"⚠ Ошибка при получении информации о группе: {e}")
            return
        finally:
            await client.disconnect()

        async def send_broadcast():
            session = StringSession(session_string)
            client = TelegramClient(session, API_ID, API_HASH)

            await client.connect()
            try:
                group = await client.get_entity(group_id)
                await client.send_message(group, broadcast_text)
            except Exception as e:
                print(f"Ошибка отправки сообщения в группу: {e}")
            finally:
                await client.disconnect()

        scheduler.add_job(
            send_broadcast,
            IntervalTrigger(minutes=interval_minutes),
            id=job_id,
            replace_existing=True
        )

        await event.respond(f"✅ Рассылка в группу **{group_title}** начата!")
        if not scheduler.running:
            scheduler.start()
    else:
        await event.respond("⚠ Рассылка еще не настроена для этой группы.")

@bot.on(events.CallbackQuery(data=lambda data: data.decode().startswith("stop_accountbroadcast_")))
async def stop_broadcast(event):
    data = event.data.decode()
    try:
        user_id, group_id = map(int, data.split("_")[2:])

    except ValueError as e:
        await event.respond(f"⚠ Ошибка при извлечении user_id и group_id: {e}")

        return
    session_string = cursor.execute("SELECT session_string FROM sessions WHERE user_id = ?", (user_id,)).fetchone()[0]
    session = StringSession(session_string)
    client = TelegramClient(session, API_ID, API_HASH)
    await client.connect()
    group = await client.get_entity(group_id)
    job_id = f"broadcast_{user_id}_{group_id}"
    job = scheduler.get_job(job_id)
    if job:
        job.remove()
        cursor.execute("UPDATE broadcasts SET is_active = ? WHERE user_id = ? AND group_id = ?", (False, user_id, group_id))
        conn.commit()

        await event.respond(f"⛔ Рассылка в группу **{group.title}** остановлена.")
    else:
        await event.respond(f"⚠ Рассылка в группу **{group.title}** не была запущена.")

user_sessions_phone = {}

@bot.on(events.CallbackQuery(data=b"delete_account"))
async def handle_delete_account(event):
    user_sessions_phone[event.sender_id] = {"step": "awaiting_phone"}
    await event.respond("📲 Введите номер телефона аккаунта, который нужно удалить:")

@bot.on(events.NewMessage)
async def handle_user_input(event):
    user_state = user_sessions_phone.get(event.sender_id)

    if user_state and user_state["step"] == "awaiting_phone":
        phone_number = event.text.strip()

        if phone_number.startswith("+") and phone_number[1:].isdigit():
            cursor.execute("SELECT user_id FROM sessions WHERE session_string = ?", (phone_number,))
            user = cursor.fetchone()

            if user:
                cursor.execute("DELETE FROM sessions WHERE session_string = ?", (phone_number,))
                conn.commit()
                await event.respond(f"✅ Аккаунт с номером {phone_number} успешно удален.")
            else:
                await event.respond("⚠ Этот аккаунт не найден в базе данных.")

            user_sessions_phone.pop(event.sender_id, None)
        else:
            await event.respond("⚠ Пожалуйста, введите корректный номер телефона, начиная с '+'.")

cursor.execute("CREATE TABLE IF NOT EXISTS groups (group_id INTEGER PRIMARY KEY AUTOINCREMENT, group_username TEXT UNIQUE)")
conn.commit()

user_sessions = {}

@bot.on(events.CallbackQuery(data=b"groups"))
async def manage_groups(event):
    user_sessions[event.sender_id] = {"step": "awaiting_group_username"}
    await event.respond("📲 Напишите @username группы, чтобы добавить её в базу данных:")

@bot.on(events.NewMessage)
async def handle_group_input(event):
    user_state = user_sessions.pop(event.sender_id, None) 

    if user_state and user_state["step"] == "awaiting_group_username":
        group_username = event.text.strip()

        if group_username.startswith("@") and " " not in group_username:  
            try:
                cursor.execute("INSERT INTO groups (group_username) VALUES (?)", (group_username,))
                conn.commit()
                await event.respond(f"✅ Группа {group_username} успешно добавлена в базу данных!")
            except sqlite3.IntegrityError:
                await event.respond("⚠ Эта группа уже существует в базе данных.")
        else:
            await event.respond("⚠ Ошибка! Неправильный формат. Попробуйте снова, нажав кнопку.")



@bot.on(events.CallbackQuery(data=b"my_groups"))
async def my_groups(event):
    cursor.execute("SELECT group_username FROM groups")
    groups = cursor.fetchall()

    if not groups:
        await event.respond("❌ У вас нет добавленных групп.")
        return

    message = "📑 **Список добавленных групп:**\n"

    for group in groups:
        message += f"📌 {group[0]}\n"
        buttons = [
            [Button.inline("❌ Удалить группу", b"delete_group")],
            [Button.inline("➕ Добавить все аккаунты в эти группы", b"add_all_accounts_to_groups")]
        ]
    await event.respond(message, buttons=buttons)

@bot.on(events.CallbackQuery(data=b"add_all_accounts_to_groups"))
async def add_all_accounts_to_groups(event):
    cursor.execute("SELECT session_string FROM sessions")
    accounts = cursor.fetchall()

    cursor.execute("SELECT group_username FROM groups")
    groups = cursor.fetchall()

    if not accounts:
        await event.respond("❌ Нет добавленных аккаунтов.")
        return

    if not groups:
        await event.respond("❌ Нет добавленных групп.")
        return

    group_list = "\n".join([f"📌 {group[0]}" for group in groups])
    await event.respond(f"✅ Аккаунты успешно добавлены в следующие группы:\n{group_list}")

    for account in accounts:
        session = StringSession(account[0])
        client = TelegramClient(session, API_ID, API_HASH)
        await client.connect()
        try:

            for group in groups:
                await client(JoinChannelRequest(group[0]))
        except Exception as e:
            await event.respond(f"⚠ Ошибка при добавлении аккаунта: {e}")

user_sessions_deliting = {}

@bot.on(events.CallbackQuery(data=b"delete_group"))
async def handle_delete_group(event):
    user_sessions_deliting[event.sender_id] = {"step": "awaiting_group_username"}
    await event.respond("📲 Введите @username группы, которую нужно удалить:")

@bot.on(events.NewMessage)
async def handle_user_input(event):
    user_state = user_sessions_deliting.get(event.sender_id)

    if user_state and user_state["step"] == "awaiting_group_username":
        group_username = event.text.strip()

        if group_username.startswith("@"):
            cursor.execute("SELECT * FROM groups WHERE group_username = ?", (group_username,))
            group = cursor.fetchone()

            if group:
                cursor.execute("DELETE FROM groups WHERE group_username = ?", (group_username,))
                conn.commit()
                await event.respond(f"✅ Группа {group_username} успешно удалена из базы данных!")
            else:
                await event.respond("⚠ Группа с именем {group_username} не найдена в базе данных.")

            user_sessions_deliting.pop(event.sender_id, None)
        else:
            await event.respond("⚠ Пожалуйста, введите корректный @username группы, начиная с '@'.")
            return

print("🚀 Бот запущен...")
bot.run_until_disconnected()