from pathlib import Path

from flask import Flask

from litreel import _ensure_secret_key


def _build_app(tmp_path, *, testing=False, secret="dev-secret-key"):
    app = Flask(__name__, instance_path=str(tmp_path))
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    app.config["SECRET_KEY"] = secret
    app.config["TESTING"] = testing
    return app


def test_ensure_secret_key_generates_random_secret(tmp_path):
    app = _build_app(tmp_path, testing=False, secret="dev-secret-key")
    _ensure_secret_key(app)
    secret_path = Path(app.instance_path) / "secret.key"
    assert app.config["SECRET_KEY"] != "dev-secret-key"
    assert secret_path.exists()
    assert secret_path.read_text().strip() == app.config["SECRET_KEY"]


def test_ensure_secret_key_uses_test_value_in_testing(tmp_path):
    app = _build_app(tmp_path, testing=True, secret="dev-secret-key")
    _ensure_secret_key(app)
    assert app.config["SECRET_KEY"] == "test-secret-key"
    secret_path = Path(app.instance_path) / "secret.key"
    assert not secret_path.exists()
