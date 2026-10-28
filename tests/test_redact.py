from possessed_shell.redact import MASK, redact, redact_obj


def test_key_value_secrets():
    assert redact("export GITHUB_TOKEN=abc123def") == f"export GITHUB_TOKEN={MASK}"
    assert redact("DB_PASSWORD='hunter2' ./run") == f"DB_PASSWORD={MASK} ./run"


def test_cli_flag_secrets():
    assert redact("deploy --api-key sk_live_123 --verbose") == f"deploy --api-key {MASK} --verbose"
    assert "hunter2" not in redact("mysql -u root -phunter2 db")


def test_tokens_and_urls():
    assert "ghp_" not in redact("ghp_" + "a" * 36)
    assert "AKIA" not in redact("aws key AKIAABCDEFGHIJKLMNOP")
    assert redact("git clone https://bob:s3cret@github.com/x/y") == f"git clone https://bob:{MASK}@github.com/x/y"
    assert MASK in redact("curl -H 'Authorization: Bearer abcdefghijklmnop'")


def test_ordinary_errors_survive():
    msg = "fatal: Authentication failed for 'https://github.com/x/y'"
    assert redact(msg) == msg
    assert redact("ls: cannot access '/nope': No such file or directory").endswith("No such file or directory")


def test_redact_obj_recurses():
    event = {"a": ["TOKEN=xyz"], "b": {"c": "password: hunter2"}, "n": 3}
    out = redact_obj(event)
    assert out == {"a": [f"TOKEN={MASK}"], "b": {"c": f"password: {MASK}"}, "n": 3}
