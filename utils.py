import re


def escape_plain_text(text: str) -> str:
    # Telegram MarkdownV2 special characters that must be escaped outside code blocks
    escape_chars = r"\_*[]()~`>#+-=|{}.!"
    # We must escape backslash first!
    escaped = ""
    for char in text:
        if char == "\\":
            escaped += "\\\\"
        elif char in escape_chars:
            escaped += f"\\{char}"
        else:
            escaped += char
    return escaped


def escape_code(text: str) -> str:
    # Inside code blocks, only backslash and backticks must be escaped
    return text.replace("\\", "\\\\").replace("`", "\\`")


def escape_text_with_placeholders(text: str) -> str:
    tokens = re.split(r"(MARKDOWNPLACEHOLDER\d+TEMPMARKDOWN)", text)
    for i in range(len(tokens)):
        if not tokens[i].startswith("MARKDOWNPLACEHOLDER"):
            tokens[i] = escape_plain_text(tokens[i])
    return "".join(tokens)


def to_telegram_markdown(text: str) -> str:
    if not text:
        return ""

    # 0. Normalize horizontal rules (e.g., *** or --- or ___ on their own line)
    # Convert them to a clean divider line of dashes
    text = re.sub(r"(?m)^\s*([\*\-_])\1{2,}\s*$", "————————", text)

    placeholders: list[str] = []

    def add_placeholder(formatted_text: str) -> str:
        idx = len(placeholders)
        placeholders.append(formatted_text)
        return f"MARKDOWNPLACEHOLDER{idx}TEMPMARKDOWN"

    current_text = text

    # 1. Extract Fenced Code Blocks
    def replace_code_block(match):
        lang = match.group(1) or ""
        content = match.group(2)
        escaped_content = escape_code(content)
        formatted = f"```{lang}\n{escaped_content}\n```"
        return add_placeholder(formatted)

    current_text = re.sub(
        r"```(\w+)?\n?([\s\S]*?)```", replace_code_block, current_text
    )

    # 2. Extract Block Math ($$...$$ or \[...\])
    def replace_block_math(match):
        content = match.group(1).strip()
        escaped_content = escape_code(content)
        formatted = f"```math\n{escaped_content}\n```"
        return add_placeholder(formatted)

    current_text = re.sub(r"\$\$([\s\S]+?)\$\$", replace_block_math, current_text)
    current_text = re.sub(r"\\\[([\s\S]+?)\\\]", replace_block_math, current_text)

    # 3. Extract Inline Math ($math$ or \(math\))
    def replace_inline_math(match):
        content = match.group(1).strip()
        escaped_content = escape_code(content)
        formatted = f"`{escaped_content}`"
        return add_placeholder(formatted)

    current_text = re.sub(
        r"\$(?!\s)([^\$]+?)(?<!\s)\$", replace_inline_math, current_text
    )
    current_text = re.sub(r"\\\(([\s\S]+?)\\\)", replace_inline_math, current_text)

    # 4. Extract Inline Code (`code`)
    def replace_inline_code(match):
        content = match.group(1)
        escaped_content = escape_code(content)
        formatted = f"`{escaped_content}`"
        return add_placeholder(formatted)

    current_text = re.sub(r"`([^`]+?)`", replace_inline_code, current_text)

    # Helper function to parse bold, italic, links, and blockquotes recursively
    def parse_formatting(txt: str) -> str:
        # Extract Headers (# Header, ## Header, etc.) and convert to Bold
        def replace_header(match):
            header_text = parse_formatting(match.group(1))
            escaped = escape_text_with_placeholders(header_text)
            formatted = f"*{escaped}*"
            return add_placeholder(formatted)

        txt = re.sub(r"(?m)^#{1,6}\s*(.+)$", replace_header, txt)

        # Extract Blockquotes (> text at start of line)
        def replace_blockquote(match):
            blockquote_text = parse_formatting(match.group(1))
            escaped = escape_text_with_placeholders(blockquote_text)
            formatted = f">{escaped}"
            return add_placeholder(formatted)

        txt = re.sub(r"(?m)^>\s*(.+)$", replace_blockquote, txt)

        # Extract Links
        def replace_link(match):
            link_text = parse_formatting(match.group(1))
            escaped_link_text = escape_text_with_placeholders(link_text)
            url = match.group(2)
            escaped_url = url.replace("\\", "\\\\").replace(")", "\\)")
            formatted = f"[{escaped_link_text}]({escaped_url})"
            return add_placeholder(formatted)

        txt = re.sub(r"\[([^\]]+?)\]\(([^\)]+?)\)", replace_link, txt)

        # Bold (**bold**)
        def replace_bold(match):
            bold_text = parse_formatting(match.group(1))
            escaped = escape_text_with_placeholders(bold_text)
            formatted = f"*{escaped}*"
            return add_placeholder(formatted)

        txt = re.sub(r"\*\*(?!\s)([\s\S]+?)(?<!\s)\*\*", replace_bold, txt)

        # Italic (*italic* or _italic_)
        def replace_italic(match):
            italic_text = parse_formatting(match.group(1))
            escaped = escape_text_with_placeholders(italic_text)
            formatted = f"_{escaped}_"
            return add_placeholder(formatted)

        txt = re.sub(r"\*(?!\s)([\s\S]+?)(?<!\s)\*", replace_italic, txt)
        txt = re.sub(r"(?<!\w)_(?!\s)([\s\S]+?)(?<!\s)_(?!\w)", replace_italic, txt)

        return txt

    # Apply formatting parser
    current_text = parse_formatting(current_text)

    # 5. Escape all remaining plain text
    escaped_text = escape_text_with_placeholders(current_text)

    # 6. Restore placeholders in reverse order
    for i in reversed(range(len(placeholders))):
        escaped_text = escaped_text.replace(
            f"MARKDOWNPLACEHOLDER{i}TEMPMARKDOWN", placeholders[i]
        )

    return escaped_text
