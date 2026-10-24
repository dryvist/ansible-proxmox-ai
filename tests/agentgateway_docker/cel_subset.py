"""A tiny evaluator for the CEL subset the Hindsight ACL rules use.

It exists so the tests can run the rendered authorization rules against sample
requests instead of only comparing strings. It supports exactly: string, list and
boolean literals; `!`, `&&`, `||`, `==`, `!=`, `in`; field access, indexing and
calls to `default`, `contains`, `startsWith`, `endsWith`, `matches`, `stripPrefix`.
Anything else raises CelError, so a rule that uses a construct this module does
not model fails the test instead of being silently mis-evaluated.

Error semantics follow the gateway's documented behaviour: an expression that
cannot be evaluated is an error; an `allow` that errors does not match and a
`require` that errors denies. Operators short-circuit left to right.
"""

from __future__ import annotations

import re
from typing import Any

TOKEN = re.compile(
    r"""\s*(?:
    (?P<string>"(?:[^"\\]|\\.)*")
    |(?P<op>&&|\|\||==|!=|[!.()\[\],])
    |(?P<word>[A-Za-z_][A-Za-z0-9_]*)
    )""",
    re.VERBOSE,
)


class CelError(Exception):
    """The expression cannot be evaluated (missing field, bad type, unsupported syntax)."""


