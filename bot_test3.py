"""
╔══════════════════════════════════════════════════╗
║       TELEGRAM БОТ-МОДЕРАТОР + AI  v3.5          ║
║   ТОЛЬКО ОСКОРБЛЕНИЯ (игнорирует маты)           ║
║   + САМООБУЧЕНИЕ новым оскорблениям              ║
║   + ДВА ВЛАДЕЛЬЦА                                ║
║   + HEALTHCHECK СЕРВЕР ДЛЯ ПИНГА                 ║
╚══════════════════════════════════════════════════╝
"""
import logging, json, re, asyncio, os, random
from datetime import datetime, timedelta
from telegram import Update, ChatPermissions
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from groq import Groq
from aiohttp import web

BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

# =====================================================
# 👑 ДВА ВЛАДЕЛЬЦА (укажите ID через запятую)
# =====================================================
OWNER_IDS = [6276293498, 5019808756]
# =====================================================

GROQ_MODEL = "llama-3.3-70b-versatile"

# Базовый список оскорблений
INSULTS = ["идиот","кретин","дебил","даун","тупой","тупая","глупый","глупая","дурак","дура","дурачок","урод","уродина","чмо","чмошник","лох","лошара","придурок","ненормальный","псих","бестолочь","болван","тупица","бездарь","идиотка","stupid","idiot","fool","dumb"]

# Библиотека для самообучения
insults_library = set()

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO, handlers=[logging.FileHandler("bot.log", encoding="utf-8"), logging.StreamHandler()])
logger = logging.getLogger(__name__)

DEFAULT_SETTINGS = {"mute_duration": 30, "warn_limit": 3, "ai_moderation": True, "ai_assistant": True}
chat_settings = {}
ROLE_NAMES = {0: "👤 Участник", 1: "🤝 Хелпер", 2: "🛡 Модератор", 3: "👑 Владелец"}
roles = {}
warnings = {}
unmute_tasks = {}
muted_users = {}
conversation_history = {}
casino_data = {}
groq_client = None
active_model = None

# ─────────────────────── Функция проверки владельца ─────────────
def is_owner(user_id: int) -> bool:
    return user_id in OWNER_IDS

# ─────────────────────── Загрузка библиотеки оскорблений ─────────
def load_insults_library():
    global insults_library
    try:
        with open("insults_library.txt", "r", encoding="utf-8") as f:
            insults_library = set(word.strip().lower() for word in f.readlines() if word.strip() and not word.startswith("#"))
        logger.info(f"📚 Загружена библиотека оскорблений: {len(insults_library)} слов")
    except FileNotFoundError:
        insults_library = set()
        logger.info("📚 Библиотека оскорблений не найдена, создана новая")
        with open("insults_library.txt", "w", encoding="utf-8") as f:
            f.write("# Новые оскорбления будут добавляться сюда автоматически\n")

def save_insult_to_library(word: str) -> bool:
    global insults_library
    word = word.lower().strip()
    if word and word not in INSULTS and word not in insults_library:
        insults_library.add(word)
        try:
            with open("insults_library.txt", "a", encoding="utf-8") as f:
                f.write(f"{word}\n")
            logger.info(f"📝 Добавлено новое оскорбление в библиотеку: {word}")
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения оскорбления: {e}")
    return False

def remove_insult_from_library(word: str) -> bool:
    global insults_library
    word = word.lower().strip()
    if word in insults_library:
        insults_library.remove(word)
        try:
            with open("insults_library.txt", "w", encoding="utf-8") as f:
                f.write("# Новые оскорбления будут добавляться сюда автоматически\n")
                for w in sorted(insults_library):
                    f.write(f"{w}\n")
            logger.info(f"🗑️ Удалено оскорбление из библиотеки: {word}")
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка удаления оскорбления: {e}")
    return False

# ─────────────────────── Настройки ───────────────────────────────
def load_settings():
    global chat_settings
    try:
        with open("settings3.json", "r") as f:
            chat_settings = {int(k): v for k, v in json.load(f).items()}
    except FileNotFoundError:
        chat_settings = {}
def save_settings():
    with open("settings3.json", "w") as f:
        json.dump(chat_settings, f, ensure_ascii=False, indent=2)
def get_setting(chat_id, key):
    return chat_settings.get(chat_id, {}).get(key, DEFAULT_SETTINGS[key])
def set_setting(chat_id, key, value):
    chat_settings.setdefault(chat_id, {})[key] = value
    save_settings()

# ─────────────────────── Роли ───────────────────────────────────
def load_roles():
    global roles
    try:
        with open("roles3.json", "r") as f:
            roles = {int(k): {int(u): v for u, v in m.items()} for k, m in json.load(f).items()}
    except FileNotFoundError:
        roles = {}
