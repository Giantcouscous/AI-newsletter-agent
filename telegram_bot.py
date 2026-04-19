import os
import asyncio
import anthropic
import openai
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import date
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

AGENT_ID = os.environ.get("SUBSTACK_AGENT_ID", "")
ENVIRONMENT_ID = "env_01X1MZKN477CYnkffM2d77fM"
ALLOWED_USER_ID = int(os.environ.get("TELEGRAM_USER_ID", "0"))

def send_email(draft_text):
    gmail_address = os.environ.get("GMAIL_ADDRESS", "")
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD", "")

    if not gmail_address or not gmail_password:
        print("\n[Email not sent — credentials not set]")
        return

    msg = MIMEMultipart()
    msg["From"] = gmail_address
    msg["To"] = gmail_address
    msg["Subject"] = f"Your Substack Draft — {date.today().strftime('%B %d, %Y')}"
    msg.attach(MIMEText(draft_text, "plain"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(gmail_address, gmail_password)
            server.send_message(msg)
        print("\n[Email sent successfully!]")
    except Exception as e:
        print(f"\n[Email failed: {e}]")

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    if user_id != ALLOWED_USER_ID:
        await update.message.reply_text("Sorry, I don't recognise you.")
        return

    await update.message.reply_text("Got your voice note! Transcribing now...")

    voice = update.message.voice
    file = await context.bot.get_file(voice.file_id)
    file_path = "/tmp/voice_note.ogg"
    await file.download_to_drive(file_path)

    openai_client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

    with open(file_path, "rb") as audio_file:
        transcription = openai_client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
        )
    voice_text = transcription.text
    await update.message.reply_text(f"Transcribed: {voice_text}\n\nNow writing your Substack draft...")

    latest_briefing = context.bot_data.get("latest_briefing", "No briefing available yet.")

    message = f"WEEKLY BRIEFING:\n{latest_briefing}\n\nAUTHOR'S NOTE:\n{voice_text}"

    anthropic_client = anthropic.Anthropic()

    try:
        session = anthropic_client.beta.sessions.create(
            agent=AGENT_ID,
            environment_id=ENVIRONMENT_ID,
        )
    except anthropic.APIError as e:
        await update.message.reply_text(f"Error creating session: {e}")
        return

    full_draft = []

    try:
        with anthropic_client.beta.sessions.events.stream(session_id=session.id) as stream:
            anthropic_client.beta.sessions.events.send(
                session_id=session.id,
                events=[
                    {
                        "type": "user.message",
                        "content": [{"type": "text", "text": message}],
                    }
                ],
            )
            for event in stream:
                if event.type == "agent.message":
                    for block in event.content:
                        if block.type == "text":
                            full_draft.append(block.text)
                elif event.type == "session.status_idle":
                    stop_type = getattr(
                        getattr(event, "stop_reason", None), "type", None
                    )
                    if stop_type == "requires_action":
                        continue
                    break
                elif event.type == "session.status_terminated":
                    break
    except anthropic.APIError as e:
        await update.message.reply_text(f"API error: {e}")
        return

    draft_text = "".join(full_draft)
    send_email(draft_text)
    await update.message.reply_text("Done! Your Substack draft has been emailed to you.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    if user_id != ALLOWED_USER_ID:
        await update.message.reply_text("Sorry, I don't recognise you.")
        return

    text = update.message.text.lower()
    if text == "/start":
        await update.message.reply_text("Hi! Send me a voice note and I'll write your Substack draft.")
    else:
        await update.message.reply_text("Send me a voice note to get started!")

def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    app = Application.builder().token(token).build()
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT, handle_text))
    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
