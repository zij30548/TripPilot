"""Versioned internal-owner marker shared by runtime and offline validation."""
WORKSPACE_PASSWORD_MARKER = "!anonymous-workspace-v1"
WORKSPACE_USERNAME_PREFIX = "workspace_"


def workspace_fields(identifier: object, username: object, password_hash: object) -> bool:
    return (isinstance(identifier, str) and len(identifier) == 32
            and all(character in "0123456789abcdef" for character in identifier)
            and username == WORKSPACE_USERNAME_PREFIX + identifier
            and password_hash == WORKSPACE_PASSWORD_MARKER)