def save_roles():
    with open("roles3.json", "w") as f:
        json.dump(roles, f, ensure_ascii=False, indent=2)
def get_role(chat_id, user_id):
    if is_owner(user_id):
        return 3
    return roles.get(chat_id, {}).get(user_id, 0)
def set_role(chat_id, user_id, role):
    roles.setdefault(chat_id, {})[user_id] = role
    save_roles()

async def effective_role(update, context, user_id=None):
    uid = user_id or update.effective_user.id
    chat_id = update.effective_chat.id
    bot_role = get_role(chat_id, uid)
    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        if any(a.user.id == uid for a in admins):
            bot_role = max(bot_role, 2)
    except:
        pass
    return bot_role
async def require_role(update, context, min_role):
    role = await effective_role(update, context)
    if role < min_role:
        await update.message.reply_text(f"❌ Недостаточно прав. Нужна роль: {ROLE_NAMES.get(min_role, str(min_role))} или выше.")
        return False
    return True
async def require_owner(update, context):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Эта команда только для владельца бота!")
        return False
    return True

# ─────────────────────── Предупреждения ─────────────────────────
def load_warnings():
    global warnings
    try:
        with open("warnings3.json", "r") as f:
            warnings = {int(k): {int(u): c for u, c in v.items()} for k, v in json.load(f).items()}
    except FileNotFoundError:
        warnings = {}
def save_warnings():
    with open("warnings3.json", "w") as f:
        json.dump(warnings, f)
def get_warn_count(chat_id, user_id):
    return warnings.get(chat_id, {}).get(user_id, 0)
def add_warn(chat_id, user_id):
    warnings.setdefault(chat_id, {})[user_id] = warnings.get(chat_id, {}).get(user_id, 0) + 1
    save_warnings()
    return warnings[chat_id][user_id]
def reset_warns(chat_id, user_id):
    if chat_id in warnings and user_id in warnings[chat_id]:
        del warnings[chat_id][user_id]
        save_warnings()

# ─────────────────────── Мут / размут ───────────────────────────
def is_user_muted(chat_id, user_id):
    return chat_id in muted_users and user_id in muted_users[chat_id]
def add_muted_user(chat_id, user_id):
    muted_users.setdefault(chat_id, set()).add(user_id)
def remove_muted_user(chat_id, user_id):
    if chat_id in muted_users and user_id in muted_users[chat_id]:
        muted_users[chat_id].discard(user_id)
        if not muted_users[chat_id]:
            del muted_users[chat_id]
def cancel_unmute_task(chat_id, user_id):
    if chat_id in unmute_tasks and user_id in unmute_tasks[chat_id]:
        t = unmute_tasks[chat_id][user_id]
        if not t.done():
            t.cancel()
        del unmute_tasks[chat_id][user_id]
async def do_unmute(context, chat_id, user_id, user_name):
    try:
        remove_muted_user(chat_id, user_id)
        name = user_name or "Пользователь"
        await context.bot.send_message(chat_id=chat_id, text=f"🔊 [{name}](tg://user?id={user_id}) — мут снят!", parse_mode="Markdown")
        logger.info(f"Снят мут для {user_id} в чате {chat_id}")
    except Exception as e:
        logger.error(f"Ошибка снятия мута: {e}")
    finally:
        if chat_id in unmute_tasks and user_id in unmute_tasks[chat_id]:
            del unmute_tasks[chat_id][user_id]
async def schedule_unmute(context, chat_id, user_id, minutes, user_name=None):
    cancel_unmute_task(chat_id, user_id)
    async def _task():
        await asyncio.sleep(minutes * 60)
        await do_unmute(context, chat_id, user_id, user_name)
    task = asyncio.create_task(_task())
    unmute_tasks.setdefault(chat_id, {})[user_id] = task
async def mute_user(context, chat_id, user_id, minutes, user_name=None):
    add_muted_user(chat_id, user_id)
    await schedule_unmute(context, chat_id, user_id, minutes, user_name)
    logger.info(f"Мут для {user_id} на {minutes} мин")

# ─────────────────────── Казино ──────────────────────────────────
def load_casino():
    global casino_data
    try:
        with open("casino3.json", "r") as f:
            casino_data = {int(k): v for k, v in json.load(f).items()}
    except FileNotFoundError:
        casino_data = {}
def save_casino():
    with open("casino3.json", "w") as f:
        json.dump(casino_data, f)
def get_balance(user_id):
    if user_id not in casino_data:
        casino_data[user_id] = {"balance": 100, "last_bonus": 0}
        save_casino()
    return casino_data[user_id]["balance"]
