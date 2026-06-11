import telegramify_markdown

def to_telegram_markdown(text: str) -> str:
    """
    Formats standard Markdown and LaTeX expressions into Telegram-compliant MarkdownV2
    using the telegramify-markdown library. This library parses LaTeX formulas into 
    clean Unicode representations and ensures all symbols are escaped to prevent crashes.
    """
    if not text:
        return ""
    return telegramify_markdown.markdownify(text)

