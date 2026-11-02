import pytest
from cryptography.fernet import Fernet

from scripts.setup_env import ensure_env


@pytest.fixture
def repo(tmp_path):
    (tmp_path / ".env.example").write_text("PLAID_SECRET=\nFERNET_KEY=\nPLAID_ENV=sandbox\n")
    return tmp_path


def key_in(env) -> str:
    line = next(ln for ln in env.read_text().splitlines() if ln.startswith("FERNET_KEY="))
    return line.split("=", 1)[1]


def test_fresh_clone_gets_env_with_a_working_key(repo):
    ensure_env(repo)
    env = repo / ".env"
    Fernet(key_in(env).encode())     # raises if it isn't a real key
    assert "PLAID_ENV=sandbox" in env.read_text()


def test_blank_key_in_existing_env_is_filled_and_the_rest_kept(repo):
    (repo / ".env").write_text("PLAID_SECRET=mine\nFERNET_KEY=\n")
    ensure_env(repo)
    Fernet(key_in(repo / ".env").encode())
    assert "PLAID_SECRET=mine" in (repo / ".env").read_text()


def test_missing_key_line_is_added(repo):
    (repo / ".env").write_text("PLAID_SECRET=mine")
    ensure_env(repo)
    assert "PLAID_SECRET=mine\n" in (repo / ".env").read_text()
    Fernet(key_in(repo / ".env").encode())


# replacing a key would make every stored plaid token unreadable
def test_existing_key_is_never_replaced(repo):
    original = "PLAID_SECRET=mine\nFERNET_KEY=abc123\n"
    (repo / ".env").write_text(original)
    ensure_env(repo)
    assert (repo / ".env").read_text() == original


def test_running_twice_keeps_the_generated_key(repo):
    ensure_env(repo)
    first = key_in(repo / ".env")
    ensure_env(repo)
    assert key_in(repo / ".env") == first


def test_the_key_is_never_printed(repo, capsys):
    ensure_env(repo)
    out = capsys.readouterr().out
    assert key_in(repo / ".env") not in out


@pytest.mark.parametrize("prefix", ["export ", "  ", "\t"])
def test_valid_dotenv_key_formats_keep_existing_ciphertext_readable(repo, prefix):
    from dotenv import dotenv_values
    key = Fernet.generate_key()
    token = Fernet(key).encrypt(b"dummy-token")
    original = prefix + "FERNET_KEY=" + key.decode() + "\n"
    (repo / ".env").write_text(original)
    ensure_env(repo)
    assert (repo / ".env").read_text() == original
    effective = dotenv_values(repo / ".env")["FERNET_KEY"]
    assert Fernet(effective.encode()).decrypt(token) == b"dummy-token"


@pytest.mark.parametrize("assignment", ["export FERNET_KEY=", "  FERNET_KEY=", "FERNET_KEY='' # blank"])
def test_blank_dotenv_formats_get_one_effective_working_key(repo, assignment):
    from dotenv import dotenv_values
    (repo / ".env").write_text(assignment + "\n")
    ensure_env(repo)
    key = dotenv_values(repo / ".env")["FERNET_KEY"]
    Fernet(key.encode())
    ensure_env(repo)
    assert dotenv_values(repo / ".env")["FERNET_KEY"] == key
