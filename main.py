import sys
import anthropic

AGENT_ID = "agent_011CaD2HTNeNUGZ5Ruh4qCnz"
ENVIRONMENT_ID = "env_01X1MZKN477CYnkffM2d77fM"


def main():
    message = " ".join(sys.argv[1:]) or "Hello! Please introduce yourself."

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

    try:
        with client.beta.sessions.stream(session_id=session.id) as stream:
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

                elif event.type == "session.status_idle":
                    stop_type = getattr(
                        getattr(event, "stop_reason", None), "type", None
                    )
                    if stop_type == "requires_action":
                        continue
                    print("\n\n[done]")
                    break

                elif event.type == "session.status_terminated":
                    print("\n\n[session terminated]")
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
