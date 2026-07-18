class AuditLog:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def record(self, user: str, action: str, allowed: bool) -> None:
        self.events.append({"user": user, "action": action, "allowed": allowed})
