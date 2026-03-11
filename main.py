from __future__ import annotations

import sys
from pathlib import Path

from codelens.__main__ import main

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))



if __name__ == "__main__":
    main()
