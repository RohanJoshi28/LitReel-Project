from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy

# Global SQLAlchemy instance so models can share it without circular imports.
db = SQLAlchemy()
login_manager = LoginManager()
login_manager.session_protection = "strong"
