from deep_translator import GoogleTranslator


def translate_text(text, source="zh-CN", target="en"):
    """Translate text from source language to target language."""
    translator = GoogleTranslator(source=source, target=target)
    # GoogleTranslator has a character limit per request, so split large texts
    max_chunk_size = 4500
    if len(text) <= max_chunk_size:
        return translator.translate(text)

    chunks = _split_text(text, max_chunk_size)
    translated_chunks = [translator.translate(chunk) for chunk in chunks]
    return "".join(translated_chunks)


def _split_text(text, max_size):
    """Split text into chunks at paragraph boundaries where possible."""
    chunks = []
    paragraphs = text.split("\n")
    current_chunk = ""

    for paragraph in paragraphs:
        if len(current_chunk) + len(paragraph) + 1 <= max_size:
            current_chunk += paragraph + "\n"
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = paragraph + "\n"

    if current_chunk:
        chunks.append(current_chunk.strip())

    return chunks