def set_balance(user_id, amount):
    casino_data.setdefault(user_id, {"balance": 100, "last_bonus": 0})["balance"] = amount
    save_casino()
def add_balance(user_id, amount):
    set_balance(user_id, get_balance(user_id) + amount)

# ─────────────────────── AI Groq ─────────────────────────────────
def init_groq():
    global groq_client, active_model
    if not GROQ_API_KEY:
        logger.warning("⚠️ Groq API ключ не настроен")
        return
    try:
        groq_client = Groq(api_key=GROQ_API_KEY)
        preferred = ["llama-3.3-70b-versatile", "llama-3.1-70b-versatile", "llama-3.1-8b-instant"]
        try:
            available = [m.id for m in groq_client.models.list()]
            active_model = next((m for m in preferred if m in available), available[0] if available else GROQ_MODEL)
        except:
            active_model = GROQ_MODEL
        groq_client.chat.completions.create(model=active_model, messages=[{"role": "user", "content": "test"}], max_tokens=5)
        logger.info(f"✅ Groq AI: {active_model}")
    except Exception as e:
        logger.error(f"❌ Groq ошибка: {e}")
        groq_client = None

def contains_insult(text):
    text_lower = text.lower()
    for word in INSULTS:
        if re.search(r'\b' + re.escape(word) + r'\b', text_lower):
            return True
    for word in insults_library:
        if len(word) > 2 and word in text_lower:
            return True
    return False

async def check_insult_with_groq(text, chat_id):
    if contains_insult(text):
        return True, "🔍 обнаружено оскорбление"
    if not get_setting(chat_id, "ai_moderation") or not groq_client or not active_model:
        return False, ""
    try:
        prompt = f'Ты модератор. Текст: "{text}". Игнорируй маты! Отмечай ТОЛЬКО оскорбления в адрес людей. Если нашел новое оскорбительное слово, укажи его в поле "new_word". Ответь JSON: {{"is_insult": true/false, "reason": "причина", "new_word": "новое оскорбление если есть"}}'
        resp = groq_client.chat.completions.create(model=active_model, messages=[{"role": "user", "content": prompt}], temperature=0.1, max_tokens=100)
        raw = resp.choices[0].message.content
        match = re.search(r'\{[^}]+\}', raw)
        if match:
            data = json.loads(match.group())
            if data.get("is_insult"):
                reason = data.get('reason', 'оскорбление')
                if data.get("new_word"):
                    new_word = data["new_word"].lower().strip()
                    if new_word and len(new_word) > 2:
                        if save_insult_to_library(new_word):
                            reason += f" (📝 слово '{new_word}' добавлено в библиотеку)"
                return True, f"🤖 AI: {reason}"
        return False, ""
    except Exception as e:
        logger.error(f"Groq ошибка: {e}")
        return False, ""

async def get_ai_response(text, user_id, chat_id):
    if not groq_client or not active_model:
        return "❌ AI не доступен"
    key = f"{chat_id}_{user_id}"
    history = conversation_history.get(key, [])[-10:]
    try:
        messages = [{"role": "system", "content": "Ты умный AI-ассистент. Отвечай на русском."}] + history + [{"role": "user", "content": text}]
        resp = groq_client.chat.completions.create(model=active_model, messages=messages, temperature=0.7, max_tokens=1500)
        answer = resp.choices[0].message.content
        conversation_history.setdefault(key, [])
        conversation_history[key] += [{"role": "user", "content": text}, {"role": "assistant", "content": answer}]
        while len(conversation_history[key]) > 20:
            conversation_history[key].pop(0)
        return answer
    except Exception as e:
        return f"❌ Ошибка: {str(e)[:100]}"

