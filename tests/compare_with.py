# -*- coding: utf-8 -*-
"""Сравнение поведения текущего дерева с любой ревизией git.

Снимает три слепка в отдельной рабочей копии указанной ревизии и в текущем
дереве, затем сверяет. Любое расхождение — изменение поведения; если правка
задумана как чисто внутренняя (рефакторинг), расхождений быть не должно.

    python tests/compare_with.py            # сравнить с HEAD
    python tests/compare_with.py v0.9.5     # сравнить с тегом/коммитом/веткой

Возвращает 0, если расхождений нет.
"""
import io
import os
import sys
import shutil
import difflib
import subprocess
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

SNAPSHOTS = [
    ("поведение", "behavior_snapshot.py", "behavior.txt", "file"),
    ("след run_job", "trace_run_job.py", "run_job.txt", "file"),
    ("рендеры строк", "render_history_rows.py", "rows", "dir"),
]


def run_snapshots(cwd, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    for name, script, out, _kind in SNAPSHOTS:
        dst = os.path.join(out_dir, out)
        r = subprocess.run([sys.executable, os.path.join(HERE, script), dst],
                           cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0:
            print("  ОШИБКА в %s:\n%s" % (name, (r.stderr or "")[-1500:]))
            return False
    return True


def read_lines(path):
    with open(path, encoding="utf-8") as f:
        return f.read().split("\n")


def hashes(dir_path):
    """Хеши кадров из rows.sha256, снятого рендерером."""
    out = {}
    with open(os.path.join(dir_path, "rows.sha256"), encoding="utf-8") as f:
        for l in f:
            if l.strip():
                h, n = l.split("  ", 1)
                out[n.strip()] = h
    return out


def main():
    ref = sys.argv[1] if len(sys.argv) > 1 else "HEAD"
    tmp = tempfile.mkdtemp(prefix="snatchr_cmp_")
    base_tree = os.path.join(tmp, "base")
    print("Сравнение текущего дерева с %s" % ref)
    r = subprocess.run(["git", "worktree", "add", "--detach", base_tree, ref],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print("не удалось получить %s:\n%s" % (ref, r.stderr))
        shutil.rmtree(tmp, ignore_errors=True)
        return 2
    try:
        print("  снимаю слепки базовой ревизии…")
        if not run_snapshots(base_tree, os.path.join(tmp, "before")):
            return 2
        print("  снимаю слепки текущего дерева…")
        if not run_snapshots(ROOT, os.path.join(tmp, "after")):
            return 2

        total = 0
        for name, _script, out, kind in SNAPSHOTS:
            a_path = os.path.join(tmp, "before", out)
            b_path = os.path.join(tmp, "after", out)
            print("\n=== %s ===" % name)
            if kind == "dir":
                a, b = hashes(a_path), hashes(b_path)
                bad = [k for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]
                print("кадров: %d / %d | расхождений: %d" % (len(a), len(b), len(bad)))
                for k in bad:
                    print("   отличается:", k)
                total += len(bad)
                continue
            a, b = read_lines(a_path), read_lines(b_path)
            diff = [x for x in difflib.unified_diff(a, b, lineterm="", n=0)
                    if x[:1] in "+-" and x[:3] not in ("+++", "---")]
            print("строк: %d / %d | расхождений: %d" % (len(a), len(b), len(diff)))
            for x in diff[:60]:
                print("   ", x[:170])
            if len(diff) > 60:
                print("    … ещё %d" % (len(diff) - 60))
            total += len(diff)

        print("\n" + "=" * 52)
        print("ИТОГО расхождений: %d" % total)
        if total == 0:
            print("Поведение идентично базовой ревизии.")
        else:
            print("Поведение изменилось — убедись, что каждое расхождение задумано.")
        return 0 if total == 0 else 1
    finally:
        subprocess.run(["git", "worktree", "remove", base_tree, "--force"],
                       cwd=ROOT, capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
