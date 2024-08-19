import os
import re
import smtplib
import logging
import asyncio
import fcntl
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ConversationHandler, CallbackContext
from dotenv import load_dotenv
from aiohttp import web

# Загрузка переменных окружения из .env файла
load_dotenv()

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Conversation stages
EMAIL, SUBJECT, NAME, ATTACHMENT = range(4)

def ensure_single_instance():
    """Ensure that only one instance of the script is running."""
    global lock_file
    lock_file = open("/tmp/sendemail_bot.lock", "w")
    try:
        fcntl.lockf(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        print("Another instance is already running. Exiting.")
        sys.exit(1)

# Function to send an email
def send_email(subject, body, to_email, attachment=None):
    try:
        from_email = os.getenv("EMAIL_ADDRESS")
        password = os.getenv("EMAIL_PASSWORD")

        msg = MIMEMultipart()
        msg['From'] = from_email
        msg['To'] = to_email
        msg['Subject'] = subject

        msg.attach(MIMEText(body, 'plain'))

        # If there is an attachment
        if attachment:
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(attachment['data'])
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f"attachment; filename= {attachment['filename']}")
            msg.attach(part)

        # Sending the email
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(from_email, password)
            server.sendmail(from_email, to_email, msg.as_string())
        logging.info(f"Email successfully sent to {to_email}")

    except Exception as e:
        logging.error(f"Failed to send email to {to_email}: {e}")
        raise

# Start conversation
async def start(update: Update, context: CallbackContext) -> int:
    logging.info(f"Received /start command from {update.effective_user.id}")
    await update.message.reply_text("Please enter the recipient's email address:")
    return EMAIL

async def get_email(update: Update, context: CallbackContext) -> int:
    email = update.message.text
    # Проверка на правильность формата email
    if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
        await update.message.reply_text("Invalid email format. Please enter a valid email address:")
        return EMAIL
    
    context.user_data['email'] = email
    logging.info(f"Email received: {email}")
    await update.message.reply_text("Enter the subject of the email:")
    return SUBJECT

async def get_subject(update: Update, context: CallbackContext) -> int:
    context.user_data['subject'] = update.message.text
    logging.info(f"Subject received: {update.message.text}")
    await update.message.reply_text("Enter the recipient's name:")
    return NAME

async def get_name(update: Update, context: CallbackContext) -> int:
    context.user_data['name'] = update.message.text
    logging.info(f"Name received: {update.message.text}")
    await update.message.reply_text("Please send an attachment (e.g., CV file) or type 'no' to proceed without an attachment:")
    return ATTACHMENT

async def get_attachment(update: Update, context: CallbackContext) -> int:
    attachment = None
    try:
        if update.message.document:
            file = await update.message.document.get_file()
            file_data = await file.download_as_bytearray()
            attachment = {'filename': update.message.document.file_name, 'data': file_data}
            logging.info(f"Document attachment received: {update.message.document.file_name}")
        elif update.message.photo:
            # Get the largest photo (last in the list)
            photo = update.message.photo[-1]
            file = await photo.get_file()
            file_data = await file.download_as_bytearray()
            attachment = {'filename': f"photo_{file.file_id}.jpg", 'data': file_data}
            logging.info(f"Photo attachment received: {attachment['filename']}")
        elif update.message.text.lower() == 'no':
            logging.info("No attachment")
        else:
            await update.message.reply_text("Invalid input. Please send a file, photo, or type 'no'.")
            return ATTACHMENT

        context.user_data['attachment'] = attachment
        await send_mail(update, context)
        await update.message.reply_text("Email successfully sent!")
    except Exception as e:
        logging.error(f"Error while processing the attachment: {e}")
        await update.message.reply_text("Failed to send email due to an error.")
    return ConversationHandler.END

async def send_mail(update: Update, context: CallbackContext) -> None:
    try:
        email = context.user_data['email']
        subject = context.user_data['subject']
        name = context.user_data['name']
        attachment = context.user_data.get('attachment', None)

        body = f"Good afternoon {name},\n\nPlease find attached CV file.\n\nBest regards,\n\nMaksym Anheliuk"

        send_email(subject, body, email, attachment)
    except Exception as e:
        logging.error(f"Error while sending email: {e}")
        await update.message.reply_text("Failed to send email due to an error.")

async def cancel(update: Update, context: CallbackContext) -> int:
    logging.info("Operation canceled by user")
    await update.message.reply_text("Operation canceled.")
    return ConversationHandler.END

async def timeout_handler(update: Update, context: CallbackContext) -> int:
    logging.info("Conversation timed out")
    await update.message.reply_text("Timeout reached. Operation canceled due to inactivity. Please start again with /start.")
    return ConversationHandler.END

async def handle(request):
    return web.Response(text="Bot is running!")

async def init_app():
    app = web.Application()
    app.router.add_get('/', handle)
    return app

async def main() -> None:
    ensure_single_instance()
    
    await asyncio.sleep(5)  # Подождать 5 секунд перед запуском

    # Create and run the Telegram bot application
    application = Application.builder().token(os.getenv("TELEGRAM_TOKEN")).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_email)],
            SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_subject)],
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            ATTACHMENT: [MessageHandler(filters.Document.ALL | filters.PHOTO | filters.TEXT & ~filters.COMMAND, get_attachment)]
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        conversation_timeout=300  # Тайм-аут 5 минут (300 секунд)
    )

    application.add_handler(conv_handler)

    # Инициализация и запуск бота
    await application.initialize()
    await application.start()
    await application.updater.start_polling()

    # Запуск веб-сервера
    app = await init_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', int(os.getenv('PORT', 5000)))
    await site.start()

    # Держим приложение запущенным
    await application.run_polling()

if __name__ == '__main__':
    asyncio.run(main())