# ─────────────────────── КОМАНДЫ КАЗИНО ─────────────────────────
async def cmd_casino(update, context):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("🎰 *КАЗИНО*\n\n`/casino баланс`\n`/casino бонус`\n`/casino <ставка>`\n`/casino куб <ставка> <1-6>`\n💰 Начальный баланс: 100", parse_mode="Markdown")
        return
    command = context.args[0].lower()
    if command in ["баланс", "balance"]:
        await update.message.reply_text(f"💰 Баланс: *{get_balance(user_id)}* монет", parse_mode="Markdown")
    elif command in ["бонус", "bonus"]:
        current_time = int(datetime.now().timestamp())
        user_data = casino_data.get(user_id, {"balance": 100, "last_bonus": 0})
        last_bonus = user_data.get("last_bonus", 0)
        if current_time - last_bonus < 86400:
            hours_left = 24 - ((current_time - last_bonus) // 3600)
            await update.message.reply_text(f"🎁 Бонус через {hours_left} ч.")
            return
        bonus = random.randint(50, 200)
        add_balance(user_id, bonus)
        casino_data[user_id]["last_bonus"] = current_time
        save_casino()
        await update.message.reply_text(f"🎁 Вы получили *{bonus}* монет!\n💰 Баланс: {get_balance(user_id)}", parse_mode="Markdown")
    elif command in ["куб", "dice"]:
        if len(context.args) < 3:
            await update.message.reply_text("❌ /casino куб <ставка> <число 1-6>")
            return
        try:
            bet, guess = int(context.args[1]), int(context.args[2])
        except:
            await update.message.reply_text("❌ Ставка и число должны быть числами")
            return
        if guess < 1 or guess > 6:
            await update.message.reply_text("❌ Число от 1 до 6")
            return
        balance = get_balance(user_id)
        if bet <= 0 or bet > balance:
            await update.message.reply_text(f"❌ Недостаточно монет. Баланс: {balance}")
            return
        result = random.randint(1, 6)
        if result == guess:
            win = bet * 5
            add_balance(user_id, win - bet)
            await update.message.reply_text(f"🎲 Выпало: {result}\n🎉 Вы угадали! Выигрыш: *{win}* монет\n💰 Баланс: {get_balance(user_id)}", parse_mode="Markdown")
        else:
            add_balance(user_id, -bet)
            await update.message.reply_text(f"🎲 Выпало: {result}\n😢 Проигрыш: {bet} монет\n💰 Баланс: {get_balance(user_id)}", parse_mode="Markdown")
    else:
        try:
            bet = int(command)
        except:
            await update.message.reply_text("❌ Неизвестная команда")
            return
        balance = get_balance(user_id)
        if bet <= 0 or bet > balance:
            await update.message.reply_text(f"❌ Недостаточно монет. Баланс: {balance}")
            return
        win = random.choice([True, False])
        if win:
            add_balance(user_id, bet)
            await update.message.reply_text(f"🎰 Вы выиграли! +{bet} монет\n💰 Баланс: {get_balance(user_id)}", parse_mode="Markdown")
        else:
            add_balance(user_id, -bet)
            await update.message.reply_text(f"🎰 Вы проиграли. -{bet} монет\n💰 Баланс: {get_balance(user_id)}", parse_mode="Markdown")

# ─────────────────────── Вспомогательные функции ─────────────────
async def resolve_target(update, context):
    msg = update.message
    if msg.reply_to_message:
        u = msg.reply_to_message.from_user
        return u.id, u.first_name, u.username
    if context.args and context.args[0].startswith("@"):
        username = context.args[0].lstrip("@")
        try:
            member = await context.bot.get_chat_member(update.effective_chat.id, f"@{username}")
            u = member.user
            return u.id, u.first_name, u.username
        except:
            await msg.reply_text(f"❌ Пользователь @{username} не найден.")
            return None, None, None
    await msg.reply_text("↩️ Ответьте на сообщение или укажите @username.\nПример: `/mute @username 30`", parse_mode="Markdown")
    return None, None, None

async def can_moderate(update, target_id, action_name):
    actor_id = update.effective_user.id
    chat_id = update.effective_chat.id
    actor_role = get_role(chat_id, actor_id)
    target_role = get_role(chat_id, target_id)
    if target_role >= 3 or is_owner(target_id):
        await update.message.reply_text(f"❌ Нельзя {action_name} владельца бота!")
        return False
    if target_role >= actor_role:
        await update.message.reply_text(f"❌ Нельзя {action_name} пользователя с ролью {ROLE_NAMES.get(target_role, str(target_role))}")
        return False
    return True

# ─────────────────────── АВТОМОДЕРАЦИЯ ──────────────────────────
async def auto_moderate(update, context):
    msg = update.message
    if not msg or not msg.text or msg.chat.type == "private":
        return
    user = msg.from_user
    if not user:
        return
    chat_id = msg.chat_id
    user_role = get_role(chat_id, user.id)
    if user_role >= 3 or is_owner(user.id):
        return
    if is_user_muted(chat_id, user.id):
        try:
            await msg.delete()
        except:
            pass
        return
    is_insult, reason = await check_insult_with_groq(msg.text, chat_id)
    if not is_insult:
        return
    try:
        await msg.delete()
    except:
        pass
    warn_limit = get_setting(chat_id, "warn_limit")
    mute_dur = get_setting(chat_id, "mute_duration")
    warn_count = add_warn(chat_id, user.id)
    mention = f"[{user.first_name}](tg://user?id={user.id})"
    reason_txt = f" ({reason})" if reason else ""
    if warn_count >= warn_limit:
        await mute_user(context, chat_id, user.id, mute_dur, user.first_name)
        reset_warns(chat_id, user.id)
        await msg.reply_text(f"🔇 {mention} получил мут на {mute_dur} мин. за оскорбления.{reason_txt}", parse_mode="Markdown")
    else:
        remaining = warn_limit - warn_count
        await msg.reply_text(f"⚠️ {mention}, оскорбление!{reason_txt}\nПредупреждение {warn_count}/{warn_limit}. Ещё {remaining} — мут.", parse_mode="Markdown")
    logger.info(f"Оскорбление от {user.id} (роль {user_role}) в {chat_id}: {reason} | варны: {warn_count}")

# ─────────────────────── AI АССИСТЕНТ ────────────────────────────────
async def ai_assistant(update, context):
    msg = update.message
    if not msg or not msg.text:
        return
    chat_id = msg.chat_id
    user_id = msg.from_user.id
    user_role = get_role(chat_id, user_id)
    if user_role < 3 and not is_owner(user_id) and is_user_muted(chat_id, user_id):
        return
    if not get_setting(chat_id, "ai_assistant"):
        return
    respond = False
    if msg.chat.type == "private":
        respond = True
    elif msg.chat.type in ("group", "supergroup"):
        bot_user = context.bot.username
        if f"@{bot_user}" in msg.text:
            respond = True
            msg._unfreeze()
            msg.text = msg.text.replace(f"@{bot_user}", "").strip()
        elif msg.reply_to_message and msg.reply_to_message.from_user.id == context.bot.id:
            respond = True
    if not respond:
        return
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    response = await get_ai_response(msg.text, user_id, chat_id)
    for i in range(0, len(response), 4000):
        await msg.reply_text(response[i:i+4000], parse_mode="Markdown")

# ─────────────────────── КОМАНДЫ БОТА ───────────────────────────
async def cmd_commands(update, context):
    user_role = await effective_role(update, context)
    role_name = ROLE_NAMES.get(user_role, "Участник")
    common = "📋 *ОБЩИЕ КОМАНДЫ:*\n• `/start`\n• `/myrole`\n• `/commands`\n• `/rules`\n• `/casino`\n• `/learned_insults` — показать выученные оскорбления\n\n🚫 *Реагирую ТОЛЬКО на оскорбления! Маты игнорируются.*\n📚 *Самообучаюсь новым оскорблениям!*"
    mod = ""
    if user_role >= 1:
        mod += "\n🤝 *ХЕЛПЕР:*\n`/mute`, `/unmute`, `/warn`, `/unwarn`, `/warns`\n"
    if user_role >= 2:
        mod += "\n🛡 *МОДЕРАТОР:*\n`/ban`, `/kick`\n"
    if user_role >= 3:
        mod += "\n👑 *ВЛАДЕЛЕЦ:*\n`/setrole`, `/roles`, `/settings`, `/set`, `/setrules`\n`/add_insult <слово>` — добавить оскорбление\n`/remove_insult <слово>` — удалить оскорбление\n`/clear_insults` — очистить библиотеку\n"
    await update.message.reply_text(common + mod + f"\n👤 Ваша роль: *{role_name}*", parse_mode="Markdown")

async def cmd_start(update, context):
    role = await effective_role(update, context)
    owner_text = f"\n👑 Владельцы: {', '.join(str(oid) for oid in OWNER_IDS)}" if is_owner(update.effective_user.id) else ""
    await update.message.reply_text(f"👮 *Бот-модератор v3.5 (с самообучением)*\n\nВаша роль: {ROLE_NAMES.get(role, 'Участник')}{owner_text}\n\n🚫 Реагирую ТОЛЬКО на оскорбления!\n📚 Самообучаюсь новым оскорблениям через AI!\n➕ Владельцы могут добавлять оскорбления командой `/add_insult`\n\n`/commands` — список команд", parse_mode="Markdown")

async def cmd_myrole(update, context):
    role = await effective_role(update, context)
    await update.message.reply_text(f"Ваша роль: {ROLE_NAMES.get(role, str(role))}")

async def cmd_learned_insults(update, context):
    if insults_library:
        words = sorted(list(insults_library))
        text = "📚 *Выученные оскорбления:*\n" + ", ".join(f"`{w}`" for w in words[:50])
        if len(words) > 50:
            text += f"\n...и ещё {len(words)-50} слов"
        await update.message.reply_text(text, parse_mode="Markdown")
    else:
        await update.message.reply_text("📚 Пока нет выученных оскорблений. AI будет добавлять их автоматически или используйте `/add_insult`.")

async def cmd_add_insult(update, context):
    if not await require_owner(update, context):
        return
    if not context.args:
        await update.message.reply_text("❌ Использование: `/add_insult <слово>`\nПример: `/add_insult козёл`", parse_mode="Markdown")
        return
    word = " ".join(context.args).lower().strip()
    if len(word) < 2:
        await update.message.reply_text("❌ Слово слишком короткое (минимум 2 символа)")
        return
    if word in INSULTS:
        await update.message.reply_text(f"⚠️ Слово '{word}' уже есть в базовом списке оскорблений")
        return
    if save_insult_to_library(word):
        await update.message.reply_text(f"✅ Слово `{word}` добавлено в библиотеку оскорблений", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ Слово `{word}` уже есть в библиотеке", parse_mode="Markdown")

async def cmd_remove_insult(update, context):
    if not await require_owner(update, context):
        return
    if not context.args:
        await update.message.reply_text("❌ Использование: `/remove_insult <слово>`\nПример: `/remove_insult козёл`", parse_mode="Markdown")
        return
    word = " ".join(context.args).lower().strip()
    if word in INSULTS:
        await update.message.reply_text(f"⚠️ Слово '{word}' находится в базовом списке и не может быть удалено")
        return
    if remove_insult_from_library(word):
        await update.message.reply_text(f"✅ Слово `{word}` удалено из библиотеки оскорблений", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ Слово `{word}` не найдено в библиотеке", parse_mode="Markdown")

async def cmd_clear_insults(update, context):
    if not await require_owner(update, context):
        return
    global insults_library
    count = len(insults_library)
    insults_library.clear()
    with open("insults_library.txt", "w", encoding="utf-8") as f:
        f.write("# Новые оскорбления будут добавляться сюда автоматически\n")
    await update.message.reply_text(f"✅ Библиотека выученных оскорблений очищена! (удалено {count} слов)")

async def cmd_setrole(update, context):
    if not await require_role(update, context, 3):
        return
    if update.message.reply_to_message:
        u = update.message.reply_to_message.from_user
        target_id, target_name = u.id, u.first_name
        role_arg = context.args[0] if context.args else None
    elif len(context.args) >= 2 and context.args[0].startswith("@"):
        username = context.args[0].lstrip("@")
        try:
            member = await context.bot.get_chat_member(update.effective_chat.id, f"@{username}")
            target_id, target_name = member.user.id, member.user.first_name
        except:
            await update.message.reply_text(f"❌ @{username} не найден")
            return
        role_arg = context.args[1]
    else:
        await update.message.reply_text("❌ /setrole @username <0-3> или ответом на сообщение")
        return
    if role_arg is None or not role_arg.isdigit() or int(role_arg) not in range(4):
        await update.message.reply_text("❌ Роль от 0 до 3")
        return
    new_role = int(role_arg)
    my_role = await effective_role(update, context)
    if new_role >= my_role:
        await update.message.reply_text("❌ Нельзя назначить роль >= своей")
        return
    if is_owner(target_id):
        await update.message.reply_text("❌ Нельзя изменить роль владельца!")
        return
    set_role(update.effective_chat.id, target_id, new_role)
    await update.message.reply_text(f"✅ [{target_name}](tg://user?id={target_id}) → {ROLE_NAMES[new_role]}", parse_mode="Markdown")

async def cmd_roles(update, context):
    if not await require_role(update, context, 3):
        return
    chat_roles = roles.get(update.effective_chat.id, {})
    if not chat_roles:
        await update.message.reply_text("📋 Нет назначенных ролей")
        return
    lines = ["📋 *Роли:*\n"]
    for uid, r in sorted(chat_roles.items(), key=lambda x: -x[1]):
        if r > 0:
            owner_mark = " 👑" if is_owner(uid) else ""
            lines.append(f"• `{uid}` — {ROLE_NAMES.get(r, r)}{owner_mark}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_settings(update, context):
    if not await require_role(update, context, 3):
        return
    chat_id = update.effective_chat.id
    await update.message.reply_text(f"⚙️ *Настройки:*\n`mute_duration` = {get_setting(chat_id, 'mute_duration')} мин\n`warn_limit` = {get_setting(chat_id, 'warn_limit')}\n\nИзменить: `/set <параметр> <значение>`", parse_mode="Markdown")

async def cmd_set(update, context):
    if not await require_role(update, context, 3):
        return
    if len(context.args) < 2:
        await update.message.reply_text("❌ /set <параметр> <значение>")
        return
    chat_id = update.effective_chat.id
    key, val = context.args[0].lower(), context.args[1].lower()
    if key == "mute_duration":
        if not val.isdigit() or int(val) < 1:
            await update.message.reply_text("❌ Число минут (минимум 1)")
            return
        set_setting(chat_id, "mute_duration", int(val))
        await update.message.reply_text(f"✅ mute_duration = {val} мин")
    elif key == "warn_limit":
        if not val.isdigit() or int(val) < 1:
            await update.message.reply_text("❌ Число (минимум 1)")
            return
        set_setting(chat_id, "warn_limit", int(val))
        await update.message.reply_text(f"✅ warn_limit = {val}")
    else:
        await update.message.reply_text("❌ Параметр: mute_duration или warn_limit")

async def cmd_mute(update, context):
    if not await require_role(update, context, 1):
        return
    chat_id = update.effective_chat.id
    minutes = get_setting(chat_id, "mute_duration")
    if update.message.reply_to_message:
        if context.args:
            try:
                minutes = int(context.args[0])
            except:
                await update.message.reply_text("⚠️ /mute 60")
                return
        user_id, first_name, _ = await resolve_target(update, context)
    else:
        if len(context.args) >= 2:
            try:
                minutes = int(context.args[1])
            except:
                await update.message.reply_text("⚠️ /mute @username 60")
                return
        user_id, first_name, _ = await resolve_target(update, context)
    if user_id is None:
        return
    if not await can_moderate(update, user_id, "мутить"):
        return
    cancel_unmute_task(chat_id, user_id)
    await mute_user(context, chat_id, user_id, minutes, first_name)
    await update.message.reply_text(f"🔇 [{first_name}](tg://user?id={user_id}) замучен на {minutes} мин.", parse_mode="Markdown")

async def cmd_unmute(update, context):
    if not await require_role(update, context, 1):
        return
    user_id, first_name, _ = await resolve_target(update, context)
    if user_id is None:
        return
    if not await can_moderate(update, user_id, "размутить"):
        return
    cancel_unmute_task(update.effective_chat.id, user_id)
    await do_unmute(context, update.effective_chat.id, user_id, first_name)

async def cmd_warn(update, context):
    if not await require_role(update, context, 1):
        return
    user_id, first_name, _ = await resolve_target(update, context)
    if user_id is None:
        return
    if not await can_moderate(update, user_id, "выдать предупреждение"):
        return
    chat_id = update.effective_chat.id
    warn_limit = get_setting(chat_id, "warn_limit")
    mute_dur = get_setting(chat_id, "mute_duration")
    warn_count = add_warn(chat_id, user_id)
    mention = f"[{first_name}](tg://user?id={user_id})"
    if warn_count >= warn_limit:
        cancel_unmute_task(chat_id, user_id)
        await mute_user(context, chat_id, user_id, mute_dur, first_name)
        reset_warns(chat_id, user_id)
        await update.message.reply_text(f"🔇 {mention} мут на {mute_dur} мин", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"⚠️ {mention} — предупреждение {warn_count}/{warn_limit}", parse_mode="Markdown")

async def cmd_unwarn(update, context):
    if not await require_role(update, context, 1):
        return
    user_id, first_name, _ = await resolve_target(update, context)
    if user_id is None:
        return
    if not await can_moderate(update, user_id, "снять предупреждения"):
        return
    reset_warns(update.effective_chat.id, user_id)
    await update.message.reply_text(f"✅ Предупреждения [{first_name}](tg://user?id={user_id}) сброшены", parse_mode="Markdown")

async def cmd_warns(update, context):
    if not await require_role(update, context, 1):
        return
    user_id, first_name, _ = await resolve_target(update, context)
    if user_id is None:
        return
    if not await can_moderate(update, user_id, "посмотреть предупреждения"):
        return
    chat_id = update.effective_chat.id
    count = get_warn_count(chat_id, user_id)
    limit = get_setting(chat_id, "warn_limit")
    await update.message.reply_text(f"📋 [{first_name}](tg://user?id={user_id}): {count}/{limit}", parse_mode="Markdown")

async def cmd_ban(update, context):
    if not await require_role(update, context, 2):
        return
    user_id, first_name, _ = await resolve_target(update, context)
    if user_id is None:
        return
    if not await can_moderate(update, user_id, "забанить"):
        return
    chat_id = update.effective_chat.id
    cancel_unmute_task(chat_id, user_id)
    remove_muted_user(chat_id, user_id)
    await context.bot.ban_chat_member(chat_id, user_id)
    await update.message.reply_text(f"🚫 [{first_name}](tg://user?id={user_id}) забанен", parse_mode="Markdown")

async def cmd_kick(update, context):
    if not await require_role(update, context, 2):
        return
    user_id, first_name, _ = await resolve_target(update, context)
    if user_id is None:
        return
    if not await can_moderate(update, user_id, "кикнуть"):
        return
    chat_id = update.effective_chat.id
    cancel_unmute_task(chat_id, user_id)
    remove_muted_user(chat_id, user_id)
    await context.bot.ban_chat_member(chat_id, user_id)
    await context.bot.unban_chat_member(chat_id, user_id)
    await update.message.reply_text(f"👢 [{first_name}](tg://user?id={user_id}) кикнут", parse_mode="Markdown")

async def cmd_rules(update, context):
    chat_id = update.effective_chat.id
    try:
        with open(f"rules3_{chat_id}.txt", "r", encoding="utf-8") as f:
            text = f.read()
    except:
        text = "🚫 Правила: запрещены оскорбления в адрес других участников. Маты разрешены."
    await update.message.reply_text(f"📜 *Правила:*\n\n{text}", parse_mode="Markdown")

async def cmd_setrules(update, context):
    if not await require_role(update, context, 3):
        return
    if not context.args:
        await update.message.reply_text("❌ /setrules <текст правил>")
        return
    chat_id = update.effective_chat.id
    text = " ".join(context.args)
    with open(f"rules3_{chat_id}.txt", "w", encoding="utf-8") as f:
        f.write(text)
    await update.message.reply_text("✅ Правила сохранены")

async def cmd_model_info(update, context):
    if groq_client and active_model:
        await update.message.reply_text(f"🤖 Модель: `{active_model}`\n✅ Активна\n🚫 Режим: только оскорбления\n📚 Самообучение: включено\n👑 Владельцев: {len(OWNER_IDS)}", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ AI не подключён")

async def cmd_clear_history(update, context):
    key = f"{update.effective_chat.id}_{update.effective_user.id}"
    conversation_history.pop(key, None)
    await update.message.reply_text("✅ История очищена")

# ─────────────────────── HEALTHCHECK СЕРВЕР ─────────────────────
async def health_check(request):
    return web.Response(text="Bot is alive")

async def run_web():
    app_web = web.Application()
    app_web.router.add_get("/", health_check)
    app_web.router.add_get("/health", health_check)
    runner = web.AppRunner(app_web)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    print("✅ Healthcheck сервер запущен на порту 8080")

# ─────────────────────── MAIN ───────────────────────────────────
async def main_async():
    # Запускаем healthcheck сервер
    await run_web()
    
    # Запускаем бота
    if not BOT_TOKEN or not GROQ_API_KEY:
        print("❌ Ошибка: BOT_TOKEN или GROQ_API_KEY не настроены! Добавьте переменные окружения в Render.")
        return
    load_warnings()
    load_settings()
    load_roles()
    load_casino()
    load_insults_library()
    init_groq()
    print(f"✅ Бот запускается...")
    print(f"🤖 Groq: {'активен (' + active_model + ')' if groq_client else 'не активен'}")
    print(f"👑 Владельцы ID: {OWNER_IDS}")
    print(f"🎰 Казино загружено")
    print(f"📚 Библиотека оскорблений: {len(insults_library)} слов")
    print(f"🚫 РЕЖИМ: только оскорбления, маты игнорируются!")
    print(f"📚 САМООБУЧЕНИЕ: включено!")
    
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("myrole", cmd_myrole))
    app.add_handler(CommandHandler("commands", cmd_commands))
    app.add_handler(CommandHandler("setrole", cmd_setrole))
    app.add_handler(CommandHandler("roles", cmd_roles))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("set", cmd_set))
    app.add_handler(CommandHandler("mute", cmd_mute))
    app.add_handler(CommandHandler("unmute", cmd_unmute))
    app.add_handler(CommandHandler("warn", cmd_warn))
    app.add_handler(CommandHandler("unwarn", cmd_unwarn))
    app.add_handler(CommandHandler("warns", cmd_warns))
    app.add_handler(CommandHandler("ban", cmd_ban))
    app.add_handler(CommandHandler("kick", cmd_kick))
    app.add_handler(CommandHandler("rules", cmd_rules))
    app.add_handler(CommandHandler("setrules", cmd_setrules))
    app.add_handler(CommandHandler("model_info", cmd_model_info))
    app.add_handler(CommandHandler("clear_history", cmd_clear_history))
    app.add_handler(CommandHandler("casino", cmd_casino))
    app.add_handler(CommandHandler("learned_insults", cmd_learned_insults))
    app.add_handler(CommandHandler("add_insult", cmd_add_insult))
    app.add_handler(CommandHandler("remove_insult", cmd_remove_insult))
    app.add_handler(CommandHandler("clear_insults", cmd_clear_insults))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, auto_moderate))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_assistant), group=1)
    
    logger.info("✅ Бот запущен!")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    
    # Держим бота запущенным
    while True:
        await asyncio.sleep(1)

def main():
    asyncio.run(main_async())

if __name__ == "__main__":
    main()