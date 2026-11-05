"""Administrative CLI: ``python -m app.cli create-user --email you@example.com --admin``."""

import asyncio
import uuid

import typer
from email_validator import EmailNotValidError, validate_email
from sqlalchemy.exc import IntegrityError

from app.db.session import dispose_engine, get_sessionmaker
from app.services.users import create_user

cli = typer.Typer(help="BiblioFlow administration", no_args_is_help=True)


@cli.callback()
def _root() -> None:
    """BiblioFlow administration commands."""


async def _create(email: str, password: str, display_name: str | None, admin: bool) -> None:
    try:
        async with get_sessionmaker()() as db:
            user = await create_user(db, email=email, password=password, display_name=display_name, is_admin=admin)
            await db.commit()
            typer.echo(f"Created user {user.email} ({user.id}){' [admin]' if admin else ''}")
    finally:
        await dispose_engine()


@cli.command("create-user")
def create_user_cmd(
    email: str = typer.Option(..., "--email", prompt=True),
    password: str = typer.Option(
        ..., "--password", prompt=True, hide_input=True, confirmation_prompt=True, envvar="BF_PASSWORD"
    ),
    display_name: str | None = typer.Option(None, "--display-name"),
    admin: bool = typer.Option(False, "--admin", help="Grant admin flag"),
) -> None:
    """Create a user together with their personal workspace."""
    try:
        email = validate_email(email, check_deliverability=False).normalized.lower()
    except EmailNotValidError as exc:
        typer.echo(f"Invalid email: {exc}", err=True)
        raise typer.Exit(2) from exc
    try:
        asyncio.run(_create(email, password, display_name, admin))
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(2) from exc
    except IntegrityError as exc:
        typer.echo("Error: a user with this email already exists", err=True)
        raise typer.Exit(1) from exc


async def _reindex(paper_id: uuid.UUID | None) -> int:
    from sqlalchemy import select

    from app.core.queue import close_arq_pool, get_arq_pool
    from app.models.paper import Paper

    try:
        async with get_sessionmaker()() as db:
            stmt = select(Paper.id).where(Paper.has_pdf.is_(True))
            if paper_id is not None:
                stmt = stmt.where(Paper.id == paper_id)
            ids = list((await db.execute(stmt)).scalars())
        pool = await get_arq_pool()
        for pid in ids:
            await pool.enqueue_job("process_paper", str(pid))
        return len(ids)
    finally:
        await close_arq_pool()
        await dispose_engine()


@cli.command("reindex")
def reindex_cmd(
    all_papers: bool = typer.Option(False, "--all", help="Re-index every paper that has a PDF"),
    paper: uuid.UUID | None = typer.Option(None, "--paper", help="Re-index one paper by id"),
) -> None:
    """Queue ``process_paper`` again (re-extract chunks) for existing papers."""
    if all_papers == (paper is not None):
        typer.echo("Error: give exactly one of --all or --paper ID", err=True)
        raise typer.Exit(2)
    count = asyncio.run(_reindex(paper))
    if paper is not None and count == 0:
        typer.echo("Error: no such paper with a PDF", err=True)
        raise typer.Exit(1)
    typer.echo(f"Queued {count} paper(s)")


if __name__ == "__main__":
    cli()
