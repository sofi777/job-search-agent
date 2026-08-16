"""Print the full prompt + token usage of the most recent OpenRouter call (see src/agents.py _log_last_call)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents import LAST_CALL_LOG_PATH

if __name__ == "__main__":
    if not LAST_CALL_LOG_PATH.exists():
        print(f"No calls logged yet at {LAST_CALL_LOG_PATH}")
        sys.exit(1)

    data = json.loads(LAST_CALL_LOG_PATH.read_text())
    print(f"Model: {data['model']}\n")
    for m in data["messages"]:
        print(f"--- {m['role']} ---\n{m['content']}\n")
    print(f"--- reply ---\n{data['reply_content']}\n")
    print(f"Usage: {data['usage']}")
