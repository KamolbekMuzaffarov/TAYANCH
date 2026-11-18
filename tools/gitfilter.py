"""Git clean filter: removes code comments from files as they enter the repository.

The working copy keeps its comments; only the committed content is stripped.
Usage (configured in .git/config): python3 tools/gitfilter.py <path>   (content on stdin, result on stdout)
Any failure exits non-zero so git refuses the commit instead of silently storing comments.
"""
from __future__ import annotations

import io
import re
import subprocess
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TS_HELPER = ROOT / "tools" / "strip_ts_comments.cjs"


def strip_python(src: str) -> str:
    lines = src.splitlines(keepends=True)
    cuts: dict[int, int] = {}
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            row, col = tok.start
            if row == 1 and tok.string.startswith("#!"):
                continue
            cuts[row] = col
    out = []
    for i, line in enumerate(lines, start=1):
        if i in cuts:
            nl = "\n" if line.endswith("\n") else ""
            kept = line[: cuts[i]].rstrip()
            if not kept:
                continue
            line = kept + nl
        out.append(line)
    result = "".join(out)
    compile(result, "<filtered>", "exec")
    return result


def strip_ts(src: str, path: str) -> str:
    proc = subprocess.run(["node", str(TS_HELPER), path], input=src.encode("utf-8"),
                          capture_output=True, cwd=str(ROOT), timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace"))
    return proc.stdout.decode("utf-8")


def strip_css(src: str) -> str:
    out = re.sub(r"/\*[\s\S]*?\*/", "", src)
    return re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", out)


def strip_html(src: str) -> str:
    return re.sub(r"[ \t]*<!--[\s\S]*?-->[ \t]*\n?", "", src)


def strip_hash_lines(src: str) -> str:
    out = []
    for i, line in enumerate(src.splitlines(keepends=True)):
        s = line.lstrip()
        if s.startswith("#") and not (i == 0 and s.startswith("#!")):
            continue
        out.append(line)
    return "".join(out)


def strip_bat(src: str) -> str:
    return "".join(l for l in src.splitlines(keepends=True)
                   if not re.match(r"\s*(rem(\s|$)|::)", l, flags=re.IGNORECASE))


def strip_sql(src: str) -> str:
    return "".join(l for l in src.splitlines(keepends=True) if not l.lstrip().startswith("--"))


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else ""
    raw = sys.stdin.buffer.read()
    try:
        src = raw.decode("utf-8")
    except UnicodeDecodeError:
        sys.stdout.buffer.write(raw)
        return
    ext = Path(path).suffix.lower()
    if ext == ".py":
        out = strip_python(src)
    elif ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
        out = strip_ts(src, path)
    elif ext == ".css":
        out = strip_css(src)
    elif ext == ".html":
        out = strip_html(src)
    elif ext in (".sh", ".command", ".ps1") or path.endswith((".gitignore", ".gitattributes")):
        out = strip_hash_lines(src)
    elif ext in (".bat", ".cmd"):
        out = strip_bat(src)
    elif ext == ".sql":
        out = strip_sql(src)
    else:
        out = src
    sys.stdout.buffer.write(out.encode("utf-8"))


if __name__ == "__main__":
    main()
