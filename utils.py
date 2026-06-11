import re
import html


def strip_markdown(val: str) -> str:
    """
    Strips basic markdown markers to keep table cell text clean.
    """
    return re.sub(r"\*\*|\*|~~|`|_", "", val)


def format_markdown_tables(text: str, add_placeholder) -> str:
    """
    Detects markdown tables and formats them into aligned fixed-width text tables
    wrapped in <pre>...</pre> blocks as placeholders.
    """
    lines = text.split("\n")
    new_lines = []
    in_table = False
    table_lines = []

    def process_table(t_lines):
        if not t_lines:
            return ""

        parsed_rows = []
        for line in t_lines:
            content = line.strip()
            if content.startswith("|"):
                content = content[1:]
            if content.endswith("|"):
                content = content[:-1]
            cells = [cell.strip() for cell in content.split("|")]

            # Check if this is a separator line (contains only dashes, colons, spaces)
            is_separator = all(re.match(r"^[\s\-:]+$", cell) for cell in cells)
            if is_separator:
                continue

            # Strip markdown markers from cells to ensure aligned lengths
            cleaned_cells = [strip_markdown(cell) for cell in cells]
            parsed_rows.append(cleaned_cells)

        if not parsed_rows:
            return "\n".join(t_lines)

        # Determine columns count
        num_cols = max(len(row) for row in parsed_rows)
        for row in parsed_rows:
            while len(row) < num_cols:
                row.append("")

        # Calculate max width for each column
        col_widths = [0] * num_cols
        for row in parsed_rows:
            for i in range(num_cols):
                col_widths[i] = max(col_widths[i], len(row[i]))

        # Format rows
        formatted_rows = []

        # Header row
        header = parsed_rows[0]
        header_formatted = " | ".join(
            f"{header[i]:<{col_widths[i]}}" for i in range(num_cols)
        )
        formatted_rows.append(header_formatted)

        # Separator line
        separator = "-+-".join("-" * col_widths[i] for i in range(num_cols))
        formatted_rows.append(separator)

        # Data rows
        for row in parsed_rows[1:]:
            row_formatted = " | ".join(
                f"{row[i]:<{col_widths[i]}}" for i in range(num_cols)
            )
            formatted_rows.append(row_formatted)

        table_text = "\n".join(formatted_rows)
        escaped_table_text = html.escape(table_text)
        formatted_html = f"<pre>{escaped_table_text}</pre>"
        return add_placeholder(formatted_html)

    i = 0
    while i < len(lines):
        line = lines[i]
        if re.match(r"^\s*\|.*\|\s*$", line):
            if not in_table:
                in_table = True
                table_lines = [line]
            else:
                table_lines.append(line)
        else:
            if in_table:
                new_lines.append(process_table(table_lines))
                in_table = False
                table_lines = []
            new_lines.append(line)
        i += 1

    if in_table:
        new_lines.append(process_table(table_lines))

    return "\n".join(new_lines)


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
    text = re.sub(r"(?m)^\s*([\*\-_])\1{2,}\s*$", "————————", text)

    placeholders = []

    def add_placeholder(formatted_text: str) -> str:
        idx = len(placeholders)
        placeholders.append(formatted_text)
        return f"HTMLPLACEHOLDER{idx}TEMPHTML"

    # 1. Parse tables first (to protect them from formatting/escaping)
    current_text = format_markdown_tables(text, add_placeholder)

    # 2. Extract Fenced Code Blocks
    def replace_code_block(match):
        lang = match.group(1) or ""
        content = match.group(2)
        escaped_content = html.escape(content)
        if lang:
            formatted = (
                f'<pre><code class="language-{lang}">{escaped_content}</code></pre>'
            )
        else:
            formatted = f"<pre><code>{escaped_content}</code></pre>"
        return add_placeholder(formatted)

    current_text = re.sub(
        r"```(\w+)?\n?([\s\S]*?)```", replace_code_block, current_text
    )

    # 3. Extract Block Math ($$...$$ or \[...\]) to <tg-math-block>
    def replace_block_math(match):
        content = match.group(1).strip()
        escaped_content = html.escape(content)
        formatted = f"<tg-math-block>{escaped_content}</tg-math-block>"
        return add_placeholder(formatted)

    current_text = re.sub(r"\$\$([\s\S]+?)\$\$", replace_block_math, current_text)
    current_text = re.sub(r"\\\[([\s\S]+?)\\\]", replace_block_math, current_text)

    # 4. Extract Inline Math ($math$ or \(math\)) to <tg-math>
    def replace_inline_math(match):
        content = match.group(1).strip()
        escaped_content = html.escape(content)
        formatted = f"<tg-math>{escaped_content}</tg-math>"
        return add_placeholder(formatted)

    current_text = re.sub(
        r"\$(?!\s)([^\$]+?)(?<!\s)\$", replace_inline_math, current_text
    )
    current_text = re.sub(r"\\\(([\s\S]+?)\\\)", replace_inline_math, current_text)

    # 5. Extract Inline Code (`code`)
    def replace_inline_code(match):
        content = match.group(1)
        escaped_content = html.escape(content)
        formatted = f"<code>{escaped_content}</code>"
        return add_placeholder(formatted)

    current_text = re.sub(r"`([^`]+?)`", replace_inline_code, current_text)

    # Helper function to parse bold, italic, links, headers, lists and blockquotes recursively
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

        # Unordered Lists
        def replace_list_item(match):
            indent = match.group(1) or ""
            content = parse_formatting(match.group(3))
            return f"{indent}• {content}"

        txt = re.sub(r"(?m)^(\s*)([\*\-\+])\s+(.+)$", replace_list_item, txt)

        # Extract Links
        def replace_link(match):
            link_text = parse_formatting(match.group(1))
            url = match.group(2)
            escaped_url = html.escape(url)
            formatted = f'<a href="{escaped_url}">{link_text}</a>'
            return add_placeholder(formatted)

        txt = re.sub(r"\[([^\]]+?)\]\(([^\)]+?)\)", replace_link, txt)

        # Underlines (__underline__)
        def replace_underline(match):
            underline_text = parse_formatting(match.group(1))
            formatted = f"<u>{underline_text}</u>"
            return add_placeholder(formatted)

        txt = re.sub(r"__(?!\s)([\s\S]+?)(?<!\s)__", replace_underline, txt)

        # Bold (**bold**)
        def replace_bold(match):
            bold_text = parse_formatting(match.group(1))
            formatted = f"<b>{bold_text}</b>"
            return add_placeholder(formatted)

        txt = re.sub(
            r"\*\frac{a}{b}\*(?!\s)([\s\S]+?)(?<!\s)\*\frac{a}{b}\*", replace_bold, txt
        )  # wait, simple fix for copy paste typo, let's keep standard bold regex below
        txt = re.sub(r"\*\*(?!\s)([\s\S]+?)(?<!\s)\*\*", replace_bold, txt)

        # Italic (*italic* or _italic_)
        def replace_italic(match):
            italic_text = parse_formatting(match.group(1))
            formatted = f"<i>{italic_text}</i>"
            return add_placeholder(formatted)

        txt = re.sub(r"\*(?!\s)([\s\S]+?)(?<!\s)\*", replace_italic, txt)
        txt = re.sub(r"(?<!\w)_(?!\s)([\s\S]+?)(?<!\s)_(?!\w)", replace_italic, txt)

        # Spoilers (||spoiler||)
        def replace_spoiler(match):
            spoiler_text = parse_formatting(match.group(1))
            formatted = f"<tg-spoiler>{spoiler_text}</tg-spoiler>"
            return add_placeholder(formatted)

        txt = re.sub(r"\|\|(?!\s)([\s\S]+?)(?<!\s)\|\|", replace_spoiler, txt)

        # Strikethrough (~~strikethrough~~)
        def replace_strikethrough(match):
            strikethrough_text = parse_formatting(match.group(1))
            formatted = f"<s>{strikethrough_text}</s>"
            return add_placeholder(formatted)

        txt = re.sub(r"~~(?!\s)([\s\S]+?)(?<!\s)~~", replace_strikethrough, txt)

        return txt

    # Apply formatting parser
    current_text = parse_formatting(current_text)

    # 6. Escape all remaining plain text
    tokens = re.split(r"(HTMLPLACEHOLDER\d+TEMPHTML)", current_text)
    for i in range(len(tokens)):
        if not tokens[i].startswith("HTMLPLACEHOLDER"):
            tokens[i] = html.escape(tokens[i])

    escaped_text = "".join(tokens)

    # 7. Restore placeholders in reverse order
    for i in reversed(range(len(placeholders))):
        escaped_text = escaped_text.replace(
            f"HTMLPLACEHOLDER{i}TEMPHTML", placeholders[i]
        )

    return escaped_text
