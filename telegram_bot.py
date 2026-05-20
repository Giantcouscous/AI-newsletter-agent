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

COLLECTING_VOICE = 1
WAITING_FOR_ANSWERS = 2

def get_latest_briefing():
    token = os.environ.get("GIST_TOKEN", "")
    if not token:
        return "No briefing available yet."

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }

    try:
        response = requests.get("https://api.github.com/gists", headers=headers)
        gists = response.json()

        if not isinstance(gists, list):
            print(f"Unexpected Gist response: {gists}")
            return "No briefing available yet."

        for gist in gists:
            if not isinstance(gist, dict):
                continue
            if "weekly_ai_briefing.txt" in gist.get("files", {}):
                gist_id = gist["id"]
                detail = requests.get(f"https://api.github.com/gists/{gist_id}", headers=headers)
                content = detail.json()["files"]["weekly_ai_briefing.txt"]["content"]
                return content

    except Exception as e:
        print(f"Error fetching briefing: {e}")

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

async def generate_draft(all_voice_text, user_answers, briefing):
    message = (
        f"WEEKLY BRIEFING:\n{briefing}\n\n"
        f"AUTHOR'S VOICE NOTES:\n{all_voice_text}\n\n"
        f"AUTHOR'S ANSWERS TO YOUR QUESTIONS:\n{user_answers}\n\n"
        f"Now write the full Substack draft."
    )

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

async def ask_questions(all_voice_text, briefing):
    question_prompt = (
        f"WEEKLY BRIEFING:\n{briefing}\n\n"
        f"AUTHOR'S VOICE NOTES:\n{all_voice_text}\n\n"
        f"Before writing the draft, ask the author 2-3 short clarifying questions "
        f"that will help you write a more personalised and specific newsletter post. "
        f"Ask them as a numbered list, nothing else."
    )

    anthropic_client = anthropic.Anthropic()

    try:
        session = anthropic_client.beta.sessions.create(
            agent=AGENT_ID,
            environment_id=ENVIRONMENT_ID,
        )
    except anthropic.APIError as e:
        return f"Error creating session: {e}"

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

    return "".join(questions)

async def handle_voice_collecting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    if user_id != ALLOWED_USER_ID:
        await update.message.reply_text("Sorry, I don't recognise you.")
        return ConversationHandler.END

    await update.message.reply_text("Got your voice note! Transcribing now...")

    voice_text = await transcribe_voice(update, context)

    existing = context.user_data.get("all_voice_text", "")
    context.user_data["all_voice_text"] = existing + "\n\n" + voice_text if existing else voice_text

    await update.message.reply_text(
        f"Transcribed:\n\n{voice_text}\n\n"
        f"Send another voice note to keep adding, or say 'go' when you're ready."
    )

    return COLLECTING_VOICE

async def handle_go(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    if user_id != ALLOWED_USER_ID:
        await update.message.reply_text("Sorry, I don't recognise you.")
        return ConversationHandler.END

    text = update.message.text.lower().strip()

    if text != "go":
        await update.message.reply_text("Send a voice note to add more, or say 'go' when ready.")
        return COLLECTING_VOICE

    all_voice_text = context.user_data.get("all_voice_text", "")
    if not all_voice_text:
        await update.message.reply_text("I don't have any voice notes yet. Send one first!")
        return COLLECTING_VOICE

    await update.message.reply_text("Fetching this week's briefing and preparing questions...")

    briefing = get_latest_briefing()
    context.user_data["briefing"] = briefing

    questions_text = await ask_questions(all_voice_text, briefing)
    context.user_data["questions"] = questions_text

    await update.message.reply_text(
        f"{questions_text}\n\nReply with your answers — text or voice note both work!"
    )

    return WAITING_FOR_ANSWERS

async def handle_answers_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    if user_id != ALLOWED_USER_ID:
        await update.message.reply_text("Sorry, I don't recognise you.")
        return ConversationHandler.END

    user_answers = update.message.text
    all_voice_text = context.user_data.get("all_voice_text", "")
    briefing = context.user_data.get("briefing", "No briefing available yet.")

    await update.message.reply_text("Got your answers! Writing your Substack draft now — this may take a few minutes...")

    draft_text = await generate_draft(all_voice_text, user_answers, briefing)
    send_email(draft_text)

    await update.message.reply_text("Done! Your Substack draft has been emailed to you.")
    context.user_data.clear()

    return ConversationHandler.END

async def handle_answers_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    if user_id != ALLOWED_USER_ID:
        await update.message.reply_text("Sorry, I don't recognise you.")
        return ConversationHandler.END

    await update.message.reply_text("Got your voice reply! Transcribing now...")

    user_answers = await transcribe_voice(update, context)
    await update.message.reply_text(f"Transcribed: {user_answers}\n\nWriting your Substack draft now — this may take a few minutes...")

    all_voice_text = context.user_data.get("all_voice_text", "")
    briefing = context.user_data.get("briefing", "No briefing available yet.")

    draft_text = await generate_draft(all_voice_text, user_answers, briefing)
    send_email(draft_text)

    await update.message.reply_text("Done! Your Substack draft has been emailed to you.")
    context.user_data.clear()

    return ConversationHandler.END

async def handle_text_idle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    if user_id != ALLOWED_USER_ID:
        await update.message.reply_text("Sorry, I don't recognise you.")
        return

    text = update.message.text.lower()
    if text == "/start":
        await update.message.reply_text(
            "Hi! Send me a voice note to get started.\n"
            "You can send as many as you like, then say 'go' when you're ready."
        )
    else:
        await update.message.reply_text(
            "Send me a voice note to get started, or say 'go' when you're ready!"
        )

def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    app = Application.builder().token(token).build()

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.VOICE, handle_voice_collecting)],
        states={
            COLLECTING_VOICE: [
                MessageHandler(filters.VOICE, handle_voice_collecting),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_go),
            ],
            WAITING_FOR_ANSWERS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_answers_text),
                MessageHandler(filters.VOICE, handle_answers_voice),
            ],
        },
        fallbacks=[MessageHandler(filters.COMMAND, handle_text_idle)],
    )

    app.add_handler(conv_handler)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_idle))
    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
