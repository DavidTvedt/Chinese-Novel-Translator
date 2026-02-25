import argparse
import os
import sys

from translator import translate_text
from pdf_generator import generate_pdf
from epub_generator import generate_epub


def parse_args():
    parser = argparse.ArgumentParser(
        description="Translate a Chinese novel and export it to PDF or EPUB."
    )
    parser.add_argument("input", help="Path to the Chinese text file (.txt)")
    parser.add_argument(
        "--title", default="Translated Novel", help="Title of the novel"
    )
    parser.add_argument(
        "--author", default="Unknown", help="Author of the novel"
    )
    parser.add_argument(
        "--output",
        default="output",
        help="Output file path without extension (default: output)",
    )
    parser.add_argument(
        "--format",
        choices=["pdf", "epub", "both"],
        default="both",
        help="Output format: pdf, epub, or both (default: both)",
    )
    parser.add_argument(
        "--source",
        default="zh-CN",
        help="Source language code (default: zh-CN)",
    )
    parser.add_argument(
        "--target",
        default="en",
        help="Target language code (default: en)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if not os.path.isfile(args.input):
        print(f"Error: Input file '{args.input}' not found.", file=sys.stderr)
        sys.exit(1)

    with open(args.input, "r", encoding="utf-8") as f:
        chinese_text = f.read()

    print(f"Translating '{args.input}' from {args.source} to {args.target}...")
    translated_text = translate_text(chinese_text, source=args.source, target=args.target)
    print("Translation complete.")

    if args.format in ("pdf", "both"):
        pdf_path = args.output + ".pdf"
        print(f"Generating PDF: {pdf_path}")
        generate_pdf(args.title, translated_text, pdf_path)
        print(f"PDF saved to {pdf_path}")

    if args.format in ("epub", "both"):
        epub_path = args.output + ".epub"
        print(f"Generating EPUB: {epub_path}")
        generate_epub(args.title, args.author, translated_text, epub_path)
        print(f"EPUB saved to {epub_path}")


if __name__ == "__main__":
    main()
