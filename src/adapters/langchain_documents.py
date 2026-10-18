"""LangChain document loading/chunking after the synthetic PDF trust checks."""
from collections.abc import Iterator
from pathlib import Path

from langchain_core.document_loaders import BaseLoader
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.data_models import Dataset
from src.pdf_models import ParsedPDFDataset, RAGSourceMetadata
from src.retrieval.corpus import build_corpus
from src.retrieval.models import CorpusDocument
from src.retrieval.pdf_metadata import build_pdf_metadata


def split_verified_section(text: str, section_id: str) -> str:
    """Never join or renumber sections; reject a library-induced text change."""
    splitter = RecursiveCharacterTextSplitter(chunk_size=2000, chunk_overlap=0,
        separators=["\n\n", "\n", " ", ""], strip_whitespace=False)
    documents = splitter.create_documents([text], metadatas=[{"section_id": section_id}])
    if (len(documents) != 1 or documents[0].page_content != text
            or documents[0].metadata != {"section_id": section_id}):
        raise ValueError("LangChain chunking changed the verified section boundary")
    return documents[0].page_content


def corpus_documents(corpus: tuple[CorpusDocument, ...],
                     metadata: dict[str, RAGSourceMetadata] | None = None) -> list[Document]:
    provenance = metadata or {}
    return [Document(id=source.source_id, page_content=source.text, metadata={
        "source_payload": source.model_dump_json(),
        "rag_metadata": provenance[source.source_id].model_dump_json() if source.source_id in provenance else None,
    }) for source in corpus]


class LangChainPDFLoader(BaseLoader):
    """Load only a verified packet, with canonical metadata and baseline WOs."""

    def __init__(self, manifest_path: Path, baseline: Dataset) -> None:
        self.manifest_path, self.baseline = manifest_path, baseline
        self.parsed: ParsedPDFDataset | None = None

    def lazy_load(self) -> Iterator[Document]:
        from src.adapters.pdf_manuals import SyntheticPDFAdapter

        self.parsed = SyntheticPDFAdapter().parse(self.manifest_path, self.baseline)
        yield from corpus_documents(build_corpus(self.parsed.dataset), build_pdf_metadata(self.parsed))
