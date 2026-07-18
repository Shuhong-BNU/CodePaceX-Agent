from mini_multi.audit import AuditLog


def authorize(user: str, action: str, roles: dict[str, set[str]], audit: AuditLog) -> bool:
    allowed = action in roles.get(user, set())
    if allowed:
        audit.record(user, action, True)
    return allowed
