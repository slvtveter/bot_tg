import re
import html

def to_telegram_html(text: str) -> str:
    """
    Formats standard Markdown and LaTeX expressions into Telegram-compliant HTML.
    Utilizes Telegram's native `<tg-math>` (inline math) and `<tg-math-block>` (block math)
    tags for native LaTeX rendering. All plain text elements are escaped safely using
    standard HTML entities to avoid parse errors.
    """
    if not text:
        return ""

    # 0. Normalize horizontal rules (e.g., *** or --- or ___ on their own line)
    # Convert them to a clean divider line of dashes
    text = re.sub(r'(?m)^\s*([\*\-_])\1{2,}\s*$', '————————', text)

    placeholders = []

    def add_placeholder(formatted_text: str) -> str:
        idx = len(placeholders)
        placeholders.append(formatted_text)
        return f"HTMLPLACEHOLDER{idx}TEMPHTML"

    current_text = text

    # 1. Extract Fenced Code Blocks
    def replace_code_block(match):
        lang = match.group(1) or ""
        content = match.group(2)
        escaped_content = html.escape(content)
        if lang:
            formatted = f'<pre><code class="language-{lang}">{escaped_content}</code></pre>'
        else:
            formatted = f'<pre><code>{escaped_content}</code></pre>'
        return add_placeholder(formatted)
    
    current_text = re.sub(r'```(\w+)?\n?([\s\S]*?)```', replace_code_block, current_text)

    # 2. Extract Block Math ($$...$$ or \[...\]) to <tg-math-block>
    def replace_block_math(match):
        content = match.group(1).strip()
        escaped_content = html.escape(content)
        formatted = f'<tg-math-block>{escaped_content}</tg-math-block>'
        return add_placeholder(formatted)

    current_text = re.sub(r'\$\$([\s\S]+?)\$\$', replace_block_math, current_text)
    current_text = re.sub(r'\\\[([\s\S]+?)\\\]', replace_block_math, current_text)

    # 3. Extract Inline Math ($math$ or \(math\)) to <tg-math>
    def replace_inline_math(match):
        content = match.group(1).strip()
        escaped_content = html.escape(content)
        formatted = f'<tg-math>{escaped_content}</tg-math>'
        return add_placeholder(formatted)

    current_text = re.sub(r'\$(?!\s)([^\$]+?)(?<!\s)\$', replace_inline_math, current_text)
    current_text = re.sub(r'\\\(([\s\S]+?)\\\)', replace_inline_math, current_text)

    # 4. Extract Inline Code (`code`)
    def replace_inline_code(match):
        content = match.group(1)
        escaped_content = html.escape(content)
        formatted = f'<code>{escaped_content}</code>'
        return add_placeholder(formatted)

    current_text = re.sub(r'`([^`]+?)`', replace_inline_code, current_text)

    # Helper function to parse bold, italic, links, headers, and blockquotes recursively
    def parse_formatting(txt: str) -> str:
        # Extract Headers (# Header, ## Header, etc.) and convert to Bold
        def replace_header(match):
            header_text = parse_formatting(match.group(1))
            formatted = f"<b>{header_text}</b>"
            return add_placeholder(formatted)

        txt = re.sub(r"(?m)^#{1,6}\s*(.+)$", replace_header, txt)

        # Extract Blockquotes (> text at start of line)
        def replace_blockquote(match):
            blockquote_text = parse_formatting(match.group(1))
            formatted = f"<blockquote>{blockquote_text}</blockquote>"
            return add_placeholder(formatted)

        txt = re.sub(r"(?m)^>\s*(.+)$", replace_blockquote, txt)

        # Extract Links
        def replace_link(match):
            link_text = parse_formatting(match.group(1))
            url = match.group(2)
            escaped_url = html.escape(url)
            formatted = f'<a href="{escaped_url}">{link_text}</a>'
            return add_placeholder(formatted)
        
        txt = re.sub(r'\[([^\]]+?)\]\(([^\)]+?)\)', replace_link, txt)

        # Bold (**bold**)
        def replace_bold(match):
            bold_text = parse_formatting(match.group(1))
            formatted = f"<b>{bold_text}</b>"
            return add_placeholder(formatted)
        
        txt = re.sub(r'\*\*(?!\s)([\s\S]+?)(?<!\s)\*\*', replace_bold, txt)

        # Italic (*italic* or _italic_)
        def replace_italic(match):
            italic_text = parse_formatting(match.group(1))
            formatted = f"<i>{italic_text}</i>"
            return add_placeholder(formatted)
        
        txt = re.sub(r'\*(?!\s)([\s\S]+?)(?<!\s)\*', replace_italic, txt)
        txt = re.sub(r'(?<!\w)_(?!\s)([\s\S]+?)(?<!\s)_(?!\w)', replace_italic, txt)

        return txt

    # Apply formatting parser
    current_text = parse_formatting(current_text)

    # 5. Escape all remaining plain text
    tokens = re.split(r'(HTMLPLACEHOLDER\d+TEMPHTML)', current_text)
    for i in range(len(tokens)):
        if not tokens[i].startswith("HTMLPLACEHOLDER"):
            tokens[i] = html.escape(tokens[i])
    
    escaped_text = "".join(tokens)

    # 6. Restore placeholders in reverse order
    for i in reversed(range(len(placeholders))):
        escaped_text = escaped_text.replace(f"HTMLPLACEHOLDER{i}TEMPHTML", placeholders[i])

    return escaped_text
