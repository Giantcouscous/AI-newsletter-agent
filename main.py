import sys
import os
import anthropic
import requests
from datetime import date

AGENT_ID = "agent_011CaD2HTNeNUGZ5Ruh4qCnz"
ENVIRONMENT_ID = "env_01X1MZKN477CYnkffM2d77fM"
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = "5858773467"

def save_briefing_to_gist(briefing_text):
    token = os.environ.get("GIST_TOKEN", "")
    if not token:
        print("\n[Gist not saved — GIST_TOKEN not set]")
        return

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }

    response = requests.get("https://api.github.com/gists", headers=headers)
    gists = response.json()
    existing_gist = None
    for gist in gists:
        if "weekly_ai_briefing.txt" in gist["files"]:
            existing_gist = gist["id"]
            break

    data = {
        "description": "Latest Weekly AI Briefing",
        "public": False,
        "files": {
            "weekly_ai_briefing.txt": {
                "content": briefing_text
            }
        }
    }

    if existing_gist:
        requests.patch(f"https://api.github.com/gists/{existing_gist}", headers=headers, json=data)
        print("\n[Briefing updated in Gist]")
    else:
        requests.post("https://api.github.com/gists", headers=headers, json=data)
        print("\n[Briefing saved to new Gist]")

def send_telegram(briefing_text):
    if not TELEGRAM_BOT_TOKEN:
        print("\n[Telegram not sent — TELEGRAM_BOT_TOKEN not set]")
        return

    header = f"📰 Your Weekly AI Briefing — {date.today().strftime('%B %d, %Y')}\n\n"
    full_message = header + briefing_text

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    # Telegram limit is 4096 chars — split if needed
    if len(full_message) <= 4096:
        chunks = [full_message]
    else:
        chunks = [header + "Briefing is long — sending in parts:"]
        parts = [briefing_text[i:i+4000] for i in range(0, len(briefing_text), 4000)]
        for i, part in enumerate(parts, 1):
            chunks.append(f"Part {i}:\n\n{part}")

    for chunk in chunks:
        try:
            response = requests.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": chunk
            })
            if response.status_code == 200:
                print("\n[Telegram message sent successfully!]")
            else:
                print(f"\n[Telegram failed: {response.text}]")
        except Exception as e:
            print(f"\n[Telegram error: {e}]")

def main():
    user_message = " ".join(sys.argv[1:]) or "Send the weekly AI newsletter."

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
                        "content": [{"type": "text", "text": user_message}],
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
                    briefing = "".join(full_briefing)
                    send_telegram(briefing)
                    save_briefing_to_gist(briefing)
                    break
                elif event.type == "session.status_terminated":
                    print("\n\n[session terminated]")
                    briefing = "".join(full_briefing)
                    send_telegram(briefing)
                    save_briefing_to_gist(briefing)
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
