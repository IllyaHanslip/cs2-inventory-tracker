"""The pre-upload privacy check. Test values are assembled at runtime so that this
file never contains anything the checker would flag."""
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_secrets.py"
spec = importlib.util.spec_from_file_location("check_secrets", SCRIPT)
check = importlib.util.module_from_spec(spec)
sys.modules["check_secrets"] = check
spec.loader.exec_module(check)

REAL_LOOKING_ID = "7656119" + "8" + "123456789"
FAKE_TEST_ID = "7656119" + "0000000001"


def kinds(text, words=None):
    return [f.kind for f in check.scan_text("file.txt", text, words)]


@pytest.mark.parametrize("path, blocked", [
    (".env", True), (".env.local", True), ("config/.env", True), (".env.example", False),
    ("data/cs2_tracker.db", True), ("backup.sqlite3", True), (".venv/pyvenv.cfg", True),
    ("export.csv", True), ("CS2.lnk", True), (".private-words", True), ("app.py", False),
])
def test_private_files_are_blocked(path, blocked):
    assert (check.check_path(path) is not None) == blocked


def test_api_keys_are_found():
    key_name = "CSFLOAT" + "_API_KEY"
    assert "API key in a config line" in kinds(f"{key_name}=abcdEFGH1234ijkl")
    assert kinds(f"{key_name}=") == []                       # the empty template line is fine
    assert "possible Steam Web API key" in kinds("key = " + "0123456789ABCDEF" * 2)


def test_steam_ids():
    assert kinds(f"id: {REAL_LOOKING_ID}") == ["SteamID64"]
    assert kinds(f"id: {FAKE_TEST_ID}") == []                 # below the range Steam assigns
    assert kinds("steam://rungame/730/" + "76561202255233023" + "/+csgo_econ_action_preview") == []


def test_emails_paths_and_tokens():
    assert kinds("mail me: someone" + "@" + "gmail.com") == ["e-mail address"]
    assert kinds("contact: someone" + "@" + "example.com") == []
    assert kinds(r"C:" + "\\Users\\" + "realperson\\Documents") == ["personal Windows path"]
    assert kinds(r"C:" + "\\Users\\" + "you\\Documents") == []
    assert kinds("/home/" + "realperson/project") == ["personal Mac/Linux path"]
    assert kinds("token ghp_" + "a" * 36) == ["GitHub token"]
    assert kinds("cookie " + check.STEAM_SESSION_COOKIE + "=x") == ["Steam login cookie"]


def test_private_words_and_masking():
    found = check.scan_text("notes.md", "my steam name is SecretGamer42", ["secretgamer42"])
    assert [f.kind for f in found] == ["one of your private words"]
    assert "SecretGamer42".lower() not in str(found[0]).lower()   # never printed in full


def test_this_repository_is_clean():
    """Fails if anything private would currently be uploaded."""
    findings = check.run(quiet=True)
    assert findings == [], "\n".join(map(str, findings))
