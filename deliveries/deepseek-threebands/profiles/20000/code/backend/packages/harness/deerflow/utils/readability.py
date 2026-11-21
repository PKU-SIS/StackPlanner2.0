import logging
import re
import subprocess
from urllib.parse import urljoin

from markdownify import markdownify as md
from readabilipy import simple_json_from_html_string

logger = logging.getLogger(__name__)


class Article:
    url: str

    def __init__(self, title: str, html_content: str):
        self.title = title
        self.html_content = html_content

    def to_markdown(self, including_title: bool = True) -> str:
        markdown = ""
        if including_title:
            markdown += f"# {self.title}\n\n"

        if self.html_content is None or not str(self.html_content).strip():
            markdown += "*No content available*\n"
        else:
            markdown += md(self.html_content)

        return markdown

    def to_message(self) -> list[dict]:
        image_pattern = r"!\[.*?\]\((.*?)\)"

        content: list[dict[str, str]] = []
        markdown = self.to_markdown()

        if not markdown or not markdown.strip():
            return [{"type": "text", "text": "No content available"}]

        parts = re.split(image_pattern, markdown)

        for i, part in enumerate(parts):
            if i % 2 == 1:
                image_url = urljoin(self.url, part.strip())
                content.append({"type": "image_url", "image_url": {"url": image_url}})
            else:
                text_part = part.strip()
                if text_part:
                    content.append({"type": "text", "text": text_part})

        # If after processing all parts, content is still empty, provide a fallback message.
        if not content:
            content = [{"type": "text", "text": "No content available"}]

        return content


def extract_readable_markdown(raw_html: str) -> tuple[str | None, str | None]:
    """Extract readable Markdown from HTML using the existing extractor.

    Returns ``(markdown, error)``.  A placeholder/empty extraction is a
    failure, not valid body text.
    """
    try:
        article = ReadabilityExtractor().extract_article(raw_html)
        markdown = article.to_markdown().strip()
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    lowered = markdown.lower()
    if not markdown or "no content could be extracted" in lowered or "no content available" in lowered:
        return None, "readability extraction returned no content"
    return markdown, None


class ReadabilityExtractor:
    def extract_article(self, html: str) -> Article:
        try:
            article = simple_json_from_html_string(html, use_readability=True)
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            stderr = getattr(exc, "stderr", None)
            if isinstance(stderr, bytes):
                stderr = stderr.decode(errors="replace")
            stderr_info = f"; stderr={stderr.strip()}" if isinstance(stderr, str) and stderr.strip() else ""
            logger.warning(
                "Readability.js extraction failed with %s%s; falling back to pure-Python extraction",
                type(exc).__name__,
                stderr_info,
                exc_info=True,
            )
            article = simple_json_from_html_string(html, use_readability=False)

        html_content = article.get("content")
        if not html_content or not str(html_content).strip():
            html_content = "No content could be extracted from this page"

        title = article.get("title")
        if not title or not str(title).strip():
            title = "Untitled"

        return Article(title=title, html_content=html_content)
