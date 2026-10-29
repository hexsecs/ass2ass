"""Run an isolated two-agent exchange without package installation."""
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/ass2ass/scripts"))
from ass2ass import DirectorySurface, Message

with tempfile.TemporaryDirectory(prefix="ass2ass-demo-") as root:
    channel = DirectorySurface(root)
    request = Message.create("planner", "builder", {"task": "add", "values": [17, 25]}, kind="request")
    channel.publish(request)
    incoming = channel.receive("builder")[0]
    reply = incoming.reply("builder", {"result": sum(incoming.data["payload"]["values"])})
    channel.publish(reply)
    channel.acknowledge("builder", incoming.id)
    result = channel.receive("planner")[0]
    print(result.to_json())
    channel.acknowledge("planner", result.id)
    assert channel.receive("builder") == channel.receive("planner") == []
