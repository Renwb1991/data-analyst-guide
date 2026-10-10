#!/usr/bin/env python3
"""同步 README 里的统计数字（徽章 + 表格行数 + 正文提及）。

单一事实来源：直接数文件，不信任 README 里写着的数。

用法：
    python3 .github/scripts/update_stats.py            # 写入
    python3 .github/scripts/update_stats.py --check    # 只检查，有差异则退出码 1
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"

# 正文教程：文件 → README 表格里的展示顺序
CHAPTERS = [
    "chapter-01-sql.md",
    "chapter-01-excel.md",
    "chapter-01-business.md",
    "chapter-01-stats.md",
    "chapter-01-communication.md",
    "chapter-02.md",
    "chapter-03.md",
]


def count_lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def count_exercises(path: Path) -> int:
    """EXERCISES.md 里的题数：形如 `#### A-Q1` / `### C-Q1` 的标题。"""
    return len(re.findall(r"^#{3,4}\s+[A-E]-Q\d+", path.read_text(encoding="utf-8"), re.M))


def count_pitfalls(path: Path) -> int:
    """PITFALLS.md 里的陷阱条数：表格数据行，排除表头与分隔行。"""
    rows = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        if re.match(r"^\|[\s:|-]+\|$", line):                      # 分隔行
            continue
        if re.match(r"^\|\s*(陷阱|错误|偏差|你看到的现象)\s*\|", line):  # 表头
            continue
        rows += 1
    return rows


def badge_url(label: str, message: str, color: str) -> str:
    # shields.io：'-' 是分隔符，'_' 会被渲染成空格，内容里出现时需转义
    esc = lambda s: s.replace("-", "--").replace("_", "__")
    return (
        f"https://img.shields.io/badge/"
        f"{quote(esc(label))}-{quote(esc(message))}-{color}"
    )


def human_k(n: int) -> str:
    """10239 → '10.2k'；小于 1000 时直接返回整数。"""
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def substitute(text: str, pattern: str, repl: str, what: str) -> str:
    """替换且必须命中，否则报错——README 被改写时要立刻暴露，而不是静默跳过。"""
    new, n = re.subn(pattern, repl, text, count=1)
    if n == 0:
        raise SystemExit(
            f"✗ 未匹配到「{what}」的模式，README 结构可能已改动。\n"
            f"  pattern: {pattern}\n"
            f"  请同步更新 {Path(__file__).relative_to(ROOT)}"
        )
    return new


def main() -> int:
    check_only = "--check" in sys.argv

    # ---------- 统计 ----------
    chapter_lines = {f: count_lines(ROOT / f) for f in CHAPTERS}
    prose = sum(chapter_lines.values())
    n_chapters = len(CHAPTERS)
    n_ex = count_exercises(ROOT / "EXERCISES.md")
    n_pf = count_pitfalls(ROOT / "PITFALLS.md")

    print(f"正文 {n_chapters} 篇 {prose:,} 行 | 练习 {n_ex} 题 | 陷阱 {n_pf} 条")

    text = original = README.read_text(encoding="utf-8")

    # ---------- 1. 三个徽章 ----------
    for alt, url in (
        ("正文", badge_url("正文", f"{human_k(prose)} 行", "blue")),
        ("练习", badge_url("练习", f"{n_ex} 题", "green")),
        ("陷阱", badge_url("陷阱", f"{n_pf} 条", "orange")),
    ):
        text = substitute(
            text,
            rf"(\[!\[{alt}\]\()https://img\.shields\.io/badge/[^)]*(\)\])",
            lambda m, u=url: m.group(1) + u + m.group(2),
            f"{alt}徽章",
        )

    # ---------- 2. 正文教程表格里的行数 ----------
    for fname, lines in chapter_lines.items():
        text = substitute(
            text,
            rf"(\]\(\./{re.escape(fname)}\)\s*\|[^|]*\|\s*)[\d,]+(\s*行\s*\|)",
            lambda m, n=lines: f"{m.group(1)}{n}{m.group(2)}",
            f"{fname} 行数",
        )

    # ---------- 3. 目录与正文里的提及 ----------
    for pattern, repl, what in (
        (r"(—— )\d+( 篇，)[\d,]+( 行)",
         lambda m: f"{m.group(1)}{n_chapters}{m.group(2)}{prose:,}{m.group(3)}", "目录·正文篇数行数"),
        (r"(—— )\d+( 题，全部带完整答案)",
         lambda m: f"{m.group(1)}{n_ex}{m.group(2)}", "目录·练习题数"),
        (r"(—— )\d+( 条，支持症状倒查)",
         lambda m: f"{m.group(1)}{n_pf}{m.group(2)}", "目录·陷阱条数"),
        (r"(做\[练习与答案库\]\(\./EXERCISES\.md\)，)\d+( 题带完整答案)",
         lambda m: f"{m.group(1)}{n_ex}{m.group(2)}", "导航表·练习题数"),
        (r"(\*\*)\d+( 题\*\*跨节整合练习)",
         lambda m: f"{m.group(1)}{n_ex}{m.group(2)}", "正文·练习题数"),
        (r"(全书 \*\*)\d+( 条陷阱\*\*)",
         lambda m: f"{m.group(1)}{n_pf}{m.group(2)}", "正文·陷阱条数"),
    ):
        text = substitute(text, pattern, repl, what)

    # ---------- 输出 ----------
    if text == original:
        print("✓ README 统计数字已是最新")
        return 0

    if check_only:
        print("✗ README 统计数字已过期，请运行 python3 .github/scripts/update_stats.py")
        return 1

    README.write_text(text, encoding="utf-8")
    print("✓ README 已更新")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
