import sys
from pathlib import Path
# Ensure repository root is on sys.path so 'backend' can be imported reliably in Vercel
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))
from backend.app import app as app
