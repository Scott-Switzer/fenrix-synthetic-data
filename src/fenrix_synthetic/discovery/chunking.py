from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

from .schemas import DiscoveryChunk


@dataclass
class ChunkingConfig:
    max_chars: int = 1500
    overlap_chars: int = 100
    section_aware: bool = True
    paragraph_aware: bool = True


class TextChunker:
    def __init__(self, config: ChunkingConfig | None = None) -> None:
        self._config = config or ChunkingConfig()
        self._policy_hash = self._compute_policy_hash()

    @property
    def policy_hash(self) -> str:
        return self._policy_hash

    def _compute_policy_hash(self) -> str:
        cfg = self._config
        content = f"max_chars={cfg.max_chars}:overlap={cfg.overlap_chars}:section={cfg.section_aware}:para={cfg.paragraph_aware}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def chunk(
        self,
        text: str,
        document_artifact_id: str,
        section_metadata: dict[str, Any] | None = None,
    ) -> list[DiscoveryChunk]:
        if not text:
            return []

        if self._config.section_aware and section_metadata:
            return self._chunk_section_aware(text, document_artifact_id, section_metadata)

        if self._config.paragraph_aware:
            return self._chunk_paragraph_aware(text, document_artifact_id)

        return self._chunk_fixed(text, document_artifact_id)

    def _chunk_fixed(self, text: str, document_artifact_id: str) -> list[DiscoveryChunk]:
        chunks: list[DiscoveryChunk] = []
        max_c = self._config.max_chars
        overlap = self._config.overlap_chars
        pos = 0
        index = 0

        while pos < len(text):
            end = min(pos + max_c, len(text))
            if end < len(text):
                ws = _find_word_boundary(text, end)
                if ws > end:
                    end = ws

            chunk_text = text[pos:end]
            chunk_id = f"chunk-{uuid.uuid4().hex[:8]}"

            chunks.append(
                DiscoveryChunk(
                    chunk_id=chunk_id,
                    document_artifact_id=document_artifact_id,
                    chunk_index=index,
                    start_offset=pos,
                    end_offset=end,
                    text=chunk_text,
                )
            )

            if end >= len(text):
                break

            pos = end - overlap if overlap > 0 else end
            if pos <= chunks[-1].start_offset:
                pos = chunks[-1].end_offset
            index += 1

        return chunks

    def _chunk_paragraph_aware(self, text: str, document_artifact_id: str) -> list[DiscoveryChunk]:
        """Split text into chunks by paragraph, respecting max size."""
        paragraphs = _split_paragraphs(text)
        chunks: list[DiscoveryChunk] = []
        current: list[str] = []
        current_len = 0
        index = 0
        chunk_start = 0  # Start position in original text for current chunk
        overlap = self._config.overlap_chars

        for para in paragraphs:
            para_len = len(para)
            # Account for separator (2 newlines between paragraphs, except first)
            extra = 2 if current else 0
            if current_len + extra + para_len <= self._config.max_chars:
                current.append(para)
                current_len += extra + para_len
            else:
                # Flush current chunk
                if current:
                    chunk_text = "\n\n".join(current)
                    chunks.append(
                        DiscoveryChunk(
                            chunk_id=f"chunk-{uuid.uuid4().hex[:8]}",
                            document_artifact_id=document_artifact_id,
                            chunk_index=index,
                            start_offset=chunk_start,
                            end_offset=chunk_start + len(chunk_text),
                            text=chunk_text,
                        )
                    )
                    index += 1

                    # Carry overlap into next chunk
                    if overlap > 0 and len(chunk_text) > overlap:
                        overlap_text = chunk_text[-overlap:]
                        chunk_start = chunk_start + len(chunk_text) - overlap
                        current = [overlap_text]
                        current_len = len(overlap_text)
                    else:
                        chunk_start = chunk_start + len(chunk_text)
                        current = []
                        current_len = 0

                # Handle very long paragraphs
                if para_len > self._config.max_chars:
                    sub_chunks = self._chunk_fixed(para, document_artifact_id)
                    for sc in sub_chunks:
                        sc.chunk_id = f"chunk-{uuid.uuid4().hex[:8]}"
                        sc.chunk_index = index
                        sc.start_offset += chunk_start
                        sc.end_offset += chunk_start
                        chunks.append(sc)
                        index += 1
                    chunk_start += len(para)
                    current = []
                    current_len = 0
                else:
                    current.append(para)
                    current_len = para_len

        # Flush remaining text
        if current:
            chunk_text = "\n\n".join(current)
            chunks.append(
                DiscoveryChunk(
                    chunk_id=f"chunk-{uuid.uuid4().hex[:8]}",
                    document_artifact_id=document_artifact_id,
                    chunk_index=index,
                    start_offset=chunk_start,
                    end_offset=chunk_start + len(chunk_text),
                    text=chunk_text,
                )
            )

        return _renumber_chunks(chunks)

    def _chunk_section_aware(
        self,
        text: str,
        document_artifact_id: str,
        section_metadata: dict[str, Any],
    ) -> list[DiscoveryChunk]:
        sections = section_metadata.get("sections", [])
        if not sections:
            return self._chunk_paragraph_aware(text, document_artifact_id)

        chunks: list[DiscoveryChunk] = []
        index = 0
        for section in sections:
            sec_start = section.get("char_start", 0)
            sec_end = section.get("char_end", len(text))
            sec_text = text[sec_start:sec_end]
            sec_title = section.get("title", "")

            sub_chunks = self._chunk_paragraph_aware(sec_text, document_artifact_id)
            for sc in sub_chunks:
                sc.chunk_id = f"chunk-{uuid.uuid4().hex[:8]}"
                sc.chunk_index = index
                sc.start_offset += sec_start
                sc.end_offset += sec_start
                sc.section_hint = sec_title
                chunks.append(sc)
                index += 1

        return _renumber_chunks(chunks)


def _split_paragraphs(text: str) -> list[str]:
    lines = text.split("\n")
    paragraphs: list[str] = []
    current: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current:
                paragraphs.append("\n".join(current))
                current = []
        else:
            current.append(line)

    if current:
        paragraphs.append("\n".join(current))

    return paragraphs


def _find_word_boundary(text: str, pos: int) -> int:
    if pos >= len(text):
        return len(text)
    while pos < len(text) and text[pos] in " \t\n\r":
        pos += 1
    while pos < len(text) and text[pos] not in " \t\n\r":
        pos += 1
    return pos


def _renumber_chunks(chunks: list[DiscoveryChunk]) -> list[DiscoveryChunk]:
    for i, c in enumerate(chunks):
        c.chunk_index = i
    return chunks
