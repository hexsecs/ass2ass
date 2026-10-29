"""Small JSON-in / JSON-out CLI for agent tool calls and shell scripts."""
import argparse
import json
import sys

from . import DirectorySurface, Message, ProtocolError


def main(argv=None):
    parser = argparse.ArgumentParser(description="Agentic Signaling System to Any Shared Surface")
    parser.add_argument("--surface", required=True, help="shared directory")
    commands = parser.add_subparsers(dest="command", required=True)
    send = commands.add_parser("send")
    send.add_argument("--from", dest="sender", required=True)
    send.add_argument("--to", dest="recipient", required=True)
    send.add_argument("--kind", default="event")
    send.add_argument("--payload", required=True, help="JSON object")
    send.add_argument("--conversation-id")
    send.add_argument("--correlation-id")
    send.add_argument("--expires-at")
    receive = commands.add_parser("receive")
    receive.add_argument("--as", dest="recipient", required=True)
    receive.add_argument("--limit", type=int, default=100)
    ack = commands.add_parser("ack")
    ack.add_argument("--as", dest="recipient", required=True)
    ack.add_argument("message_id")
    args = parser.parse_args(argv)
    try:
        surface = DirectorySurface(args.surface)
        if args.command == "send":
            message = Message.create(args.sender, args.recipient, json.loads(args.payload),
                                     kind=args.kind, conversation_id=args.conversation_id,
                                     correlation_id=args.correlation_id, expires_at=args.expires_at)
            print(json.dumps({"id": surface.publish(message)}))
        elif args.command == "receive":
            for message in surface.receive(args.recipient, limit=args.limit):
                print(message.to_json())
        else:
            surface.acknowledge(args.recipient, args.message_id)
            print(json.dumps({"acknowledged": args.message_id}))
        return 0
    except (ProtocolError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
