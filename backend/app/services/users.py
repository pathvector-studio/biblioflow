import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, validate_password
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceKind, WorkspaceMember, WorkspaceRole


async def create_user(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    display_name: str | None = None,
    is_admin: bool = False,
) -> User:
    """Create a user plus personal workspace and owner membership (caller commits).

    Raises ``sqlalchemy.exc.IntegrityError`` when the email already exists.
    """
    validate_password(password)
    email = email.strip().lower()
    user = User(
        email=email,
        password_hash=hash_password(password),
        display_name=(display_name or email.split("@", 1)[0])[:100],
        is_admin=is_admin,
    )
    db.add(user)
    await db.flush()

    ws_id = uuid.uuid4()
    db.add(Workspace(id=ws_id, name="Personal", slug=f"personal-{ws_id.hex[:12]}", kind=WorkspaceKind.personal, owner_id=user.id))
    await db.flush()
    db.add(WorkspaceMember(workspace_id=ws_id, user_id=user.id, role=WorkspaceRole.owner))
    await db.flush()
    return user
