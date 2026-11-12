#!/usr/bin/env python3
"""Privacy check - run before uploading to GitHub.

Finds API keys, real SteamIDs, e-mail addresses, personal file paths and private
files (your .env, database, exports) among the files that would be uploaded.

    python scripts/check_secrets.py            # every file git would upload
    python scripts/check_secrets.py --staged   # only what is staged (used by the pre-commit hook)

Exit code 0 means clean; 1 means something needs your attention. Matches are
always shown masked, so the check itself never prints a secret in full.

Optional: list your own private words (Steam nickname, real SteamID, real name...)
one per line in a file called ".private-words" in the project folder. That file is
git-ignored and only read by this check.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_WORDS_FILE = ".private-words"
# Lowest SteamID64 Steam assigns to an individual account (universe 1, type 1, instance 1).
FIRST_REAL_STEAMID64 = 0x0110000100000000
# Steam's fixed "rungame" id that appears inside every CS2 inspect link - not a person.
ALLOWED_STEAM_IDS = {"76561202255233023"}
ALLOWED_EMAIL_DOMAINS = ("example.com", "example.org", "example.net", "example.invalid", "noreply.github.com")
ALLOWED_PATH_USERS = {"you", "yourname", "username", "user", "name", "example", "public", "default"}
SKIP_DIRS = {".git", ".venv", "venv", "env", "data", "__pycache__", ".pytest_cache", "node_modules"}
MAX_BYTES = 2_000_000

FORBIDDEN_FILES = [
    (re.compile(r"(^|/)\.env(\.(?!example$)[^/]+)?$"), "an environment file - it holds your API keys"),
    (re.compile(r"\.(db|sqlite|sqlite3|db-wal|db-shm)$"), "a database - it holds your inventory and settings"),
    (re.compile(r"(^|/)data/"), "the local data folder - your inventory and price history"),
    (re.compile(r"(^|/)\.?venv/"), "a Python environment - it contains paths from your PC"),
    (re.compile(r"(^|/)\.private-words$"), "your private-words list"),
    (re.compile(r"\.lnk$"), "a Windows shortcut - it contains paths from your PC"),
    (re.compile(r"\.csv$"), "an exported spreadsheet - it may list your inventory"),
    (re.compile(r"(\.pem|\.key|(^|/)id_rsa[^/]*)$"), "a private key file"),
]

STEAM_SESSION_COOKIE = "steamLogin" + "Secure"  # split so this file doesn't flag itself
PATTERNS = [
    ("API key in a config line",
     re.compile(r"\b(?:CSFLOAT_API_KEY|STEAM_API_KEY)\s*[=:]\s*['\"]?([A-Za-z0-9_\-]{12,})")),
    ("possible Steam Web API key", re.compile(r"\b([0-9A-F]{32})\b")),
    ("SteamID64", re.compile(r"\b(7656119\d{10})\b")),
    ("e-mail address", re.compile(r"\b([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")),
    ("personal Windows path", re.compile(r"(?i)\b[A-Z]:[\\/]+(?:Users|Documents and Settings)[\\/]+([^\\/\s\"'<>%$]+)")),
    ("personal Mac/Linux path", re.compile(r"(?<![\w.])/(?:Users|home)/([^/\s\"'<>$]+)")),
    ("private key", re.compile(r"(-----BEGIN [A-Z ]*PRIVATE KEY-----)")),
    ("GitHub token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})\b")),
    ("Steam login cookie", re.compile("(" + STEAM_SESSION_COOKIE + ")")),
]


@dataclass
class Finding:
    path: str
    line: int | None
    kind: str
    sample: str

    def __str__(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"  [!!] {where}  {self.kind}: {self.sample}"


def mask(value: str) -> str:
    value = value.strip()
    if len(value) <= 6:
        return "*" * len(value)
    return f"{value[:3]}{'*' * min(len(value) - 5, 12)}{value[-2:]}  ({len(value)} chars)"


def _allowed(kind: str, value: str) -> bool:
    if kind == "SteamID64":
        return value in ALLOWED_STEAM_IDS or int(value) < FIRST_REAL_STEAMID64
    if kind == "e-mail address":
        domain = value.rsplit("@", 1)[-1].lower()
        return any(domain == d or domain.endswith("." + d) for d in ALLOWED_EMAIL_DOMAINS)
    if kind.startswith("personal") and "path" in kind:
        return value.lower().strip("<>") in ALLOWED_PATH_USERS
    return False


def check_path(path: str) -> Finding | None:
    normalised = path.replace("\\", "/")
    for pattern, why in FORBIDDEN_FILES:
        if pattern.search(normalised):
            return Finding(path, None, "must not be uploaded", why)
    return None


def scan_text(path: str, text: str, private_words: list[str] | None = None) -> list[Finding]:
    findings = []
    words = [w.lower() for w in (private_words or []) if len(w) >= 3]
    for number, line in enumerate(text.splitlines(), start=1):
        for kind, pattern in PATTERNS:
            for match in pattern.finditer(line):
                value = match.group(1)
                if not _allowed(kind, value):
                    findings.append(Finding(path, number, kind, mask(value)))
        lower = line.lower()
        for word in words:
            if word in lower:
                findings.append(Finding(path, number, "one of your private words", mask(word)))
    return findings


# ---------------------------------------------------------------------------
# Which files would be uploaded?
# ---------------------------------------------------------------------------
def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def _split(output: str) -> list[str]:
    return sorted({p for p in output.split("\0") if p})


def files_to_upload() -> tuple[list[str], str]:
    try:
        if _git("rev-parse", "--is-inside-work-tree").strip() == "true":
            return _split(_git("ls-files", "-z", "--cached", "--others", "--exclude-standard")), "git"
    except (OSError, subprocess.CalledProcessError):
        pass
    try:  # not a repository yet: ask git what `git add` would pick up, via a throwaway index
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", "--bare", tmp], capture_output=True, check=True)
            out = _git(f"--git-dir={tmp}", f"--work-tree={ROOT}", "ls-files", "-z", "--others", "--exclude-standard")
            return _split(out), "git (repository not created yet)"
    except (OSError, subprocess.CalledProcessError):
        pass
    files = []  # git not installed: walk the folder, skipping the usual private folders
    for path in ROOT.rglob("*"):
        rel = path.relative_to(ROOT)
        if path.is_file() and not SKIP_DIRS.intersection(rel.parts[:-1]):
            files.append(rel.as_posix())
    return sorted(files), "folder walk (git not found - .gitignore not applied)"


def staged_files() -> list[str]:
    return _split(_git("diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR"))


def read(path: str, staged: bool) -> bytes:
    if staged:
        return subprocess.run(["git", "show", f":{path}"], cwd=ROOT, capture_output=True, check=True).stdout
    return (ROOT / path).read_bytes()


def load_private_words() -> list[str]:
    file = ROOT / PRIVATE_WORDS_FILE
    if not file.exists():
        return []
    lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
    return [w.strip() for w in lines if w.strip() and not w.lstrip().startswith("#")]


def run(staged: bool = False, quiet: bool = False) -> list[Finding]:
    if staged:
        files, source = staged_files(), "staged for commit"
    else:
        files, source = files_to_upload()
    words = load_private_words()
    findings: list[Finding] = []
    for path in files:
        problem = check_path(path)
        if problem:
            findings.append(problem)
            continue
        try:
            data = read(path, staged)
        except (OSError, subprocess.CalledProcessError):
            continue
        if len(data) > MAX_BYTES or b"\0" in data[:8000]:
            continue  # binary (e.g. the icon) or very large
        findings.extend(scan_text(path, data.decode("utf-8", errors="replace"), words))

    if not quiet:
        print(f"Privacy check - {len(files)} files ({source})"
              f"{f', plus {len(words)} private words' if words else ''}")
        if findings:
            print(*findings, sep="\n")
            print("\n  Fix these before uploading. If a real key was ever committed or pushed, deleting it")
            print("  is not enough - revoke it on the website that issued it and create a new one.")
        else:
            print("  [OK] No API keys, real SteamIDs, e-mail addresses, personal paths or private files found.")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--staged", action="store_true", help="check only files staged for commit")
    args = parser.parse_args()
    return 1 if run(staged=args.staged) else 0


if __name__ == "__main__":
    sys.exit(main())
