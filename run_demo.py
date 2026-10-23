"""Start LogHarbor with sample data in a throwaway database (for trying it out and taking screenshots)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from logharbor.cli import main  # noqa: E402

db = os.path.join(tempfile.gettempdir(), "logharbor-demo.sqlite")
for suffix in ("", "-wal", "-shm"):
    try:
        os.remove(db + suffix)
    except OSError:
        pass
port = os.environ.get("PORT") or (sys.argv[1] if len(sys.argv) > 1 else "4399")  # preview tools may assign one
sys.exit(main(["serve", "--port", port, "--demo", "--db", db]))
