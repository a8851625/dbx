from __future__ import annotations

import re
from dataclasses import dataclass

DDL_KEYWORDS = {
    "alter",
    "comment",
    "create",
    "drop",
    "grant",
    "rename",
    "revoke",
    "truncate",
}
DML_KEYWORDS = {
    "call",
    "copy",
    "delete",
    "exec",
    "execute",
    "insert",
    "load",
    "merge",
    "replace",
    "update",
}
READ_ONLY_KEYWORDS = {
    "desc",
    "describe",
    "explain",
    "pragma",
    "select",
    "show",
    "values",
    "with",
}
TRANSACTION_CONTROL_KEYWORDS = {
    "begin",
    "commit",
    "release",
    "rollback",
    "savepoint",
    "start",
}
HIGH_RISK_KEYWORDS = {
    "delete",
    "drop",
    "replace",
    "revoke",
    "truncate",
}

CHANGE_KEYWORDS = DDL_KEYWORDS | DML_KEYWORDS
CHANGE_KEYWORD_RE = re.compile(r"\b(" + "|".join(sorted(CHANGE_KEYWORDS)) + r")\b", re.IGNORECASE)
CHANGE_START_RE = re.compile(r"(?:^|[;(]|\))\s*(" + "|".join(sorted(CHANGE_KEYWORDS)) + r")\b", re.IGNORECASE)


@dataclass(frozen=True)
class ClassifiedStatement:
    order: int
    text: str
    statement_type: str
    risk_level: str
    risk_tags: list[str]
    keyword: str = ""

    @property
    def requires_approval(self) -> bool:
        return self.statement_type in {"ddl", "dml"}

    @property
    def is_directly_executable(self) -> bool:
        return self.statement_type in {"read", "metadata", "control"}


class SqlStatementSplitter:
    def __init__(self) -> None:
        self.buffer = ""
        self.in_single_quote = False
        self.in_double_quote = False
        self.in_backtick = False
        self.in_line_comment = False
        self.in_block_comment = False
        self.previous: str | None = None

    def push_chunk(self, chunk: str) -> list[str]:
        statements: list[str] = []
        chars = list(chunk)
        index = 0
        while index < len(chars):
            char = chars[index]
            nxt = chars[index + 1] if index + 1 < len(chars) else None

            if self.in_line_comment:
                self.buffer += char
                if char == "\n":
                    self.in_line_comment = False
                self.previous = char
                index += 1
                continue

            if self.in_block_comment:
                self.buffer += char
                if self.previous == "*" and char == "/":
                    self.in_block_comment = False
                self.previous = char
                index += 1
                continue

            if not self.in_single_quote and not self.in_double_quote and not self.in_backtick:
                if char == "-" and nxt == "-":
                    self.buffer += char
                    self.previous = char
                    self.in_line_comment = True
                    index += 1
                    continue
                if char == "/" and nxt == "*":
                    self.buffer += char
                    self.previous = char
                    self.in_block_comment = True
                    index += 1
                    continue

            self.buffer += char
            if char == "'" and not self.in_double_quote and not self.in_backtick and self.previous != "\\":
                self.in_single_quote = not self.in_single_quote
            elif char == '"' and not self.in_single_quote and not self.in_backtick and self.previous != "\\":
                self.in_double_quote = not self.in_double_quote
            elif char == "`" and not self.in_single_quote and not self.in_double_quote and self.previous != "\\":
                self.in_backtick = not self.in_backtick
            elif (
                char == ";"
                and not self.in_single_quote
                and not self.in_double_quote
                and not self.in_backtick
                and not self.in_line_comment
                and not self.in_block_comment
            ):
                statement = self.buffer[:-1].strip()
                self.buffer = ""
                if statement:
                    statements.append(statement)
                self.previous = None
                index += 1
                continue

            self.previous = char
            index += 1
        return statements

    def finish(self) -> list[str]:
        remaining = self.buffer.strip()
        self.buffer = ""
        self.previous = None
        self.in_single_quote = False
        self.in_double_quote = False
        self.in_backtick = False
        self.in_line_comment = False
        self.in_block_comment = False
        return [remaining] if remaining else []


def split_sql_statements(sql_text: str) -> list[str]:
    splitter = SqlStatementSplitter()
    statements = splitter.push_chunk(sql_text)
    statements.extend(splitter.finish())
    return statements


def classify_sql_statements(sql_text: str) -> list[ClassifiedStatement]:
    statements = split_sql_statements(sql_text)
    if not statements:
        raise ValueError("SQL payload is empty")

    return [classify_statement(statement, index) for index, statement in enumerate(statements, start=1)]


