import asyncio
import logging
import time
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

# --- НАСТРОЙКИ ---
TOKEN = "8541983198:AAH6gcsqQ0OowEzcubyqNkMMMN0ibsR01rc"
ADRES = -1003769555171  # ID группы модерации
KANAL = -1003575509267  # ID канала
LIMIT_POSTS = 5
LIMIT_WINDOW = 300
# -----------------

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Хранилища
user_states = {}
user_history = {}
active_moderation = {}
# Множество для предотвращения одновременной обработки одного и того же сообщения
processing_now = set()


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    text = (
        "📋 **Меню управления:**\n\n"
        "🔹 /send — Предложить новый пост\n"
        "🔹 /delete — Отменить последний предложенный пост\n"
        "🔹 /info — Посмотреть правила и лимиты\n\n"
        "Нажмите на нужную команду выше 👆"
    )
    await message.answer(text, parse_mode="Markdown")


@dp.message(Command("info"))
async def cmd_info(message: types.Message):
    rules = (
        "ℹ️ **Информация:**\n\n"
        f"• Лимит: **{LIMIT_POSTS} постов** за **{LIMIT_WINDOW // 60} минут**.\n"
        "• После отправки ваш пост попадает в очередь модерации.\n"
        "• Если модератор нажмет «Да», пост сразу выйдет в канале."
    )
    await message.answer(rules, parse_mode="Markdown")


@dp.message(Command("send"))
async def cmd_send(message: types.Message):
    user_id = message.from_user.id
    current_time = time.time()

    if user_id not in user_history:
        user_history[user_id] = []

    user_history[user_id] = [t for t in user_history[user_id] if current_time - t < LIMIT_WINDOW]

    if len(user_history[user_id]) >= LIMIT_POSTS:
        wait_time = int(LIMIT_WINDOW - (current_time - user_history[user_id][0]))
        return await message.answer(f"⏳ Лимит! Вы сможете отправить пост через {wait_time} сек.")

    user_states[user_id] = "waiting"
    await message.answer("📥 Пришлите контент (текст, фото или видео) для публикации.")


@dp.message(Command("delete"))
async def cmd_delete(message: types.Message):
    user_id = message.from_user.id
    if user_id in active_moderation and active_moderation[user_id]:
        ids = active_moderation[user_id].pop()
        try:
            await bot.delete_message(chat_id=ADRES, message_id=ids["content"])
            await bot.delete_message(chat_id=ADRES, message_id=ids["buttons"])
            return await message.answer("🗑 Ваш последний пост удален из очереди модерации.")
        except Exception as e:
            logging.error(f"Ошибка при удалении через /delete: {e}")
    await message.answer("❌ У вас нет активных постов на модерации.")


@dp.message(F.chat.type == "private")
async def handle_message(message: types.Message):
    user_id = message.from_user.id

    if user_states.get(user_id) == "waiting":
        user_history[user_id].append(time.time())
        sent_content = await message.copy_to(chat_id=ADRES)

        builder = InlineKeyboardBuilder()
        builder.add(types.InlineKeyboardButton(
            text="Да ✅",
            callback_data=f"p:y:{user_id}:{sent_content.message_id}:{message.message_id}")
        )
        builder.add(types.InlineKeyboardButton(
            text="Нет ❌",
            callback_data=f"p:n:{user_id}:{sent_content.message_id}:{message.message_id}")
        )

        sent_buttons = await bot.send_message(
            chat_id=ADRES,
            text=f"📩 **Новое предложение от пользователя**",
            reply_to_message_id=sent_content.message_id,
            reply_markup=builder.as_markup(),
            parse_mode="Markdown"
        )

        if user_id not in active_moderation:
            active_moderation[user_id] = []
        active_moderation[user_id].append({
            "content": sent_content.message_id,
            "buttons": sent_buttons.message_id
        })

        user_states[user_id] = None
        await message.answer("✅ Принято! Отправлено на модерацию.")
    elif not (message.text and message.text.startswith('/')):
        await message.answer("⚠️ Чтобы отправить пост, используйте /send.")


@dp.callback_query(F.data.startswith("p:"))
async def decision_handler(callback: types.CallbackQuery):
    data_parts = callback.data.split(":")
    if len(data_parts) < 5:
        return await callback.answer("Ошибка данных.")

    _, action, user_id, content_id, user_msg_id = data_parts
    user_id, content_id, user_msg_id = int(user_id), int(content_id), int(user_msg_id)

    # 1. Проверяем блокировку на время обработки
    if content_id in processing_now:
        return await callback.answer("Этот пост уже обрабатывается...", show_alert=False)

    # 2. Проверяем, активен ли пост
    is_active = False
    if user_id in active_moderation:
        for item in active_moderation[user_id]:
            if item["content"] == content_id:
                is_active = True
                break

    if not is_active:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except:
            pass
        return await callback.answer("Решение уже было принято!", show_alert=True)

    # Ставим блокировку
    processing_now.add(content_id)

    try:
        # Убираем кнопки СРАЗУ, чтобы никто больше не нажал
        await callback.message.edit_reply_markup(reply_markup=None)

        # Удаляем из активных СРАЗУ
        active_moderation[user_id] = [i for i in active_moderation[user_id] if i["content"] != content_id]

        mod_link = f"[{callback.from_user.full_name}](tg://user?id={callback.from_user.id})"

        if action == "y":
            verdict, res_text = "✅ Одобрено", "🌟 Ваш пост был одобрен и опубликован!"
            await bot.copy_message(chat_id=KANAL, from_chat_id=ADRES, message_id=content_id)
        else:
            verdict, res_text = "❌ Отклонено", "❌ Ваш пост был отклонен модератором."

        # Отчет в группу
        await bot.send_message(
            chat_id=ADRES,
            text=f"Вердикт: {verdict}\nМодератор: {mod_link}",
            reply_to_message_id=content_id,
            parse_mode="Markdown"
        )

        # Уведомление юзеру
        try:
            await bot.send_message(chat_id=user_id, text=res_text, reply_to_message_id=user_msg_id)
        except:
            await bot.send_message(chat_id=user_id, text=res_text)

    except Exception as e:
        logging.error(f"Ошибка: {e}")
        await callback.answer("Произошла ошибка при обработке.")
    finally:
        # Снимаем блокировку
        if content_id in processing_now:
            processing_now.remove(content_id)
        await callback.answer()


async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())