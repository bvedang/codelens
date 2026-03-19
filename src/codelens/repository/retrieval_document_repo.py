from sqlalchemy import delete
from sqlmodel import Session, col, select

from codelens.models.retrieval_document import RetrievalDocument


def commit(session: Session) -> None:
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise


def upsert_documents(session: Session, docs: list[RetrievalDocument]) -> None:
    for doc in docs:
        session.merge(doc)
    commit(session)


def get_document(session: Session, chunk_id: str) -> RetrievalDocument | None:
    return session.get(RetrievalDocument, chunk_id)


def list_documents(
    session: Session,
    *,
    repo_root: str | None,
    kind: str | None,
    filepath: str | None,
) -> list[RetrievalDocument]:
    query = select(RetrievalDocument)

    if repo_root:
        query = query.where(col(RetrievalDocument.repo_root) == repo_root)
    if kind:
        query = query.where(col(RetrievalDocument.kind) == kind)
    if filepath:
        query = query.where(col(RetrievalDocument.filepath) == filepath)

    query = query.order_by(col(RetrievalDocument.chunk_id))
    return list(session.exec(query).all())


def delete_by_repo(session: Session, repo_root: str) -> None:
    session.execute(
        delete(RetrievalDocument).where(col(RetrievalDocument.repo_root) == repo_root)
    )
    commit(session)


def delete_by_file(session: Session, repo_root: str, filepath: str) -> None:
    session.execute(
        delete(RetrievalDocument).where(
            col(RetrievalDocument.repo_root) == repo_root,
            col(RetrievalDocument.filepath) == filepath,
        )
    )
    commit(session)
