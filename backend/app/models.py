from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Float, ForeignKey, JSON, String, Text, UniqueConstraint
from sqlalchemy import func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from datetime import datetime


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "email"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        Text, ForeignKey("tenants.id"), nullable=False
    )
    email: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="member"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.id"), nullable=False
    )
    tenant_id: Mapped[str] = mapped_column(
        Text, ForeignKey("tenants.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )
    current_url: Mapped[str | None] = mapped_column(Text)
    current_title: Mapped[str | None] = mapped_column(Text)
    device_preset: Mapped[str | None] = mapped_column(
        Text, server_default="desktop-1280x800"
    )
    proxy_url: Mapped[str | None] = mapped_column(Text, server_default="")
    network_egress_id: Mapped[str | None] = mapped_column(Text)
    browser_runtime: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="standard_chrome"
    )
    browser_image_id: Mapped[str | None] = mapped_column(Text)
    tenant_id: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[str | None] = mapped_column(Text)


class AppState(Base):
    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class SessionFile(Base):
    __tablename__ = "session_files"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    session_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True
    )
    tenant_id: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    original_name: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage: Mapped[str] = mapped_column(Text, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(Text)
    source_path: Mapped[str | None] = mapped_column(Text)
    source_mtime: Mapped[float | None] = mapped_column(Float)
    uploaded_at: Mapped[datetime | None] = mapped_column()
    archived_at: Mapped[datetime | None] = mapped_column()
    archived_session_id: Mapped[str | None] = mapped_column(Text)
    archived_session_name: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )


class SessionRuntimeToken(Base):
    __tablename__ = "session_runtime_tokens"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    session_id: Mapped[str] = mapped_column(Text, ForeignKey("sessions.id"), nullable=False)
    tenant_id: Mapped[str | None] = mapped_column(Text)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column()
    revoked_at: Mapped[datetime | None] = mapped_column()
    last_used_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )


class SessionRuntimeStatus(Base):
    __tablename__ = "session_runtime_status"

    session_id: Mapped[str] = mapped_column(Text, ForeignKey("sessions.id"), primary_key=True)
    purpose: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column()
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )


class SessionViewerTicket(Base):
    __tablename__ = "session_viewer_tickets"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    session_id: Mapped[str] = mapped_column(Text, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    tenant_id: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[str | None] = mapped_column(Text)
    operator_subject: Mapped[str] = mapped_column(Text, nullable=False)
    lease_id: Mapped[str | None] = mapped_column(Text, ForeignKey("agent_device_leases.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column()
    remote_addr: Mapped[str | None] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


class AgentDeviceLease(Base):
    __tablename__ = "agent_device_leases"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    device_instance_id: Mapped[str] = mapped_column(
        Text, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    device_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="browser_session")
    lease_mode: Mapped[str] = mapped_column(Text, nullable=False)
    task_id: Mapped[str | None] = mapped_column(Text)
    session_id: Mapped[str] = mapped_column(Text, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    tenant_id: Mapped[str | None] = mapped_column(Text)
    operator_subject: Mapped[str] = mapped_column(Text, nullable=False)
    operator_owner_user_id: Mapped[str | None] = mapped_column(Text)
    current_operator: Mapped[str] = mapped_column(Text, nullable=False)
    authorized_operators: Mapped[list | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    expires_at: Mapped[datetime | None] = mapped_column()
    released_at: Mapped[datetime | None] = mapped_column()
    reclaimed_at: Mapped[datetime | None] = mapped_column()
    invalidated_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


class AgentDeviceAuditEvent(Base):
    __tablename__ = "agent_device_audit_events"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    actor_owner_user_id: Mapped[str | None] = mapped_column(Text)
    device_instance_id: Mapped[str] = mapped_column(Text, nullable=False)
    lease_id: Mapped[str | None] = mapped_column(Text)
    task_id: Mapped[str | None] = mapped_column(Text)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    side_effect_level: Mapped[str] = mapped_column(Text, nullable=False)
    audit_boundary: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_refs: Mapped[list | None] = mapped_column(JSON)
    details: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


class NetworkEgressProfile(Base):
    __tablename__ = "network_egress_profiles"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        Text, ForeignKey("tenants.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="unchecked")
    proxy_url: Mapped[str | None] = mapped_column(Text, server_default="")
    config_ref: Mapped[str | None] = mapped_column(Text, server_default="")
    config_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    health_error: Mapped[str | None] = mapped_column(Text, server_default="")
    last_checked_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )
