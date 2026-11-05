"""Shared test helpers (no app imports at module level: env is configured in conftest first)."""


class FakeResolver:
    """In-memory MetadataResolver: ``add(meta)`` makes it resolvable by identifier / arXiv id / DOI."""

    def __init__(self) -> None:
        self.items: dict[str, object] = {}
        self.error: Exception | None = None
        self.calls: list[str] = []

    def add(self, meta) -> None:
        for key in (meta.identifier, meta.arxiv_id, meta.doi):
            if key:
                self.items[key.lower()] = meta

    async def resolve(self, query: str, *, limit: int = 5):
        from app.schemas.metadata import LookupItem, LookupResult
        from app.services.metadata.bibtex import make_bibtex
        from app.services.metadata.identifiers import parse_query

        self.calls.append(query)
        if self.error is not None:
            raise self.error
        parsed = parse_query(query)
        meta = self.items.get(parsed.value.lower())
        results = []
        if meta is not None:
            results = [LookupItem(**meta.model_dump(), bibtex=make_bibtex(meta, "key"))]
        return LookupResult(query=query, kind="doi" if parsed.kind == "pii" else parsed.kind, results=results)


def make_meta(identifier="2405.12345", title="Scalable BGP Routing in Clos Fabrics", year=2024, family="Smith", **kw):
    from app.models.paper import IdentifierType
    from app.schemas.metadata import Author, PaperMetadata

    itype = kw.pop("identifier_type", None) or (
        IdentifierType.doi if identifier.startswith("10.") else IdentifierType.arxiv
    )
    extra = {"arxiv_id": identifier} if itype == IdentifierType.arxiv else {"doi": identifier}
    return PaperMetadata(
        identifier=identifier, identifier_type=itype, title=title, year=year,
        authors=[Author(family=family, given="Alice")], venue="SIGCOMM", abstract="An abstract.",
        source="arxiv", **{**extra, **kw},
    )


class FakePool:
    def __init__(self) -> None:
        self.jobs: list[tuple] = []
        self.job_ids: set[str] = set()

    async def enqueue_job(self, name, *args, **kw):
        """Like Arq: a job id that is already queued is refused (returns None)."""
        job_id = kw.get("_job_id")
        if job_id is not None:
            if job_id in self.job_ids:
                return None
            self.job_ids.add(job_id)
        self.jobs.append((name, *args))
        return object()


def pdf_files(root) -> list:
    """All stored PDFs under the library (excluding the .incoming staging dir)."""
    return sorted(p for p in root.rglob("*.pdf") if ".incoming" not in p.parts)


async def join_workspace(owner, member_client, workspace_id, email, role="viewer", api="/api/v1") -> None:
    """Invite ``email`` as ``owner`` and accept as ``member_client`` (members are no longer added directly)."""
    r = await owner.post(f"{api}/workspaces/{workspace_id}/members", json={"email": email, "role": role})
    assert r.status_code == 202, r.text
    invs = (await member_client.get(f"{api}/invitations")).json()
    for inv in invs:
        r = await member_client.post(f"{api}/invitations/{inv['id']}/accept")
        assert r.status_code == 200, r.text
