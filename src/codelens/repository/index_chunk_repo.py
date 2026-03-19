from logging import getLogger

from sqlalchemy import delete
from sqlmodel import Session, col, select

from codelens.indexing.models import StoredChunk
from codelens.models.index_chunk import IndexBuildState, IndexChunk, IndexMeta
from codelens.repository.retrieval_document_repo import commit

logger = getLogger()


def upsert_index_metadata(session: Session, metadata:IndexMeta) -> None:
    session.merge(metadata)
    commit(session)

def get_index_metadata(session: Session, repo_root: str | None) -> IndexMeta | None:
    if not repo_root:
        logger.error("<get_index_metadata>: repo root path required to fetch index")
        return None
    return session.exec(select(IndexMeta).where(col(IndexMeta.repo_root) == repo_root)).first()


def get_index_chunks(session: Session, repo_root:str) -> dict[str,StoredChunk]:
    if not repo_root:
        logger.error("<get_index_metadata>: repo root path required to fetch index")
        return {}
    rows = session.exec(select(IndexChunk).where(IndexChunk.repo_root == repo_root)).all()
    chunks = {}
    for chunk in rows:
        chunks[chunk.chunk_id] = StoredChunk(
            chunk_id=chunk.chunk_id,
            faiss_ids= tuple(chunk.faiss_ids),
            payload= chunk.payload,
            shard = chunk.shard
        )
    return chunks

def insert_index_chunks(session:Session, chunks:list[IndexChunk]) -> None:
    if len(chunks) < 1:
        return
    for chunk in chunks:
        session.add(chunk)
    commit(session)


def delete_index_chunks_by_repo(session: Session, repo_root: str) -> None:
    session.execute(delete(IndexChunk).where(col(IndexChunk.repo_root) == repo_root))
    commit(session)

def delete_index_metadata(session: Session, repo_root: str) -> None:
    session.execute(delete(IndexMeta).where(col(IndexMeta.repo_root) == repo_root))
    commit(session)


def upsert_index_build_status(session: Session, state: IndexBuildState) -> IndexBuildState:
    merged = session.merge(state)
    commit(session)
    session.refresh(merged)
    session.expunge(merged)
    return merged

def get_index_build_status(session: Session, repo_root: str) -> IndexBuildState | None:
    result = session.exec(select(IndexBuildState).where(IndexBuildState.repo_root == repo_root)).first()
    if result is not None:
        session.refresh(result)
        session.expunge(result)
    return result
