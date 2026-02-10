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
user_history = {}
active_moderation = {}  # Format: {user_id: [message_id_1, message_id_2]}
# Множество для предотвращения одновременной обработки одного и того же сообщения
processing_now = set()


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    text = (
        "👋 Привет! **Отправьте пост** (текст, фото или видео) прямо сюда, "
        "и он, возможно, будет опубликован в канале."
    )
    await message.answer(text, parse_mode="Markdown")


@dp.message(Command("delete"))
async def cmd_delete(message: types.Message):
    user_id = message.from_user.id
    if user_id in active_moderation and active_moderation[user_id]:
        # Удаляем последний отправленный ID из списка
        msg_id = active_moderation[user_id].pop()
        try:
            await bot.delete_message(chat_id=ADRES, message_id=msg_id)
            return await message.answer("🗑 Ваш последний пост удален из очереди модерации.")
        except Exception as e:
            logging.error(f"Ошибка при удалении через /delete: {e}")
    await message.answer("❌ У вас нет активных постов на модерации.")


@dp.message(F.chat.type == "private")
async def handle_message(message: types.Message):
    # Игнорируем команды
    if message.text and message.text.startswith('/'):
        return

    user_id = message.from_user.id
    current_time = time.time()

    # --- Проверка лимитов ---
    if user_id not in user_history:
        user_history[user_id] = []

    # Очистка старых записей
    user_history[user_id] = [t for t in user_history[user_id] if current_time - t < LIMIT_WINDOW]

    if len(user_history[user_id]) >= LIMIT_POSTS:
        wait_time = int(LIMIT_WINDOW - (current_time - user_history[user_id][0]))
        return await message.answer(f"⏳ Лимит! Вы сможете отправить пост через {wait_time} сек.")
    # ------------------------

    # Фиксируем время отправки
    user_history[user_id].append(time.time())

    # Создаем кнопки
    builder = InlineKeyboardBuilder()
    # Callback format: p:action:user_id:original_msg_id
    # ID сообщения в админке мы узнаем из самого callback'а, поэтому в data его не пишем
    builder.add(types.InlineKeyboardButton(
        text="Одобрить ✅",
        callback_data=f"p:y:{user_id}:{message.message_id}")
    )
    builder.add(types.InlineKeyboardButton(
        text="Отклонить ❌",
        callback_data=f"p:n:{user_id}:{message.message_id}")
    )

    # Отправляем копию сообщения СРАЗУ с кнопками (одним сообщением)
    sent_content = await message.copy_to(
        chat_id=ADRES,
        reply_markup=builder.as_markup()
    )

    if user_id not in active_moderation:
        active_moderation[user_id] = []
    # Сохраняем только ID этого сообщения
    active_moderation[user_id].append(sent_content.message_id)

    await message.answer(
        "✅ Ваш пост отправлен на модерацию.\n\n"
        "Для удаления последнего предложенного поста используйте команду /delete"
    )


@dp.callback_query(F.data.startswith("p:"))
async def decision_handler(callback: types.CallbackQuery):
    data_parts = callback.data.split(":")
    if len(data_parts) < 4:
        return await callback.answer("Ошибка данных.")

    # Формат: p : action : user_id : user_msg_id
    _, action, user_id, user_msg_id = data_parts
    user_id, user_msg_id = int(user_id), int(user_msg_id)
    
    # ID сообщения в чате модерации - это сообщение, к которому прикреплена кнопка
    content_id = callback.message.message_id

    # 1. Проверяем блокировку на время обработки
    if content_id in processing_now:
        return await callback.answer("Этот пост уже обрабатывается...", show_alert=False)

    # 2. Проверяем, активен ли пост (есть ли он в списке пользователя)
    is_active = False
    if user_id in active_moderation:
        if content_id in active_moderation[user_id]:
            is_active = True

    if not is_active:
        try:
            # Если пост не активен, но кнопки остались - удаляем их
            await callback.message.edit_reply_markup(reply_markup=None)
        except:
            pass
        return await callback.answer("Решение уже было принято!", show_alert=True)

    # Ставим блокировку
    processing_now.add(content_id)

    try:
        # Убираем кнопки с поста
        await callback.message.edit_reply_markup(reply_markup=None)

        # Удаляем из активных
        if user_id in active_moderation:
            active_moderation[user_id] = [mid for mid in active_moderation[user_id] if mid != content_id]

        mod_link = f"[{callback.from_user.full_name}](tg://user?id={callback.from_user.id})"

        if action == "y":
            verdict, res_text = "✅ Одобрено", "🌟 Ваш пост был одобрен и опубликован!"
            # Публикуем в канал (копируем то самое сообщение из админки)
            await bot.copy_message(chat_id=KANAL, from_chat_id=ADRES, message_id=content_id)
        else:
            verdict, res_text = "❌ Отклонено", "❌ Ваш пост был отклонен модератором."

        # Отправляем отчет в группу (реплаем на пост)
        await bot.send_message(
            chat_id=ADRES,
            text=f"Вердикт: {verdict}\nМодератор: {mod_link}",
            reply_to_message_id=content_id,
            parse_mode="Markdown"
        )

        # Уведомление пользователю
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
