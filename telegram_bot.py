import os
import anthropic
import openai
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import date
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes, ConversationHandler

AGENT_ID = os.environ.get("SUBSTACK_AGENT_ID", "")
ENVIRONMENT_ID = "env_01X1MZKN477CYnkffM2d77fM"
ALLOWED_USER_ID = int(os.environ.get("TELEGRAM_USER_ID", "0"))

WAITING_FOR_ANSWERS = 1

def get_latest_briefing():
    token = os.environ.get("GIST_TOKEN", "")
    if not token:
        return "No briefing available yet."

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }

    response = requests.get("https://api.github.com/gists", headers=headers)
    gists = response.json()

    for gist in gists:
        if "weekly_ai_briefing.txt" in gist["files"]:
            gist_id = gist["id"]
            detail = requests.get(f"https://api.github.com/gists/{gist_id}", headers=headers)
            content = detail.json()["files"]["weekly_ai_briefing.txt"]["content"]
            return content

    return "No briefing available yet."

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

async def transcribe_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    openai_client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
    voice = update.message.voice
    file = await context.bot.get_file(voice.file_id)
    file_path = "/tmp/voice_note.ogg"
    await file.download_to_drive(file_path)

    with open(file_path, "rb") as audio_file:
        transcription = openai_client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
        )
    return transcription.text

async def generate_draft(voice_text, user_answers, briefing):
    message = f"WEEKLY BRIEFING:\n{briefing}\n\nAUTHOR'S VOICE NOTE:\n{voice_text}\n\nAUTHOR'S ANSWERS TO YOUR QUESTIONS:\n{user_answers}\n\nNow write the full Substack draft."

    anthropic_client = anthropic.Anthropic()

    try:
        session = anthropic_client.beta.sessions.create(
            agent=AGENT_ID,
            environment_id=ENVIRONMENT_ID,
        )
    except anthropic.APIError as e:
        return f"Error creating session: {e}"

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
        return f"API error: {e}"

    return "".join(full_draft)

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    if user_id != ALLOWED_USER_ID:
        await update.message.reply_text("Sorry, I don't recognise you.")
        return ConversationHandler.END

    await update.message.reply_text("Got your voice note! Transcribing now...")

    voice_text = await transcribe_voice(update, context)
    context.user_data["voice_text"] = voice_text
    context.user_data["briefing"] = get_latest_briefing()

    await update.message.reply_text(f"Transcribed: {voice_text}\n\nFetching this week's briefing and thinking of questions...")

    briefing = context.user_data["briefing"]
    question_prompt = f"WEEKLY BRIEFING:\n{briefing}\n\nAUTHOR'S VOICE NOTE:\n{voice_text}\n\nBefore writing the draft, ask the author 2-3 short clarifying questions that will help you write a more personalised and specific newsletter post. Ask them as a numbered list, nothing else."

    anthropic_client = anthropic.Anthropic()

    try:
        session = anthropic_client.beta.sessions.create(
            agent=AGENT_ID,
            environment_id=ENVIRONMENT_ID,
        )
    except anthropic.APIError as e:
        await update.message.reply_text(f"Error: {e}")
        return ConversationHandler.END

    questions = []

    with anthropic_client.beta.sessions.events.stream(session_id=session.id) as stream:
        anthropic_client.beta.sessions.events.send(
            session_id=session.id,
            events=[
                {
                    "type": "user.message",
                    "content": [{"type": "text", "text": question_prompt}],
                }
            ],
        )
        for event in stream:
            if event.type == "agent.message":
                for block in event.content:
                    if block.type == "text":
                        questions.append(block.text)
            elif event.type == "session.status_idle":
                stop_type = getattr(
                    getattr(event, "stop_reason", None), "type", None
                )
                if stop_type == "requires_action":
                    continue
                break
            elif event.type == "session.status_terminated":
                break

    questions_text = "".join(questions)
    context.user_data["questions"] = questions_text

    await update.message.reply_text(f"{questions_text}\n\nReply with your answers — text or voice note both work!")

    return WAITING_FOR_ANSWERS

async def handle_answers_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    if user_id != ALLOWED_USER_ID:
        await update.message.reply_text("Sorry, I don't recognise you.")
        return ConversationHandler.END

    user_answers = update.message.text
    voice_text = context.user_data.get("voice_text", "")
    briefing = context.user_data.get("briefing", "No briefing available yet.")

    await update.message.reply_text("Got your answers! Writing your Substack draft now — this may take a few minutes...")

    draft_text = await generate_draft(voice_text, user_answers, briefing)
    send_email(draft_text)

    await update.message.reply_text("Done! Your Substack draft has been emailed to you.")

    return ConversationHandler.END

async def handle_answers_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    if user_id != ALLOWED_USER_ID:
        await update.message.reply_text("Sorry, I don't recognise you.")
        return ConversationHandler.END

    await update.message.reply_text("Got your voice reply! Transcribing now...")

    user_answers = await transcribe_voice(update, context)
    await update.message.reply_text(f"Transcribed: {user_answers}\n\nWriting your Substack draft now — this may take a few minutes...")

    voice_text = context.user_data.get("voice_text", "")
    briefing = context.user_data.get("briefing", "No briefing available yet.")

    draft_text = await generate_draft(voice_text, user_answers, briefing)
    send_email(draft_text)

    await update.message.reply_text("Done! Your Substack draft has been emailed to you.")

    return ConversationHandler.END

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

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.VOICE, handle_voice)],
        states={
            WAITING_FOR_ANSWERS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_answers_text),
                MessageHandler(filters.VOICE, handle_answers_voice),
            ],
        },
        fallbacks=[MessageHandler(filters.COMMAND, handle_text)],
    )

    app.add_handler(conv_handler)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
