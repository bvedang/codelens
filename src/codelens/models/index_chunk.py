from sqlmodel import JSON, Column, Field, SQLModel


class IndexMeta(SQLModel, table=True):
    repo_root: str = Field(primary_key=True)
    model_name: str | None = None
    indexed_at: str | None = None


class IndexChunk(SQLModel, table=True):
    chunk_id: str = Field(primary_key=True)
    repo_root: str
    faiss_ids: list[int] = Field(sa_column=Column(JSON, nullable=False))
    payload: dict = Field(sa_column=Column(JSON, nullable=False))
    shard: str | None = None


class IndexBuildState(SQLModel, table=True):
    repo_root: str = Field(primary_key=True)
    status: str
    workspace_signature: str
    model_name: str
    indexed_at: str
    total_files: int
    completed_files: list[str] = Field(sa_column=Column(JSON, nullable=False))
    failed_files: dict[str, str] = Field(sa_column=Column(JSON, nullable=False))
    documents_indexed: int = 0
    next_shard_id: int = 0
