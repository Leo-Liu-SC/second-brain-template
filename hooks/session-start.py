"""
SessionStart hook - injects knowledge base index into new sessions.

This allows Claude to access your accumulated knowledge.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX_FILE = ROOT / "wiki" / "INDEX.md"

def main():
    if not INDEX_FILE.exists():
        sys.exit(0)

    index_content = INDEX_FILE.read_text(encoding="utf-8")

    print(f"""## Your Second Brain Index

You have access to a personal knowledge base with the following topics:

{index_content}

When the user asks about any of these topics, refer to your knowledge base.
""")

if __name__ == "__main__":
    main()
