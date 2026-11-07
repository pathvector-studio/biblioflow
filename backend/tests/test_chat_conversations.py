"""Stored chat conversations: API, access rules, retention, cascades (needs the test database)."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update

from app.db.session import get_sessionmaker
from app.models import ChatConversation, ChatMessage, ChatMessageStatus, ChatRole
from app.services import chat as chat_service
from tests.helpers import join_workspace

API = "/api/v1"
URL = f"{API}/chat/conversations"


async def personal_ws(c) -> str:
    return next(w["id"] for w in (await c.get(f"{API}/workspaces")).json() if w["kind"] == "personal")


async def add_paper(c, title, workspace_id) -> str:
    r = await c.post(f"{API}/papers/metadata-only", json={"title": title, "workspace_id": workspace_id})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def make_conv(user, workspace_id, paper_id=None, question="What is a Clos fabric?", with_answer=True) -> str:
    """Store a conversation with one question and (optionally) one answer, through the service helpers."""
    async with get_sessionmaker()() as db:
        conv = await chat_service.create_conversation(
            db, user_id=user.id, workspace_id=uuid.UUID(str(workspace_id)),
            paper_id=uuid.UUID(str(paper_id)) if paper_id else None, first_message=question,
        )
        await chat_service.append_message(db, conv, role=ChatRole.user, content=question)
        if with_answer:
            await chat_service.append_message(
                db, conv, role=ChatRole.assistant, content="A leaf-spine network [1].",
                citations=[{"ref": 1, "label": "p.4"}], model="fake-model", usage={"input": 10, "output": 5},
            )
        await db.commit()
        return str(conv.id)


# ---------------------------------------------------------------- unit (no DB)
def test_make_title():
    assert chat_service.make_title("  hello \n  world  ") == "hello world"
    assert len(chat_service.make_title("x" * 500)) == chat_service.TITLE_MAX
    assert chat_service.make_title("   ") == "(empty)"


async def test_retention_zero_keeps_everything_without_touching_the_db():
    class Boom:
        def __call__(self):
            raise AssertionError("no DB access when retention is 0")

    assert await chat_service.delete_expired_conversations(Boom(), retention_days=0) == 0


# ---------------------------------------------------------------- own conversations
async def test_list_get_delete_own(login):
    me, c = await login("a@example.com")
    ws = await personal_ws(c)
    paper = await add_paper(c, "Scalable BGP Routing", ws)
    conv_paper = await make_conv(me, ws, paper, "Question about the paper")
    conv_lib = await make_conv(me, ws, None, "Question across the library", with_answer=False)

    items = (await c.get(URL)).json()
    assert [i["id"] for i in items] == [conv_lib, conv_paper]  # newest activity first
    assert items[1]["paper_id"] == paper and items[0]["paper_id"] is None
    assert items[1]["title"] == "Question about the paper" and items[1]["workspace_id"] == ws
    assert "messages" not in items[0]

    assert [i["id"] for i in (await c.get(URL, params={"paper_id": paper})).json()] == [conv_paper]
    assert len((await c.get(URL, params={"limit": 1})).json()) == 1
    assert [i["id"] for i in (await c.get(URL, params={"limit": 1, "offset": 1})).json()] == [conv_paper]

    detail = (await c.get(f"{URL}/{conv_paper}")).json()
    assert detail["id"] == conv_paper and [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    a = detail["messages"][1]
    assert a["content"] == "A leaf-spine network [1]." and a["citations"] == [{"ref": 1, "label": "p.4"}]
    assert a["model"] == "fake-model" and a["usage"] == {"input": 10, "output": 5} and a["status"] == "complete"

    assert (await c.delete(f"{URL}/{conv_paper}")).status_code == 204
    assert (await c.get(f"{URL}/{conv_paper}")).status_code == 404
    assert (await c.delete(f"{URL}/{conv_paper}")).status_code == 404
    async with get_sessionmaker()() as db:  # messages went with it
        assert (await db.execute(select(func.count()).select_from(ChatMessage))).scalar_one() == 0
    assert [i["id"] for i in (await c.get(URL)).json()] == [conv_lib]


async def test_append_message_bumps_updated_at_and_keeps_order(login):
    me, c = await login("a@example.com")
    ws = await personal_ws(c)
    older = await make_conv(me, ws, None, "older")
    newer = await make_conv(me, ws, None, "newer")
    assert [i["id"] for i in (await c.get(URL)).json()] == [newer, older]
    async with get_sessionmaker()() as db:
        conv = await db.get(ChatConversation, uuid.UUID(older))
        await chat_service.append_message(
            db, conv, role=ChatRole.assistant, content="partial", status=ChatMessageStatus.cancelled
        )
        await db.commit()
    assert [i["id"] for i in (await c.get(URL)).json()] == [older, newer]
    last = (await c.get(f"{URL}/{older}")).json()["messages"][-1]
    assert last["status"] == "cancelled"


async def test_missing_and_malformed_id_404_422(login):
    _, c = await login("a@example.com")
    assert (await c.get(f"{URL}/{uuid.uuid4()}")).status_code == 404
    assert (await c.delete(f"{URL}/{uuid.uuid4()}")).status_code == 404
    assert (await c.get(f"{URL}/not-a-uuid")).status_code == 422
    assert (await c.get(URL, params={"paper_id": str(uuid.uuid4())})).status_code == 404


async def test_requires_authentication(client):
    assert (await client.get(URL)).status_code == 401
    assert (await client.get(f"{URL}/{uuid.uuid4()}")).status_code == 401
    assert (await client.delete(f"{URL}/{uuid.uuid4()}")).status_code == 401


# ---------------------------------------------------------------- other users
async def test_other_user_gets_404(login):
    me, a = await login("a@example.com")
    _, b = await login("b@example.com")
    ws = await personal_ws(a)
    paper = await add_paper(a, "Mine", ws)
    conv = await make_conv(me, ws, paper)
    assert (await b.get(f"{URL}/{conv}")).status_code == 404
    assert (await b.delete(f"{URL}/{conv}")).status_code == 404
    assert (await b.get(URL)).json() == []
    assert (await b.get(URL, params={"paper_id": paper})).status_code == 404  # cannot even see the paper
    assert (await a.get(f"{URL}/{conv}")).status_code == 200  # still there


async def test_shared_workspace_member_cannot_read_my_conversation(login):
    owner, a = await login("a@example.com")
    _, b = await login("b@example.com")
    ws = (await a.post(f"{API}/workspaces", json={"name": "Lab"})).json()["id"]
    await join_workspace(a, b, ws, "b@example.com", "editor")
    paper = await add_paper(a, "Shared paper", ws)
    conv = await make_conv(owner, ws, paper)

    assert (await b.get(f"{API}/papers/{paper}")).status_code == 200  # b can read the paper itself
    assert (await b.get(f"{URL}/{conv}")).status_code == 404
    assert (await b.delete(f"{URL}/{conv}")).status_code == 404
    assert (await b.get(URL)).json() == []
    assert (await b.get(URL, params={"paper_id": paper})).json() == []
    assert (await a.get(f"{URL}/{conv}")).status_code == 200


async def test_losing_the_workspace_hides_the_conversation(login):
    _, a = await login("a@example.com")
    bu, b = await login("b@example.com")
    ws = (await a.post(f"{API}/workspaces", json={"name": "Lab"})).json()["id"]
    await join_workspace(a, b, ws, "b@example.com", "editor")
    paper = await add_paper(a, "Shared paper", ws)
    conv_paper = await make_conv(bu, ws, paper)
    conv_lib = await make_conv(bu, ws, None)
    assert (await b.get(f"{URL}/{conv_paper}")).status_code == 200
    assert len((await b.get(URL)).json()) == 2

    assert (await b.delete(f"{API}/workspaces/{ws}/members/{bu.id}")).status_code == 204  # leaves
    for conv in (conv_paper, conv_lib):
        assert (await b.get(f"{URL}/{conv}")).status_code == 404
        assert (await b.delete(f"{URL}/{conv}")).status_code == 404
    assert (await b.get(URL)).json() == []
    assert (await b.get(URL, params={"paper_id": paper})).status_code == 404
    async with get_sessionmaker()() as db:  # hidden, not deleted
        assert (await db.execute(select(func.count()).select_from(ChatConversation))).scalar_one() == 2
    assert (await a.get(f"{URL}/{conv_paper}")).status_code == 404  # the workspace owner is not the author either


# ---------------------------------------------------------------- PAT scopes / CSRF
async def test_pat_scopes(login, pat_client):
    me, c = await login("a@example.com")
    ws = await personal_ws(c)
    conv = await make_conv(me, ws)

    reader = await pat_client(c, ("read:papers",))
    assert (await reader.get(URL)).status_code == 200
    assert (await reader.get(f"{URL}/{conv}")).status_code == 200
    assert (await reader.delete(f"{URL}/{conv}")).status_code == 403

    writer = await pat_client(c, ("write:papers",))
    assert (await writer.get(URL)).status_code == 403
    assert (await writer.get(f"{URL}/{conv}")).status_code == 403
    assert (await writer.delete(f"{URL}/{conv}")).status_code == 204
    assert (await c.get(f"{URL}/{conv}")).status_code == 404


async def test_pat_of_another_user_is_404(login, pat_client):
    me, a = await login("a@example.com")
    _, b = await login("b@example.com")
    conv = await make_conv(me, await personal_ws(a))
    pat_b = await pat_client(b)
    assert (await pat_b.get(f"{URL}/{conv}")).status_code == 404
    assert (await pat_b.delete(f"{URL}/{conv}")).status_code == 404


async def test_delete_checks_origin_for_cookie_sessions(login, make_client):
    me, c = await login("a@example.com")
    conv = await make_conv(me, await personal_ws(c))
    c.headers["Origin"] = "https://evil.example.com"
    assert (await c.delete(f"{URL}/{conv}")).status_code == 403
    del c.headers["Origin"]
    assert (await c.delete(f"{URL}/{conv}")).status_code == 403  # no Origin/Referer at all
    c.headers["Origin"] = "https://app.example.com"
    assert (await c.delete(f"{URL}/{conv}")).status_code == 204


# ---------------------------------------------------------------- retention
async def _age(conv_id: str, days: int) -> None:
    async with get_sessionmaker()() as db:
        await db.execute(
            update(ChatConversation)
            .where(ChatConversation.id == uuid.UUID(conv_id))
            .values(updated_at=datetime.now(UTC) - timedelta(days=days))
        )
        await db.commit()


async def _ids() -> set[str]:
    async with get_sessionmaker()() as db:
        return {str(i) for i in (await db.execute(select(ChatConversation.id))).scalars()}


async def test_retention_deletes_only_old_conversations(login):
    me, c = await login("a@example.com")
    ws = await personal_ws(c)
    old, edge, fresh = await make_conv(me, ws), await make_conv(me, ws), await make_conv(me, ws)
    await _age(old, 91)
    await _age(edge, 89)

    assert await chat_service.delete_expired_conversations(get_sessionmaker(), retention_days=90) == 1
    assert await _ids() == {edge, fresh}
    async with get_sessionmaker()() as db:  # the old one's messages are gone, the others' remain
        assert (await db.execute(select(func.count()).select_from(ChatMessage))).scalar_one() == 4
    assert await chat_service.delete_expired_conversations(get_sessionmaker(), retention_days=90) == 0


async def test_retention_zero_keeps_old_conversations(login):
    me, c = await login("a@example.com")
    old = await make_conv(me, await personal_ws(c))
    await _age(old, 3650)
    assert await chat_service.delete_expired_conversations(get_sessionmaker(), retention_days=0) == 0
    assert await _ids() == {old}


async def test_retention_task_reads_the_setting(login, monkeypatch):
    from app.core.config import get_settings
    from app.workers.tasks import purge_old_conversations

    me, c = await login("a@example.com")
    ws = await personal_ws(c)
    old, fresh = await make_conv(me, ws), await make_conv(me, ws)
    await _age(old, 40)
    s = get_settings()
    monkeypatch.setattr(s, "chat_retention_days", 0)
    assert await purge_old_conversations({}) == 0
    monkeypatch.setattr(s, "chat_retention_days", 30)
    assert await purge_old_conversations({}) == 1
    assert await _ids() == {fresh}


def test_retention_cron_is_registered_daily():
    from app.workers.settings import WorkerSettings
    from app.workers.tasks import purge_old_conversations

    job = next(j for j in WorkerSettings.cron_jobs if j.coroutine is purge_old_conversations)
    assert job.hour == {3} and job.minute == {17} and job.unique


def test_retention_default_is_90_days():
    from app.core.config import Settings

    s = Settings(_env_file=None, database_url="postgresql+asyncpg://u:p@h/d", redis_url="redis://h", jwt_secret="x" * 40)
    assert s.chat_retention_days == 90


# ---------------------------------------------------------------- CASCADE
async def test_deleting_a_paper_removes_its_conversations(login):
    me, c = await login("a@example.com")
    ws = await personal_ws(c)
    p1, p2 = await add_paper(c, "Paper one", ws), await add_paper(c, "Paper two", ws)
    on_p1, on_p2, library = await make_conv(me, ws, p1), await make_conv(me, ws, p2), await make_conv(me, ws, None)
    assert (await c.delete(f"{API}/papers/{p1}")).status_code == 204
    assert await _ids() == {on_p2, library}
    assert (await c.get(f"{URL}/{on_p1}")).status_code == 404
    async with get_sessionmaker()() as db:
        assert (await db.execute(select(func.count()).select_from(ChatMessage))).scalar_one() == 4


async def test_deleting_the_user_removes_conversations(login, create_user):
    me, c = await login("a@example.com")
    conv = await make_conv(me, await personal_ws(c))
    from sqlalchemy import delete

    from app.models import User

    async with get_sessionmaker()() as db:
        await db.execute(delete(User).where(User.id == me.id))
        await db.commit()
    assert conv not in await _ids()
    async with get_sessionmaker()() as db:
        assert (await db.execute(select(func.count()).select_from(ChatMessage))).scalar_one() == 0
