"""PDF export via a local headless Chrome. Honest 501 if Chrome is not there."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome", "chromium", "chromium-browser",
]


def chrome_path() -> str | None:
    for c in CHROME_CANDIDATES:
        if Path(c).exists():
            return c
        found = shutil.which(c)
        if found:
            return found
    return None


def html_to_pdf(html: str, timeout: float = 60) -> bytes:
    exe = chrome_path()
    if not exe:
        raise RuntimeError("no local Chrome/Chromium found - use the HTML report instead")
    with tempfile.TemporaryDirectory(prefix="ph-pdf-") as tmp:
        src = Path(tmp) / "report.html"
        out = Path(tmp) / "report.pdf"
        src.write_text(html)
        # Chrome writes the PDF and then sits there talking to Google's push
        # service, so we wait for the file rather than for the process.
        cmd = [exe, "--headless=new", "--disable-gpu", "--no-sandbox",
               "--no-first-run", "--disable-extensions", "--disable-sync",
               "--disable-background-networking", "--disable-component-update",
               "--no-pdf-header-footer", f"--print-to-pdf={out}",
               f"--user-data-dir={tmp}/profile", src.as_uri()]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                text=True)
        deadline = time.monotonic() + timeout
        last_size = -1
        try:
            while time.monotonic() < deadline:
                if out.exists():
                    size = out.stat().st_size
                    if size and size == last_size:
                        break
                    last_size = size
                if proc.poll() is not None and out.exists():
                    break
                time.sleep(0.25)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(3)
                except subprocess.TimeoutExpired:
                    proc.kill()
        if not out.exists() or not out.stat().st_size:
            err = (proc.stderr.read() if proc.stderr else "")[-300:]
            raise RuntimeError(f"Chrome did not produce a PDF: {err}")
        return out.read_bytes()
