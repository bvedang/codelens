from sqlalchemy import Index
from sqlmodel import JSON, Column, Field, SQLModel


class RetrievalDocument(SQLModel, table=True):
    __tablename__ = "retrieval_documents"  # type: ignore[assignment]

    __table_args__ = (
        Index("ix_kind", "kind"),
        Index("ix_name", "name"),
        Index("ix_filepath", "filepath"),
        Index("ix_repo_root", "repo_root"),
    )

    chunk_id: str = Field(primary_key=True)
    kind: str
    name: str | None = None
    filepath: str | None = None
    repo_root: str | None = None
    package_name: str | None = None
    owner_chain: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    source_set: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    start_col: int | None = None
    end_col: int | None = None
    signature: str | None = None
    return_type: str | None = None
    field_type: str | None = None
    component_type: str | None = None
    annotations: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    modifiers: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    calls: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    fields_accessed: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    throws: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    extends_name: str | None = None
    implements: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    permits: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    resolved_symbols: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    text: str
    retrieval_text: str