def _tokenize(source: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(source):
        if source[pos:].strip() == "":
            break
        match = TOKEN.match(source, pos)
        if not match:
            raise CelError(f"cannot tokenize at: {source[pos:pos + 20]!r}")
        pos = match.end()
        kind = match.lastgroup or ""
        tokens.append((kind, match.group(kind)))
    return tokens


class _Parser:
    def __init__(self, source: str) -> None:
        self.tokens = _tokenize(source)
        self.index = 0

    def peek(self) -> tuple[str, str]:
        return self.tokens[self.index] if self.index < len(self.tokens) else ("end", "")

    def take(self, value: str | None = None) -> tuple[str, str]:
        token = self.peek()
        if value is not None and token[1] != value:
            raise CelError(f"expected {value!r}, got {token[1]!r}")
        self.index += 1
        return token

    def parse(self) -> Any:
        node = self.parse_or()
        if self.peek()[0] != "end":
            raise CelError(f"trailing tokens at {self.peek()[1]!r}")
        return node

    def parse_or(self) -> Any:
        node = self.parse_and()
        while self.peek()[1] == "||":
            self.take()
            node = ("or", node, self.parse_and())
        return node

    def parse_and(self) -> Any:
        node = self.parse_rel()
        while self.peek()[1] == "&&":
            self.take()
            node = ("and", node, self.parse_rel())
        return node

    def parse_rel(self) -> Any:
        node = self.parse_unary()
        token = self.peek()
        if token[1] in ("==", "!=") or (token[0] == "word" and token[1] == "in"):
            self.take()
            return (token[1], node, self.parse_unary())
        return node

    def parse_unary(self) -> Any:
        if self.peek()[1] == "!":
            self.take()
            return ("not", self.parse_unary())
        return self.parse_postfix()

    def parse_postfix(self) -> Any:
        node = self.parse_primary()
        while True:
            token = self.peek()
            if token[1] == ".":
                self.take()
                name = self.take()[1]
                if self.peek()[1] == "(":
                    node = ("method", node, name, self.parse_args())
                else:
                    node = ("field", node, name)
            elif token[1] == "[":
                self.take()
                key = self.parse_or()
                self.take("]")
                node = ("index", node, key)
            else:
                return node

    def parse_args(self) -> list[Any]:
        self.take("(")
        args: list[Any] = []
        while self.peek()[1] != ")":
            args.append(self.parse_or())
            if self.peek()[1] == ",":
                self.take()
        self.take(")")
        return args

    def parse_primary(self) -> Any:
        kind, value = self.take()
        if kind == "string":
            return ("lit", bytes(value[1:-1], "utf-8").decode("unicode_escape"))
        if kind == "word" and value in ("true", "false"):
            return ("lit", value == "true")
        if kind == "word":
            if self.peek()[1] == "(":
                return ("call", value, self.parse_args())
            return ("var", value)
        if value == "(":
            node = self.parse_or()
            self.take(")")
            return node
        if value == "[":
            items: list[Any] = []
            while self.peek()[1] != "]":
                items.append(self.parse_or())
                if self.peek()[1] == ",":
                    self.take()
            self.take("]")
            return ("list", items)
        raise CelError(f"unexpected token {value!r}")


def _eval(node: Any, env: dict[str, Any]) -> Any:
    kind = node[0]
    if kind == "lit":
        return node[1]
    if kind == "list":
        return [_eval(item, env) for item in node[1]]
    if kind == "var":
        if node[1] not in env:
            raise CelError(f"undeclared reference {node[1]!r}")
        return env[node[1]]
    if kind == "field":
        target = _eval(node[1], env)
        if not isinstance(target, dict) or node[2] not in target:
            raise CelError(f"no such field {node[2]!r}")
        return target[node[2]]
    if kind == "index":
        target, key = _eval(node[1], env), _eval(node[2], env)
        if not isinstance(target, dict) or key not in target:
            raise CelError(f"no such key {key!r}")
        return target[key]
    if kind == "not":
        value = _eval(node[1], env)
        if not isinstance(value, bool):
            raise CelError("! needs a bool")
        return not value
    if kind in ("and", "or"):
        left = _eval(node[1], env)
        if not isinstance(left, bool):
            raise CelError("&&/|| need bools")
        if (kind == "and" and not left) or (kind == "or" and left):
            return left
        right = _eval(node[2], env)
        if not isinstance(right, bool):
            raise CelError("&&/|| need bools")
        return right
    if kind in ("==", "!="):
        equal = _eval(node[1], env) == _eval(node[2], env)
        return equal if kind == "==" else not equal
    if kind == "in":
        container = _eval(node[2], env)
        if not isinstance(container, list):
            raise CelError("in needs a list")
        return _eval(node[1], env) in container
    if kind == "call":
        if node[1] != "default" or len(node[2]) != 2:
            raise CelError(f"unsupported function {node[1]!r}")
        try:
            return _eval(node[2][0], env)
        except CelError:
            return _eval(node[2][1], env)
    if kind == "method":
        target = _eval(node[1], env)
        args = [_eval(arg, env) for arg in node[3]]
        if not isinstance(target, str) or not all(isinstance(arg, str) for arg in args) or len(args) != 1:
            raise CelError(f"bad receiver or arguments for {node[2]!r}")
        name, arg = node[2], args[0]
        if name == "contains":
            return arg in target
        if name == "startsWith":
            return target.startswith(arg)
        if name == "endsWith":
            return target.endswith(arg)
        if name == "matches":
            return re.search(arg, target) is not None
        if name == "stripPrefix":
            return target[len(arg):] if target.startswith(arg) else target
        raise CelError(f"unsupported method {name!r}")
    raise CelError(f"unsupported node {kind!r}")


def evaluate(source: str, env: dict[str, Any]) -> Any:
    """Evaluate one CEL expression against `env`; raises CelError on any failure."""
    return _eval(_Parser(source).parse(), env)


def authorize(rules: list[dict[str, str]], env: dict[str, Any]) -> tuple[bool, int | None]:
    """Apply the gateway's documented rule order; returns (allowed, matching allow index)."""
    for rule in rules:
        if "deny" in rule:
            raise CelError("deny rules are not allowed: an erroring deny fails open")
    for rule in rules:
        if "require" in rule:
            try:
                if evaluate(rule["require"], env) is not True:
                    return False, None
            except CelError:
                return False, None
    for index, rule in enumerate(rules):
        if "allow" in rule:
            try:
                if evaluate(rule["allow"], env) is True:
                    return True, index
            except CelError:
                continue
    return False, None
