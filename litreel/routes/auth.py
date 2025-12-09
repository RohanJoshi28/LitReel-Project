from __future__ import annotations

from flask import Blueprint, jsonify, request, session
from flask_login import current_user, login_required, login_user, logout_user

from ..extensions import db
from ..models import User


auth_bp = Blueprint("auth", __name__)

MIN_PASSWORD_LENGTH = 8


def _normalize_email(value: str | None) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def _validate_credentials(email: str, password: str) -> tuple[bool, str | None]:
    if not email or "@" not in email:
        return False, "A valid email is required."
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        return False, f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    return True, None


@auth_bp.route("/signup", methods=["POST"])
def signup():
    payload = request.get_json(silent=True) or {}
    email = _normalize_email(payload.get("email"))
    password = (payload.get("password") or "").strip()

    ok, message = _validate_credentials(email, password)
    if not ok:
        return jsonify({"error": message}), 400

    existing = User.query.filter_by(email=email).first()
    if existing:
        return jsonify({"error": "An account with that email already exists."}), 409

    user = User(email=email)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    session.clear()
    login_user(user)
    return jsonify({"user": user.to_dict()}), 201


@auth_bp.route("/login", methods=["POST"])
def login():
    payload = request.get_json(silent=True) or {}
    email = _normalize_email(payload.get("email"))
    password = (payload.get("password") or "").strip()

    ok, message = _validate_credentials(email, password)
    if not ok:
        return jsonify({"error": message}), 400

    user = User.query.filter_by(email=email).first()
    if not user or not user.check_password(password):
        return jsonify({"error": "Invalid email or password."}), 401

    session.clear()
    login_user(user)
    return jsonify({"user": user.to_dict()})


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    session.clear()
    return jsonify({"status": "ok"})


@auth_bp.route("/me", methods=["GET"])
def current_session():
    if not current_user.is_authenticated:
        return jsonify({"authenticated": False}), 401
    return jsonify({"authenticated": True, "user": current_user.to_dict()})


__all__ = ["auth_bp"]