def classify_statement(statement: str, order: int = 1) -> ClassifiedStatement:
    keyword = leading_keyword(statement)
    normalized = strip_sql_comments_and_literals(statement).lower()
    effective_keyword = _effective_change_keyword(keyword, normalized)

    if effective_keyword in DDL_KEYWORDS:
        statement_type = "ddl"
    elif effective_keyword in DML_KEYWORDS:
        statement_type = "dml"
    elif keyword in READ_ONLY_KEYWORDS:
        statement_type = "metadata" if keyword in {"desc", "describe", "explain", "pragma", "show"} else "read"
    elif keyword in TRANSACTION_CONTROL_KEYWORDS:
        statement_type = "control"
    else:
        statement_type = "other"

    risk_tags: list[str] = []
    risk_level = "low"
    if statement_type in {"ddl", "dml"}:
        risk_level = "medium"
        if effective_keyword in HIGH_RISK_KEYWORDS:
            risk_tags.append("destructive")
            risk_level = "high"
        if "where" not in normalized and effective_keyword in {"delete", "update"}:
            risk_tags.append("no_where_clause")
            risk_level = "high"
        if statement_type == "ddl":
            risk_tags.append("structure_change")

    return ClassifiedStatement(
        order=order,
        text=statement.strip(),
        statement_type=statement_type,
        risk_level=risk_level,
        risk_tags=risk_tags,
        keyword=effective_keyword or keyword,
    )


def leading_keyword(statement: str) -> str:
    normalized = strip_sql_comments_and_literals(statement).strip().lstrip("(")
    match = re.match(r"([a-zA-Z_][\w$]*)", normalized)
    return match.group(1).lower() if match else ""


def first_change_keyword(statement: str) -> str:
    match = CHANGE_START_RE.search(strip_sql_comments_and_literals(statement))
    return match.group(1).lower() if match else ""


def contains_change_operation(statement: str) -> bool:
    return bool(first_change_keyword(statement))


def strip_sql_comments_and_literals(sql: str) -> str:
    result: list[str] = []
    index = 0
    in_single = False
    in_double = False
    in_backtick = False
    in_line_comment = False
    in_block_comment = False

    while index < len(sql):
        char = sql[index]
        nxt = sql[index + 1] if index + 1 < len(sql) else ""

        if in_line_comment:
            if char == "\n":
                in_line_comment = False
                result.append("\n")
            else:
                result.append(" ")
            index += 1
            continue

        if in_block_comment:
            if char == "*" and nxt == "/":
                in_block_comment = False
                result.extend("  ")
                index += 2
            else:
                result.append("\n" if char == "\n" else " ")
                index += 1
            continue

        if in_single:
            if char == "'" and nxt == "'":
                result.extend("  ")
                index += 2
                continue
            if char == "'":
                in_single = False
            result.append("\n" if char == "\n" else " ")
            index += 1
            continue

        if in_double:
            if char == '"' and nxt == '"':
                result.extend("  ")
                index += 2
                continue
            if char == '"':
                in_double = False
            result.append("\n" if char == "\n" else " ")
            index += 1
            continue

        if in_backtick:
            if char == "`":
                in_backtick = False
            result.append(" ")
            index += 1
            continue

        if char == "-" and nxt == "-":
            in_line_comment = True
            result.extend("  ")
            index += 2
            continue
        if char == "/" and nxt == "*":
            in_block_comment = True
            result.extend("  ")
            index += 2
            continue
        if char == "'":
            in_single = True
            result.append(" ")
            index += 1
            continue
        if char == '"':
            in_double = True
            result.append(" ")
            index += 1
            continue
        if char == "`":
            in_backtick = True
            result.append(" ")
            index += 1
            continue

        result.append(char)
        index += 1

    return "".join(result)


def resolve_ticket_type(statements: list[ClassifiedStatement]) -> str:
    kinds = {statement.statement_type for statement in statements}
    if len(kinds) == 1:
        return next(iter(kinds))
    return "mixed"


def resolve_ticket_risk(statements: list[ClassifiedStatement]) -> str:
    if any(statement.risk_level == "high" for statement in statements):
        return "high"
    if any(statement.statement_type == "ddl" for statement in statements):
        return "medium"
    return "low"


def summarize_sql(statements: list[ClassifiedStatement]) -> str:
    summary_parts = [f"{statement.statement_type.upper()}#{statement.order}" for statement in statements[:5]]
    extra = "" if len(statements) <= 5 else f" +{len(statements) - 5} more"
    return ", ".join(summary_parts) + extra


def _effective_change_keyword(keyword: str, normalized_sql: str) -> str:
    if keyword in CHANGE_KEYWORDS:
        return keyword
    if keyword == "with":
        match = CHANGE_START_RE.search(normalized_sql)
        return match.group(1).lower() if match else ""
    if keyword == "explain" and re.search(r"\banalyze\b", normalized_sql):
        target_keyword = _explain_analyze_target_keyword(normalized_sql)
        return target_keyword if target_keyword in CHANGE_KEYWORDS else ""
    return ""


def _explain_analyze_target_keyword(normalized_sql: str) -> str:
    target = re.sub(r"^\s*explain\s+(?:\([^)]*\)\s*)?", "", normalized_sql, count=1, flags=re.IGNORECASE)
    while True:
        match = re.match(r"\s*(analyze|verbose)\b", target, flags=re.IGNORECASE)
        if not match:
            break
        target = target[match.end() :]
    return leading_keyword(target)
