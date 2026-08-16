"""Quick check: call agents.send_message and print the reply."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents import send_message

if __name__ == "__main__":
    print(send_message("say hello"))
