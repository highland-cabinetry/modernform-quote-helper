"""Quote Helper desktop shortcut target (run with pythonw.exe: no console window)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from desktop_launcher import main  # noqa: E402

sys.exit(main())
