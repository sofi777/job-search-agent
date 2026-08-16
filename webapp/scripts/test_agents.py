"""Quick check: call agents.send_message and print the reply."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents import send_message

if __name__ == "__main__":
    reply, used_model = send_message("say hello")
    print(f"[{used_model}] {reply}")
