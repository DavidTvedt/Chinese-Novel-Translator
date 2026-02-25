# Chinese-Novel-Translator

A helpful tool for translating Chinese stories and exporting them as PDF or EPUB files.

## Features

- Translates Chinese text to English (or any other language supported by Google Translate)
- Exports translated content as **PDF** or **EPUB**
- Handles large texts by automatically splitting them into chunks

## Requirements

- Python 3.8+
- Install dependencies:

```bash
pip install -r requirements.txt
```

## Usage

```bash
python main.py <input_file.txt> [options]
```

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `--title` | `Translated Novel` | Title of the novel |
| `--author` | `Unknown` | Author of the novel |
| `--output` | `output` | Output file path without extension |
| `--format` | `both` | Output format: `pdf`, `epub`, or `both` |
| `--source` | `zh-CN` | Source language code |
| `--target` | `en` | Target language code |

### Examples

Translate a Chinese novel and generate both PDF and EPUB:

```bash
python main.py my_novel.txt --title "My Novel" --author "Author Name"
```

Generate only a PDF:

```bash
python main.py my_novel.txt --title "My Novel" --format pdf --output my_novel
```

Generate only an EPUB:

```bash
python main.py my_novel.txt --title "My Novel" --author "Author Name" --format epub --output my_novel
```
