"""Render the Mermaid diagrams in docs/ARCHITECTURE.md to PNG files in docs/diagrams/.

Run from anywhere:  python scripts/render_diagrams.py

Each diagram is a ```mermaid block preceded by a marker line such as <!-- diagram: deployment -->,
which gives the PNG its name (docs/diagrams/deployment.png). Needs Node.js (for npx). The
mermaid-cli package is fetched on first use and drives an installed Chrome or Edge, so
Puppeteer does not download a browser of its own.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "docs" / "ARCHITECTURE.md"
OUT_DIR = REPO / "docs" / "diagrams"
MERMAID_CLI = "@mermaid-js/mermaid-cli@11"
BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
]
BLOCK = re.compile(r"<!-- diagram: (\S+) -->\s*```mermaid\n(.*?)```", re.DOTALL)


def main():
    npx = shutil.which("npx")
    if npx is None:
        sys.exit("npx not found: install Node.js, or view the diagrams on GitHub (it renders Mermaid).")
    diagrams = BLOCK.findall(SOURCE.read_text(encoding="utf-8"))
    if not diagrams:
        sys.exit(f"no '<!-- diagram: name -->' blocks found in {SOURCE}")

    env = dict(os.environ, PUPPETEER_SKIP_DOWNLOAD="1")
    browser = next((b for b in BROWSERS if Path(b).exists()), None)
    OUT_DIR.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "puppeteer.json"
        config.write_text(json.dumps({"executablePath": browser} if browser else {}))
        for name, code in diagrams:
            src = Path(tmp) / f"{name}.mmd"
            src.write_text(code, encoding="utf-8")
            out = OUT_DIR / f"{name}.png"
            print(f"rendering {out.relative_to(REPO)}")
            subprocess.run([npx, "-y", MERMAID_CLI, "-i", str(src), "-o", str(out),
                            "-b", "white", "-s", "2", "-p", str(config)], check=True, env=env)


if __name__ == "__main__":
    main()
