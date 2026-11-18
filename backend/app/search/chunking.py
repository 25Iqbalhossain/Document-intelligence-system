from __future__ import annotations


def chunk_text(text: str, max_chars: int = 1000, overlap: int = 150) -> list[str]:
    """Split on paragraph/line/space boundaries into chunks of at most max_chars, with a small overlap."""
    text = text.strip()
    if not text:
        return []
    if overlap >= max_chars:
        raise ValueError("overlap must be smaller than max_chars")
    chunks: list[str] = []
    start, n = 0, len(text)
    while start < n:
        end = min(start + max_chars, n)
        if end < n:
            window = text[start:end]
            cut = max(window.rfind("\n\n"), window.rfind("\n"), window.rfind(". "), window.rfind(" "))
            if cut > max_chars // 2:
                end = start + cut + 1
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= n:
            break
        nxt = max(end - overlap, start + 1)
        # never begin a chunk in the middle of a word: move forward to the next word start (but not past `end`)
        while nxt < end and not text[nxt - 1].isspace():
            nxt += 1
        start = nxt
    return chunks
