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


class PlatformUser(Base):
    __tablename__ = "platform_users"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column()


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    default_active_session_limit: Mapped[int] = mapped_column(nullable=False)
    default_runtime_class_limits: Mapped[dict | None] = mapped_column(JSON)
    default_max_session_seconds: Mapped[int] = mapped_column(nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


class TenantPlatformSettings(Base):
    __tablename__ = "tenant_platform_settings"

    tenant_id: Mapped[str] = mapped_column(Text, ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    runtime_namespace: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    runtime_image_policy: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_by_platform_user_id: Mapped[str | None] = mapped_column(Text, ForeignKey("platform_users.id", ondelete="SET NULL"))
    suspended_at: Mapped[datetime | None] = mapped_column()
    suspended_by: Mapped[str | None] = mapped_column(Text, ForeignKey("platform_users.id", ondelete="SET NULL"))
    suspend_reason: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = mapped_column()
    deleted_by: Mapped[str | None] = mapped_column(Text, ForeignKey("platform_users.id", ondelete="SET NULL"))
    delete_reason: Mapped[str | None] = mapped_column(Text)
    retention_until: Mapped[datetime | None] = mapped_column()
    purge_requested_at: Mapped[datetime | None] = mapped_column()
    purge_requested_by: Mapped[str | None] = mapped_column(Text, ForeignKey("platform_users.id", ondelete="SET NULL"))
    purge_request_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


class TenantRuntimeQuota(Base):
    __tablename__ = "tenant_runtime_quotas"

    tenant_id: Mapped[str] = mapped_column(Text, ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    active_session_limit: Mapped[int] = mapped_column(nullable=False)
    runtime_class_limits: Mapped[dict | None] = mapped_column(JSON)
    max_session_seconds: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_by: Mapped[str | None] = mapped_column(Text, ForeignKey("platform_users.id", ondelete="SET NULL"))
    update_reason: Mapped[str | None] = mapped_column(Text)


class TenantEntitlement(Base):
    __tablename__ = "tenant_entitlements"

    tenant_id: Mapped[str] = mapped_column(Text, ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    plan_id: Mapped[str | None] = mapped_column(Text, ForeignKey("plans.id", ondelete="SET NULL"))
    active_session_limit_override: Mapped[int | None] = mapped_column()
    runtime_class_limits_override: Mapped[dict | None] = mapped_column(JSON)
    max_session_seconds_override: Mapped[int | None] = mapped_column()
    contract_ref: Mapped[str | None] = mapped_column(Text)
    trial_ends_at: Mapped[datetime | None] = mapped_column()
    effective_from: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    effective_until: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_by: Mapped[str | None] = mapped_column(Text, ForeignKey("platform_users.id", ondelete="SET NULL"))
    update_reason: Mapped[str | None] = mapped_column(Text)


class ApprovedRuntimeImage(Base):
    __tablename__ = "approved_runtime_images"
    __table_args__ = (UniqueConstraint("runtime_class", "image_digest"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    runtime_class: Mapped[str] = mapped_column(Text, nullable=False)
    image_ref: Mapped[str] = mapped_column(Text, nullable=False)
    image_digest: Mapped[str] = mapped_column(Text, nullable=False)
    chrome_version: Mapped[str | None] = mapped_column(Text)
    build_id: Mapped[str | None] = mapped_column(Text)
    scan_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    approval_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    approved_by: Mapped[str | None] = mapped_column(Text, ForeignKey("platform_users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


class PlatformAuditEvent(Base):
    __tablename__ = "platform_audit_events"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    actor_platform_user_id: Mapped[str | None] = mapped_column(Text, ForeignKey("platform_users.id", ondelete="SET NULL"))
    actor_role: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[str | None] = mapped_column(Text)
    tenant_id: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


class SessionRuntimePlacement(Base):
    __tablename__ = "session_runtime_placements"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    session_id: Mapped[str] = mapped_column(Text, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    runtime_provider: Mapped[str] = mapped_column(Text, nullable=False)
    runtime_namespace: Mapped[str] = mapped_column(Text, nullable=False)
    runtime_pod_name: Mapped[str | None] = mapped_column(Text)
    runtime_service_name: Mapped[str | None] = mapped_column(Text)
    runtime_node_name: Mapped[str | None] = mapped_column(Text)
    runtime_class: Mapped[str] = mapped_column(Text, nullable=False)
    placement_profile: Mapped[dict] = mapped_column(JSON, nullable=False)
    node_pool: Mapped[str | None] = mapped_column(Text)
    node_selector: Mapped[dict] = mapped_column(JSON, nullable=False)
    tolerations: Mapped[list] = mapped_column(JSON, nullable=False)
    runtime_phase: Mapped[str] = mapped_column(Text, nullable=False)
    egress_gateway_pod_name: Mapped[str | None] = mapped_column(Text)
    network_policy_name: Mapped[str | None] = mapped_column(Text)
    secret_name: Mapped[str | None] = mapped_column(Text)
    config_map_name: Mapped[str | None] = mapped_column(Text)
    image_ref: Mapped[str | None] = mapped_column(Text)
    image_digest: Mapped[str | None] = mapped_column(Text)
    requested_cpu: Mapped[str | None] = mapped_column(Text)
    requested_memory: Mapped[str | None] = mapped_column(Text)
    requested_ephemeral_storage: Mapped[str | None] = mapped_column(Text)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    failure_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    ready_at: Mapped[datetime | None] = mapped_column()
    ended_at: Mapped[datetime | None] = mapped_column()
    last_heartbeat_at: Mapped[datetime | None] = mapped_column()
    last_reconciled_at: Mapped[datetime | None] = mapped_column()
    last_error: Mapped[str | None] = mapped_column(Text)


class RuntimePool(Base):
    __tablename__ = "runtime_pools"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    runtime_classes: Mapped[list] = mapped_column(JSON, nullable=False)
    active_session_capacity: Mapped[int] = mapped_column(nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    is_draining: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    drain_reason: Mapped[str | None] = mapped_column(Text)
    drained_by: Mapped[str | None] = mapped_column(Text, ForeignKey("platform_users.id", ondelete="SET NULL"))
    drained_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


class RuntimeNode(Base):
    __tablename__ = "runtime_nodes"
    __table_args__ = (UniqueConstraint("runtime_pool_id", "provider_node_name"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    runtime_pool_id: Mapped[str] = mapped_column(Text, ForeignKey("runtime_pools.id", ondelete="CASCADE"), nullable=False)
    provider_node_name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    labels: Mapped[dict] = mapped_column(JSON, nullable=False)
    capacity: Mapped[dict] = mapped_column(JSON, nullable=False)
    allocatable: Mapped[dict] = mapped_column(JSON, nullable=False)
    drain_reason: Mapped[str | None] = mapped_column(Text)
    drained_by: Mapped[str | None] = mapped_column(Text, ForeignKey("platform_users.id", ondelete="SET NULL"))
    drained_at: Mapped[datetime | None] = mapped_column()
    disabled_reason: Mapped[str | None] = mapped_column(Text)
    disabled_by: Mapped[str | None] = mapped_column(Text, ForeignKey("platform_users.id", ondelete="SET NULL"))
    disabled_at: Mapped[datetime | None] = mapped_column()
    last_seen_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


class RuntimeCapacityReservation(Base):
    __tablename__ = "runtime_capacity_reservations"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    session_id: Mapped[str] = mapped_column(Text, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    runtime_pool_id: Mapped[str] = mapped_column(Text, ForeignKey("runtime_pools.id", ondelete="RESTRICT"), nullable=False)
    runtime_class: Mapped[str] = mapped_column(Text, nullable=False)
    reserved_phase: Mapped[str] = mapped_column(Text, nullable=False, server_default="reserved")
    reserved_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    released_at: Mapped[datetime | None] = mapped_column()
    release_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


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
    health_error: Mapped[str | None] = mapped_column(Text, server_default="")
    last_checked_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )
