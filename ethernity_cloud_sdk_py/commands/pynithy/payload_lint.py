"""Build-time safety lint for the serverless backend (Python dApps).

WHAT THIS IS: a lint that runs on the developer's machine at `ecld-build`,
flagging dynamic code execution -- especially of task INPUT -- before the
enclave image is sealed. Its purpose is to stop an honest developer from
accidentally writing `eval(<task input>)`, which would let a submitter run
arbitrary code inside the dApp's enclave and bypass the per-key state ACL for
that dApp's other users.

WHAT THIS IS NOT: a sandbox or a defence against a MALICIOUS author. The author
builds their own image and can edit or remove this check locally, so it cannot
constrain code they intend to ship. Cross-dApp isolation rests on the enclave
encryption key (state is encrypted under an identity the payload cannot read);
hard enforcement of per-key ownership rests on the trustedzone re-adjudicating
commits against the DO owner it independently reads. This lint is defence in
depth on top of those, aimed at the accidental footgun.

POLICY
- HARD ERROR: a dynamic-exec call (eval / exec / compile-to-exec) whose
  argument is, syntactically in the same expression, the task input dataset
  (`___etny_data_set___`) or a subscript/attr of it. This is the case that
  actually opens the enclave to a submitter.
- WARNING: any other use of eval / exec / compile / __import__ / a dynamic
  `getattr(obj, <non-literal>)`. We cannot prove these are fed untrusted input
  (that would require sound taint analysis, which is undecidable), so we flag
  rather than block -- indirection like `f = eval; f(x)` is deliberately NOT
  claimed to be caught.

OPT-OUT (this is a lint, never an unbypassable wall):
- `# ecld: allow-eval` on the call's line downgrades that finding to silent.
- `# ecld: allow-eval-file` anywhere in the file disables the lint for it.
"""

import ast

INPUT_NAMES = {"___etny_data_set___"}
DYNAMIC_EXEC = {"eval", "exec", "compile", "__import__"}
FILE_OPT_OUT = "ecld: allow-eval-file"
LINE_OPT_OUT = "ecld: allow-eval"


class Finding:
    __slots__ = ("severity", "line", "col", "message")

    def __init__(self, severity, line, col, message):
        self.severity = severity      # "error" | "warning"
        self.line = line
        self.col = col
        self.message = message


def _root_name(node):
    """The leftmost Name of a Name/Subscript/Attribute chain, or None."""
    while isinstance(node, (ast.Subscript, ast.Attribute)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _mentions_input(node):
    """True if `node` references the task input dataset anywhere within it."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id in INPUT_NAMES:
            return True
    return False


class _Visitor(ast.NodeVisitor):
    def __init__(self, allow_lines):
        self.findings = []
        self._allow_lines = allow_lines

    def _skip(self, node):
        return getattr(node, "lineno", None) in self._allow_lines

    def visit_Call(self, node):
        func = node.func
        name = func.id if isinstance(func, ast.Name) else None

        if name in DYNAMIC_EXEC and not self._skip(node):
            tainted = any(_mentions_input(a) for a in node.args)
            if name in ("eval", "exec") and tainted:
                self.findings.append(Finding(
                    "error", node.lineno, node.col_offset,
                    f"{name}() is called on task input (___etny_data_set___). "
                    "A submitter could run arbitrary code inside your enclave and "
                    "reach other users' state. Parse the input explicitly instead "
                    "of executing it. (Suppress with `# ecld: allow-eval` if you "
                    "are certain this input is trusted.)"))
            elif name == "compile" and tainted:
                self.findings.append(Finding(
                    "error", node.lineno, node.col_offset,
                    "compile() is called on task input; the compiled code can then "
                    "be exec'd. Do not compile untrusted input inside the enclave."))
            else:
                self.findings.append(Finding(
                    "warning", node.lineno, node.col_offset,
                    f"{name}() executes code dynamically. Make sure its argument is "
                    "never derived from task input; the lint cannot prove this "
                    "automatically. (`# ecld: allow-eval` to silence.)"))

        # Dynamic getattr(obj, <non-literal>) -- a common way to reach a name
        # chosen at runtime (including an imported module's internals).
        if name == "getattr" and not self._skip(node) and len(node.args) >= 2:
            key = node.args[1]
            if not isinstance(key, ast.Constant):
                sev = "error" if _mentions_input(key) else "warning"
                msg = ("getattr() selects an attribute from task input; a submitter "
                       "could reach arbitrary attributes (e.g. module internals). "
                       "Use an explicit allowlist."
                       if sev == "error" else
                       "getattr() with a non-literal attribute name executes a "
                       "runtime-chosen lookup. Ensure the name is not derived from "
                       "task input. (`# ecld: allow-eval` to silence.)")
                self.findings.append(Finding(sev, node.lineno, node.col_offset, msg))

        self.generic_visit(node)


def _allow_lines(source):
    allow = set()
    for i, line in enumerate(source.splitlines(), start=1):
        if LINE_OPT_OUT in line:
            allow.add(i)
    return allow


def analyze(source, filename="backend.py"):
    """Return (findings, file_opted_out). Never raises on a syntax error --
    the caller already reports those from its own ast.parse."""
    if FILE_OPT_OUT in source:
        return [], True
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return [], False
    v = _Visitor(_allow_lines(source))
    v.visit(tree)
    v.findings.sort(key=lambda f: (f.line, f.col))
    return v.findings, False
