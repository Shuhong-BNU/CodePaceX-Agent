from mini_multi.audit import AuditLog
from mini_multi.auth import authorize


def test_every_authorization_decision_is_audited_with_sequence() -> None:
    audit = AuditLog()
    roles = {"alice": {"read"}}
    assert authorize("alice", "read", roles, audit) is True
    assert authorize("alice", "write", roles, audit) is False
    assert audit.events == [
        {"sequence": 1, "user": "alice", "action": "read", "allowed": True},
        {"sequence": 2, "user": "alice", "action": "write", "allowed": False},
    ]
