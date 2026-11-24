"""Conservative, stateful transformations of MariaDB dump SQL.

Strings and ordinary comments are opaque. Executable version comments retain
their wrappers while their SQL is processed. Unsupported split clauses/modes
are refused instead of approximating SQL semantics.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

_WORD = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*|[0-9]+")
_SPACE = re.compile(r"\s+")
_EXECUTABLE = re.compile(r"/\*(?:!|M!)[0-9]*")
_OBJECTS = {"TABLE", "VIEW", "TRIGGER", "PROCEDURE", "FUNCTION", "EVENT", "DATABASE", "SCHEMA", "INDEX", "USER", "ROLE"}
_TRIVIA = {"space", "comment", "wrapper"}


class SqlTransformer:
    def __init__(self, *, strip_definer: bool, strip_database_directives: bool,
                 rewrites: Mapping[str, str] | None = None):
        self.strip_definer = strip_definer
        self.strip_database_directives = strip_database_directives
        self.rewrites = dict(rewrites or {})
        self.quote: str | None = None
        self.block_comment = False
        self.executable = False
        self.create_header = False
        self.mode_supported = True
        self.saved_modes: dict[str, bool] = {}
        self.in_routine = False
        self.delimiter = ";"

    def _quoted_end(self, text: str, start: int, quote: str) -> tuple[int, bool]:
        index = start
        while index < len(text):
            closing = text.find(quote, index)
            escape = text.find("\\", index) if quote != "`" else -1
            if escape >= 0 and (closing < 0 or escape < closing):
                index = escape + 2
            elif closing < 0:
                return len(text), False
            elif text[closing:closing + 2] == quote * 2:
                index = closing + 2
            else:
                return closing + 1, True
        return len(text), False

    def _tokens(self, text: str) -> list[tuple[str, str]]:
        tokens = []
        index = 0
        while index < len(text):
            start = index
            if self.quote is not None:
                index, closed = self._quoted_end(text, index, self.quote)
                tokens.append(("string", text[start:index]))
                if closed:
                    self.quote = None
                continue
            if self.block_comment:
                closer = text.find("*/", index)
                index = len(text) if closer < 0 else closer + 2
                tokens.append(("comment", text[start:index]))
                self.block_comment = closer < 0
                continue
            ch = text[index]
            if ch in {"'", '"', "`"}:
                index, closed = self._quoted_end(text, index + 1, ch)
                if ch == "`" and not closed:
                    raise ValueError("Multiline quoted identifiers are unsupported for safe SQL transformation")
                tokens.append(("identifier" if ch == "`" else "string", text[start:index]))
                if not closed:
                    self.quote = ch
                continue
            if text.startswith("/*", index):
                if self.executable:
                    raise ValueError("Nested executable SQL comments are unsupported")
                match = _EXECUTABLE.match(text, index)
                if match:
                    index = match.end()
                    self.executable = True
                    tokens.append(("wrapper", text[start:index]))
                else:
                    self.block_comment = True
                continue
            if self.executable and text.startswith("*/", index):
                self.executable = False
                index += 2
                tokens.append(("wrapper", text[start:index]))
                continue
            if ch == "#" or (text.startswith("--", index) and
                (index + 2 == len(text) or text[index + 2].isspace())):
                closer = text.find("\n", index)
                index = len(text) if closer < 0 else closer + 1
                tokens.append(("comment", text[start:index]))
                continue
            space = _SPACE.match(text, index)
            word = _WORD.match(text, index) if space is None else None
            if space:
                index = space.end()
                tokens.append(("space", text[start:index]))
            elif word:
                index = word.end()
                tokens.append(("word", text[start:index]))
            else:
                index += 1
                tokens.append(("symbol", ch))
        return tokens

    def transform(self, text: str) -> str:
        tokens = self._tokens(text)
        significant = [i for i, (kind, _) in enumerate(tokens) if kind not in _TRIVIA]
        code = [tokens[i][1].upper() if tokens[i][0] == "word" else tokens[i][1] for i in significant]
        if code and code[0] == "DELIMITER":
            delimiter = "".join(tokens[i][1] for i in significant[1:])
            if self.in_routine or not delimiter or len(delimiter) > 16 or any(ch in delimiter for ch in "'\"`\\"):
                raise ValueError("Unsupported or incomplete routine delimiter")
            self.delimiter = delimiter
            return text
        if code and not self.mode_supported and code[0] not in {"SET", "DELIMITER", ";"}:
            raise ValueError("Unverified SQL quote mode; transformation refused")
        if self.strip_database_directives and code and code[0] in {"DATABASE", "SCHEMA"} and tokens[significant[0]][0] == "word":
            raise ValueError("Split database directives are unsupported")
        ddl_object = 3 if code[:3] == ["CREATE", "OR", "REPLACE"] else 1
        directives = bool(code) and tokens[significant[0]][0] == "word" and (code[0] == "USE" or
            (len(code) > ddl_object and code[0] in {"CREATE", "DROP", "ALTER"} and code[ddl_object] in {"DATABASE", "SCHEMA"}))
        if self.strip_database_directives and directives:
            if self.in_routine:
                raise ValueError("Database directives inside routine bodies require unsupported transformation")
            if ";" not in code or any(t != ";" for t in code[code.index(";") + 1:]):
                raise ValueError("Multiline or combined database directives are unsupported")
            self.create_header = False
            # Ordinary comments are operator data, even on a directive line.
            return "".join(raw for kind, raw in tokens if kind == "comment")

        output = [raw for _, raw in tokens]
        skip_to = -1
        for position, index in enumerate(significant):
            if index <= skip_to:
                continue
            kind, raw = tokens[index]
            upper = raw.upper() if kind == "word" else ""
            next_index = significant[position + 1] if position + 1 < len(significant) else None
            following = tokens[next_index][1] if next_index is not None else None
            previous = tokens[significant[position - 1]][1] if position > 0 else None
            previous2 = tokens[significant[position - 2]][1] if position > 1 else None
            user_variable = previous == "@" and previous2 != "@"
            if code and code[0] == "SET" and not self.in_routine and kind == "word" and user_variable and following == "=":
                assigned = [tokens[i][1].upper() for i in significant[position + 2:position + 5]]
                self.saved_modes[upper] = self.mode_supported if assigned == ["@", "@", "SQL_MODE"] else False
            if upper == "CREATE":
                if not self.mode_supported:
                    raise ValueError("Unverified SQL quote mode; transformation refused")
                self.create_header = True
            elif upper in _OBJECTS or raw == ";":
                if self.create_header and upper in {"PROCEDURE", "FUNCTION", "TRIGGER", "EVENT"}:
                    self.in_routine = True
                self.create_header = False
            if upper == "SQL_MODE" and following == "=" and not user_variable and not self.in_routine:
                values = significant[position + 2:]
                if not values or tokens[values[0]][0] not in {"string", "symbol"}:
                    raise ValueError("Split or computed SQL mode assignments are unsupported")
                value_kind, value = tokens[values[0]]
                if (value_kind == "symbol" and value != "@") or (
                    value_kind == "string" and (len(value) < 2 or value[0] != value[-1])
                ):
                    raise ValueError("Unsupported SQL mode assignment")
                if value_kind == "string":
                    self.mode_supported = True
                else:
                    self.mode_supported = len(values) > 1 and self.saved_modes.get(tokens[values[1]][1].upper(), False)
                for next_index in significant[position + 2:]:
                    value_kind, value = tokens[next_index]
                    if value_kind == "string" and any(mode in value.upper() for mode in ("ANSI_QUOTES", "NO_BACKSLASH_ESCAPES")):
                        raise ValueError("SQL mode requires unsupported quote semantics; import refused")
                    if value == ";":
                        break
            if raw == ";" and not self.mode_supported and any(tokens[i][1] != ";" for i in significant[position + 1:]):
                raise ValueError("Combined statements after an unknown SQL mode are unsupported")
            if self.strip_definer and self.create_header and upper == "DEFINER":
                if position > 0 and tokens[significant[position - 1]][1].upper() == "SECURITY":
                    continue  # SQL SECURITY DEFINER is a behavior clause, not an account.
                if following is None:
                    raise ValueError("Split DEFINER clauses are unsupported")
                if following == "=":
                    # Supported dump grammar: DEFINER = `user`@`host` (also
                    # quoted SQL accounts), or CURRENT_USER[()]. Whitespace is
                    # allowed; comments inside a clause are refused/preserved.
                    args = significant[position + 2:position + 6]
                    end = None
                    if args and tokens[args[0]][1].upper() == "CURRENT_USER":
                        end = args[0]
                        if len(args) >= 2 and tokens[args[1]][1] == "(":
                            if len(args) < 3 or tokens[args[2]][1] != ")":
                                raise ValueError("Unsupported CURRENT_USER definer")
                            end = args[2]
                    elif len(args) >= 3 and tokens[args[0]][0] in {"identifier", "string"} and tokens[args[1]][1] == "@" and tokens[args[2]][0] in {"identifier", "string"} and all(
                        len(tokens[i][1]) >= 2 and tokens[i][1][-1] == tokens[i][1][0] for i in (args[0], args[2])
                    ):
                        end = args[2]
                    if end is None or any(k in {"comment", "wrapper"} for k, _ in tokens[index + 1:(end or index) + 1]):
                        raise ValueError("Unsupported DEFINER clause; unsafe substitution refused")
                    output[index] = " "
                    for removed in range(index + 1, end + 1):
                        output[removed] = ""
                    skip_to = end
                    continue
            name = raw[1:-1].replace("``", "`") if kind == "identifier" else raw if kind == "word" else None
            if name in self.rewrites and self.rewrites[name] != name:
                if following is None:
                    raise ValueError("Schema identifier split across lines; import refused")
                if following == ".":
                    target = self.rewrites[name]
                    output[index] = f"`{target}`" if kind == "identifier" else target
        code_tail = "".join(raw for kind, raw in tokens if kind not in _TRIVIA and kind != "string")
        if self.in_routine and code_tail.endswith(self.delimiter):
            self.in_routine = False
        return "".join(output)

    def finish(self) -> None:
        if self.quote is not None or self.block_comment or self.executable or self.in_routine:
            raise ValueError("Unterminated SQL quote/comment; import refused")
