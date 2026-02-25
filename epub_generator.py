import uuid
from ebooklib import epub


def generate_epub(title, author, content, output_path):
    """Generate an EPUB file from the given title, author, and content."""
    book = epub.EpubBook()
    book.set_identifier(str(uuid.uuid4()))
    book.set_title(title)
    book.set_language("en")
    book.add_author(author)

    # Create the main chapter
    chapter = epub.EpubHtml(title=title, file_name="chapter.xhtml", lang="en")
    html_content = _text_to_html(title, content)
    chapter.content = html_content

    book.add_item(chapter)

    # Add navigation
    book.toc = [epub.Link("chapter.xhtml", title, "chapter")]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    # Add basic CSS
    style = "body { font-family: serif; margin: 1em; } h1 { text-align: center; }"
    css = epub.EpubItem(
        uid="style_default",
        file_name="style/default.css",
        media_type="text/css",
        content=style,
    )
    book.add_item(css)
    chapter.add_item(css)

    book.spine = ["nav", chapter]

    epub.write_epub(output_path, book)


def _text_to_html(title, content):
    """Convert plain text content to HTML for the EPUB chapter."""
    paragraphs = "".join(
        f"<p>{para.strip()}</p>"
        for para in content.split("\n")
        if para.strip()
    )
    return f"<html><body><h1>{title}</h1>{paragraphs}</body></html>"
