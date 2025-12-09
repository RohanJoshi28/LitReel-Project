from datetime import datetime
import json

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    projects = db.relationship(
        "Project",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password, method="pbkdf2:sha256")

    def check_password(self, password: str) -> bool:
        if not password:
            return False
        return check_password_hash(self.password_hash, password)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Project(db.Model):
    __tablename__ = "projects"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    title = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(50), default="draft", nullable=False)
    active_concept_id = db.Column(db.Integer, nullable=True)
    voice = db.Column(db.String(50), nullable=False, default="sarah")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    supabase_book_id = db.Column(db.String(64), nullable=True)

    user = db.relationship("User", back_populates="projects")

    concepts = db.relationship(
        "Concept",
        backref="project",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="Concept.order_index",
    )
    render_artifacts = db.relationship(
        "RenderArtifact",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="RenderArtifact.created_at.desc()",
    )


class Concept(db.Model):
    __tablename__ = "concepts"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=False)
    order_index = db.Column(db.Integer, nullable=False, default=0)

    slides = db.relationship(
        "Slide",
        backref="concept",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="Slide.order_index",
    )
    render_artifacts = db.relationship(
        "RenderArtifact",
        back_populates="concept",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="RenderArtifact.created_at.desc()",
    )


class Slide(db.Model):
    __tablename__ = "slides"

    id = db.Column(db.Integer, primary_key=True)
    concept_id = db.Column(db.Integer, db.ForeignKey("concepts.id"), nullable=False)
    order_index = db.Column(db.Integer, nullable=False, default=0)
    text = db.Column(db.Text, nullable=False)
    image_url = db.Column(db.Text, nullable=True)
    effect = db.Column(db.String(50), nullable=False, default="none")
    transition = db.Column(db.String(50), nullable=False, default="fade")
    style = db.relationship(
        "SlideStyle",
        backref="slide",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="joined",
    )

    @property
    def style_dict(self) -> dict:
        if self.style:
            return self.style.to_dict()
        return SlideStyle.default_dict()


class SlideStyle(db.Model):
    __tablename__ = "slide_styles"

    id = db.Column(db.Integer, primary_key=True)
    slide_id = db.Column(db.Integer, db.ForeignKey("slides.id"), nullable=False, unique=True)
    text_color = db.Column(db.String(32), nullable=False, default="#FFFFFF")
    outline_color = db.Column(db.String(32), nullable=False, default="#000000")
    font_weight = db.Column(db.String(8), nullable=False, default="700")
    underline = db.Column(db.Boolean, nullable=False, default=False)

    @staticmethod
    def default_dict() -> dict:
        return {
            "text_color": "#FFFFFF",
            "outline_color": "#000000",
            "font_weight": "700",
            "underline": False,
        }

    def to_dict(self) -> dict:
        base = self.default_dict()
        if self.text_color:
            base["text_color"] = self.text_color.upper()
        if self.outline_color:
            base["outline_color"] = self.outline_color.upper()
        if self.font_weight:
            base["font_weight"] = self.font_weight
        base["underline"] = bool(self.underline)
        return base


class RenderArtifact(db.Model):
    __tablename__ = "render_artifacts"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(
        db.Integer,
        db.ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    concept_id = db.Column(
        db.Integer,
        db.ForeignKey("concepts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    job_id = db.Column(db.String(64), unique=True, nullable=False, index=True)
    status = db.Column(db.String(32), nullable=False, default="queued")
    voice = db.Column(db.String(50), nullable=True)
    download_type = db.Column(db.String(16), nullable=True)
    download_url = db.Column(db.Text, nullable=True)
    storage_path = db.Column(db.Text, nullable=True)
    file_size = db.Column(db.BigInteger, nullable=True)
    suggested_filename = db.Column(db.String(255), nullable=True)
    render_signature = db.Column(db.String(128), nullable=True)
    cache_hit = db.Column(db.Boolean, default=False, nullable=False)
    error = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )
    completed_at = db.Column(db.DateTime, nullable=True)
    download_count = db.Column(db.Integer, default=0, nullable=False)

    project = db.relationship("Project", back_populates="render_artifacts")
    concept = db.relationship("Concept", back_populates="render_artifacts")
    user = db.relationship("User")

    def to_job_payload(self) -> dict:
        payload = {
            "job_id": self.job_id,
            "project_id": self.project_id,
            "concept_id": self.concept_id,
            "status": self.status,
            "voice": self.voice,
            "download_type": self.download_type,
            "download_url": self.download_url,
            "storage_path": self.storage_path,
            "file_size": self.file_size,
            "suggested_filename": self.suggested_filename,
            "render_signature": self.render_signature,
            "cache_hit": self.cache_hit,
            "error": self.error,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "requested_by": self.user_id,
            "user_id": self.user_id,
        }
        return payload


class Book(db.Model):
    __tablename__ = "book"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    chunks = db.relationship(
        "BookChunk",
        back_populates="book",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class BookChunk(db.Model):
    __tablename__ = "book_chunk"

    id = db.Column(db.Integer, primary_key=True)
    book_id = db.Column(db.Integer, db.ForeignKey("book.id", ondelete="CASCADE"), nullable=False, index=True)
    content = db.Column(db.Text, nullable=False)
    embedding = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    book = db.relationship("Book", back_populates="chunks")

    def embedding_vector(self) -> list[float]:
        try:
            data = json.loads(self.embedding)
        except (TypeError, ValueError):
            return []
        if isinstance(data, list):
            return [float(x) for x in data if isinstance(x, (int, float))]
        return []
