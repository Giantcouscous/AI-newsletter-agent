import sys
import os
import smtplib
import anthropic
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import date

AGENT_ID = "agent_011CaD2HTNeNUGZ5Ruh4qCnz"
ENVIRONMENT_ID = "env_01X1MZKN477CYnkffM2d77fM"

def send_email(briefing_text):
    gmail_address = os.environ.get("GMAIL_ADDRESS", "")
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD", "")

    if not gmail_address or not gmail_password:
        print("\n[Email not sent — GMAIL_ADDRESS or GMAIL_APP_PASSWORD not set]")
        return

    msg = MIMEMultipart()
    msg["From"] = gmail_address
    msg["To"] = gmail_address
    msg["Subject"] = f"Your Weekly AI Briefing — {date.today().strftime('%B %d, %Y')}"
    msg.attach(MIMEText(briefing_text, "plain"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(gmail_address, gmail_password)
            server.send_message(msg)
        print("\n[Email sent successfully!]")
    except Exception as e:
        print(f"\n[Email failed: {e}]")

def main():
    gmail_address = os.environ.get("GMAIL_ADDRESS", "")
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD", "")

    user_message = " ".join(sys.argv[1:]) or "Send the weekly AI newsletter."

    if gmail_address and gmail_password:
        message = f"Gmail address: {gmail_address}\nGmail app password: {gmail_password}\n\n{user_message}"
    else:
        message = user_message

    client = anthropic.Anthropic()
    try:
        session = client.beta.sessions.create(
            agent=AGENT_ID,
            environment_id=ENVIRONMENT_ID,
        )
    except anthropic.APIError as e:
        print(f"Error creating session: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Session {session.id} created — streaming...\n")

    full_briefing = []

    try:
        with client.beta.sessions.events.stream(session_id=session.id) as stream:
            client.beta.sessions.events.send(
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
                            print(block.text, end="", flush=True)
                            full_briefing.append(block.text)
                elif event.type == "session.status_idle":
                    stop_type = getattr(
                        getattr(event, "stop_reason", None), "type", None
                    )
                    if stop_type == "requires_action":
                        continue
                    print("\n\n[done]")
                    send_email("".join(full_briefing))
                    break
                elif event.type == "session.status_terminated":
                    print("\n\n[session terminated]")
                    send_email("".join(full_briefing))
                    break
                elif event.type == "session.error":
                    print(f"\nSession error: {event}", file=sys.stderr)
                    sys.exit(1)
    except anthropic.APIError as e:
        print(f"\nAPI error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n[interrupted]")
        sys.exit(0)

if __name__ == "__main__":
    main()
