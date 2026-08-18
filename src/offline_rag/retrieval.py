from __future__ import annotations

import asyncio
import json
import re
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass

from langchain.chains import create_history_aware_retriever, create_retrieval_chain
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain.retrievers import ContextualCompressionRetriever, EnsembleRetriever
from langchain.retrievers.document_compressors import CrossEncoderReranker
from langchain_community.cross_encoders import HuggingFaceCrossEncoder
from langchain_community.retrievers import BM25Retriever
from langchain_core.documents import Document
from langchain_core.messages import BaseMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_ollama import ChatOllama

from offline_rag.config import AppConfig
from offline_rag.vector_index import VectorIndex

REFUSAL = "I cannot answer this based on the provided documents."


@dataclass(frozen=True, slots=True)
class AnswerResult:
    answer: str
    citations: list[dict[str, object]]


@dataclass(frozen=True, slots=True)
class StreamChunk:
    """A single piece of a streaming response."""
    token: str = ""
    citations: list[dict[str, object]] | None = None
    done: bool = False


def tokenize(text: str) -> list[str]:
    return re.findall(r"\b[\w'-]+\b", text.lower())


def build_embeddings(config: AppConfig) -> HuggingFaceEmbeddings:
    return HuggingFaceEmbeddings(
        model_name=config.embedding_model,
        model_kwargs={
            "device": config.device,
            "local_files_only": True,
        },
        encode_kwargs={
            "normalize_embeddings": True,
            "batch_size": config.embedding_batch_size,
        },
    )


class RetrievalService:
    def __init__(self, config: AppConfig, vector_index: VectorIndex):
        self.config = config
        self.vector_index = vector_index
        self._lock = threading.RLock()
        self._bm25: BM25Retriever | None = None
        self._document_count = 0

        cross_encoder = HuggingFaceCrossEncoder(
            model_name=config.reranker_model,
            model_kwargs={
                "device": config.device,
                "local_files_only": True,
            },
        )
        self._compressor = CrossEncoderReranker(
            model=cross_encoder,
            top_n=config.rerank_top_n,
        )
        self._llm = ChatOllama(
            model=config.ollama_model,
            base_url=config.ollama_base_url,
            temperature=0,
            num_ctx=8192,
        )
        self.refresh_sparse_index()

    def refresh_sparse_index(self) -> int:
        documents = list(self.vector_index.iter_documents())
        retriever = None
        if documents:
            retriever = BM25Retriever.from_documents(
                documents,
                preprocess_func=tokenize,
            )
            retriever.k = self.config.sparse_k
        with self._lock:
            self._bm25 = retriever
            self._document_count = len(documents)
        return len(documents)

    def _build_rag_chain(self):
        """Construct the full RAG chain (retrieval → reranking → LLM)."""
        with self._lock:
            bm25 = self._bm25
            document_count = self._document_count
        if document_count == 0 or bm25 is None:
            return None, 0

        dense = self.vector_index.store.as_retriever(
            search_type="similarity",
            search_kwargs={"k": self.config.dense_k},
        )
        hybrid = EnsembleRetriever(
            retrievers=[dense, bm25],
            weights=[self.config.dense_weight, self.config.sparse_weight],
        )
        reranking_retriever = ContextualCompressionRetriever(
            base_retriever=hybrid,
            base_compressor=self._compressor,
        )

        contextualize_prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "Given the chat history and the latest user question, rewrite the "
                    "question as a standalone search query. Do not answer the question.",
                ),
                MessagesPlaceholder("chat_history"),
                ("human", "{input}"),
            ]
        )
        history_aware_retriever = create_history_aware_retriever(
            self._llm,
            reranking_retriever,
            contextualize_prompt,
        )

        answer_prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "Answer the question ONLY using the provided context. If the answer "
                    "is not contained in the context, explicitly state "
                    f"'{REFUSAL}' Do not hallucinate. Do not invent citations or facts. "
                    "Be concise but complete.\n\nContext:\n{context}",
                ),
                MessagesPlaceholder("chat_history"),
                ("human", "{input}"),
            ]
        )
        question_answer_chain = create_stuff_documents_chain(self._llm, answer_prompt)
        rag_chain = create_retrieval_chain(
            history_aware_retriever,
            question_answer_chain,
        )
        return rag_chain, document_count

    def answer(self, question: str, chat_history: list[BaseMessage]) -> AnswerResult:
        with self._lock:
            bm25 = self._bm25
            document_count = self._document_count
        if document_count == 0 or bm25 is None:
            return AnswerResult(answer=REFUSAL, citations=[])

        rag_chain, _ = self._build_rag_chain()
        if rag_chain is None:
            return AnswerResult(answer=REFUSAL, citations=[])

        result = rag_chain.invoke(
            {
                "input": question,
                "chat_history": chat_history,
            }
        )
        context = list(result.get("context") or [])
        answer = str(result.get("answer") or REFUSAL).strip()
        return AnswerResult(answer=answer, citations=self._citations(context))

    async def answer_streaming(
        self, question: str, chat_history: list[BaseMessage]
    ) -> AsyncIterator[StreamChunk]:
        """Yield tokens one-by-one as the LLM generates them.

        Retrieval and reranking run synchronously in a thread, then the LLM
        generation step streams tokens asynchronously.
        """
        with self._lock:
            bm25 = self._bm25
            document_count = self._document_count
        if document_count == 0 or bm25 is None:
            yield StreamChunk(token=REFUSAL)
            yield StreamChunk(done=True, citations=[])
            return

        # Build chain (this includes the sync retrieval/reranking steps)
        rag_chain, _ = self._build_rag_chain()
        if rag_chain is None:
            yield StreamChunk(token=REFUSAL)
            yield StreamChunk(done=True, citations=[])
            return

        collected_context: list[Document] = []

        try:
            async for chunk in rag_chain.astream(
                {"input": question, "chat_history": chat_history}
            ):
                # Collect context documents when they appear
                if "context" in chunk and chunk["context"]:
                    collected_context = list(chunk["context"])

                # Stream answer tokens
                if "answer" in chunk:
                    token = str(chunk["answer"])
                    if token:
                        yield StreamChunk(token=token)
        except Exception as exc:
            error_msg = (
                "The local RAG pipeline could not complete the request. "
                f"Details: {type(exc).__name__}: {exc}"
            )
            yield StreamChunk(token=error_msg)

        citations = self._citations(collected_context) if collected_context else []
        yield StreamChunk(done=True, citations=citations)

    @staticmethod
    def _citations(documents: list[Document]) -> list[dict[str, object]]:
        citations: set[tuple[str, int]] = set()
        for document in documents:
            metadata = document.metadata
            try:
                page_number = int(metadata.get("page_number", 1))
            except (TypeError, ValueError):
                page_number = 1
            aliases_raw = metadata.get("source_file_names_json")
            aliases: list[str] = []
            if isinstance(aliases_raw, str):
                try:
                    parsed = json.loads(aliases_raw)
                    if isinstance(parsed, list):
                        aliases = [str(value) for value in parsed if value]
                except json.JSONDecodeError:
                    pass
            if not aliases and metadata.get("source_file_name"):
                aliases = [str(metadata["source_file_name"])]
            for alias in aliases:
                citations.add((alias, page_number))
        return [
            {"source_file_name": file_name, "page_number": page_number}
            for file_name, page_number in sorted(citations, key=lambda item: (item[0], item[1]))
        ]


