import re

def to_telegram_html(text: str) -> str:
    r"""
    Formats standard Markdown and LaTeX expressions into Telegram-compliant HTML.
    
    1. Escapes special HTML characters (&, <, >) first.
    2. Parses code blocks (```lang\n...\n```) to <pre><code class="language-lang">...</code></pre>.
    3. Parses inline code (`code`) to <code>...</code>.
    4. Parses bold (**bold**) to <b>...</b>.
    5. Parses italic (*italic* or _italic_) to <i>...</i>.
    6. Parses inline math ($math$) to <code>math</code>.
    7. Parses block math ($math$ or \[math\] or $$math$$) to <pre><code class="language-math">math</code></pre>.
    """
    if not text:
        return ""

    # 1. Escape HTML special characters first (only &, <, > to prevent breaking HTML tags we will add)
    escaped = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    placeholders = []

    def add_placeholder(formatted_text: str) -> str:
        placeholders.append(formatted_text)
        return f"HTMLPLACEHOLDERTEMP{len(placeholders) - 1}"

    # 2. Extract and format fenced code blocks (```lang\n...\n```)
    # Match ```lang\ncode``` or ```code```
    def replace_code_block(match):
        lang = match.group(1)
        content = match.group(2)
        if lang:
            formatted = f'<pre><code class="language-{lang}">{content}</code></pre>'
        else:
            formatted = f'<pre><code>{content}</code></pre>'
        return add_placeholder(formatted)

    escaped = re.sub(r'```(\w+)?\n?([\s\S]*?)```', replace_code_block, escaped)

    # 3. Extract and format block math
    # Match $$math$$
    def replace_block_math(match):
        content = match.group(1).strip()
        formatted = f'<pre><code class="language-math">{content}</code></pre>'
        return add_placeholder(formatted)

    escaped = re.sub(r'\$\$([\s\S]+?)\$\$', replace_block_math, escaped)

    # Match \[math\] (or \\[math\\] in escaped form)
    # The regex needs to look for the literal sequence \[ and \] in the escaped string.
    escaped = re.sub(r'\\\[([\s\S]+?)\\\]', replace_block_math, escaped)

    # Match single dollar on its own line (block math)
    escaped = re.sub(r'(?m)^\s*\$([^\$\n]+?)\$\s*$', replace_block_math, escaped)

    # 4. Extract and format inline code (`code`)
    def replace_inline_code(match):
        content = match.group(1)
        formatted = f'<code>{content}</code>'
        return add_placeholder(formatted)

    escaped = re.sub(r'`([^`]+?)`', replace_inline_code, escaped)

    # 5. Extract and format inline math ($math$)
    # Only match if there are no spaces immediately inside the dollar signs, to prevent matching regular text.
    def replace_inline_math(match):
        content = match.group(1).strip()
        formatted = f'<code>{content}</code>'
        return add_placeholder(formatted)

    escaped = re.sub(r'\$(?!\s)([^\$]+?)(?<!\s)\$', replace_inline_math, escaped)

    # 6. Parse bold (**bold**)
    # Ensure it doesn't match empty bold like ****
    escaped = re.sub(r'\*\*(?!\s)([\s\S]+?)(?<!\s)\*\*', r'<b>\1</b>', escaped)

    # 7. Parse italic (*italic* or _italic_)
    escaped = re.sub(r'\*(?!\s)([\s\S]+?)(?<!\s)\*', r'<i>\1</i>', escaped)
    escaped = re.sub(r'(?<!\w)_(?!\s)([\s\S]+?)(?<!\s)_(?!\w)', r'<i>\1</i>', escaped)

    # Restore placeholders in reverse order (or standard order, since they are unique placeholders)
    for i, formatted_val in enumerate(placeholders):
        escaped = escaped.replace(f"HTMLPLACEHOLDERTEMP{i}", formatted_val)

    return escaped
