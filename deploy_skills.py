#!/usr/bin/env python3
"""把本仓库的 skills/ 目录同步到当前用户的全局 agent skills 目录。

默认目标就是 ~/.agents/skills（Windows 上是 %USERPROFILE%\\.agents\\skills），
这是 CodeBuddy / WorkBuddy / Codex 等一堆 agent 共同读取的 skill 根目录。

用法:
    python deploy_skills.py                 # 同步到 ~/.agents/skills
    python deploy_skills.py --dry-run       # 只打印会发生什么，不写盘
    python deploy_skills.py --verbose       # 连每个文件都打印
    python deploy_skills.py --target DIR    # 换一个目标目录
    python deploy_skills.py --only NAME     # 只同步指定 skill（可重复）
    python deploy_skills.py --mirror        # 目标里多出来的 skill 目录也删掉

同步规则:
  * 源：脚本同级的 skills/<name>/，必须含 SKILL.md 才算一个 skill
  * 目标：~/.agents/skills/<name>/
  * 内容不同则覆盖，内容相同则跳过（不看你本地 mtime）
  * 默认只清理「本次同步的 skill 目录内」源端已不存在的文件，
    目标里的其它 skill 一律不动；要连多余 skill 一起删请加 --mirror

退出码: 0 成功 / 1 出错（源目录缺失、写入失败等）
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

SOURCE_DIRNAME = "skills"
AGENT_DIRNAME = ".agents"
SKILL_FILE = "SKILL.md"

# 这些目录/文件不属于 skill 内容，复制和比对时忽略
IGNORED_NAMES = {".git", "__pycache__", ".DS_Store", "Thumbs.db", ".pytest_cache"}

EPILOG = """deploy the repo skills/ folder to the current user's ~/.agents/skills.
existing skills in the target that are not in this repo are left untouched
unless --mirror is given."""


@dataclass
class Change:
    """单个 skill 的同步结果。"""

    skill: str
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unchanged: int = 0

    @property
    def touched(self) -> int:
        return len(self.added) + len(self.updated) + len(self.removed)


def iter_files(root: Path) -> dict[Path, Path]:
    """返回 {相对路径: 绝对路径}，跳过忽略项。"""
    files: dict[Path, Path] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_NAMES)
        for name in sorted(filenames):
            if name in IGNORED_NAMES:
                continue
            path = Path(dirpath) / name
            if path.is_file():
                files[path.relative_to(root)] = path
    return files


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(256 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def same_content(left: Path, right: Path) -> bool:
    """先比大小再比 sha256，内容一致就跳过。"""
    try:
        if left.stat().st_size != right.stat().st_size:
            return False
        return sha256(left) == sha256(right)
    except OSError:
        return False


def remove_empty_dirs(root: Path) -> None:
    """自底向上删掉空目录（root 本身保留）。"""
    if not root.is_dir():
        return
    for dirpath, _dirnames, _filenames in os.walk(root, topdown=False):
        path = Path(dirpath)
        if path == root:
            continue
        try:
            path.rmdir()
        except OSError:
            pass


def discover_skills(source_root: Path) -> tuple[list[Path], list[str]]:
    """返回 (有效 skill 目录, 缺少 SKILL.md 的目录名)。"""
    valid: list[Path] = []
    invalid: list[str] = []
    for entry in sorted(source_root.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_dir() or entry.name in IGNORED_NAMES:
            continue
        if (entry / SKILL_FILE).is_file():
            valid.append(entry)
        else:
            invalid.append(entry.name)
    return valid, invalid


def deploy_skill(src: Path, dst: Path, *, dry_run: bool, prune: bool) -> Change:
    change = Change(skill=src.name)
    src_files = iter_files(src)
    dst_files = iter_files(dst) if dst.is_dir() else {}

    for rel, src_file in src_files.items():
        dst_file = dst / rel
        if rel in dst_files and same_content(src_file, dst_file):
            change.unchanged += 1
            continue
        (change.updated if rel in dst_files else change.added).append(str(rel))
        if dry_run:
            continue
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst_file)

    if prune:
        for rel in sorted(set(dst_files) - set(src_files)):
            change.removed.append(str(rel))
            if dry_run:
                continue
            try:
                (dst / rel).unlink()
            except FileNotFoundError:
                pass
        if not dry_run and change.removed:
            remove_empty_dirs(dst)

    return change


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="deploy_skills.py",
        description=f"deploy repo {SOURCE_DIRNAME}/ to ~/{AGENT_DIRNAME}/{SOURCE_DIRNAME}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EPILOG,
    )
    parser.add_argument(
        "--target",
        "-t",
        type=Path,
        default=Path.home() / AGENT_DIRNAME / SOURCE_DIRNAME,
        help="target dir (default: ~/.agents/skills)",
    )
    parser.add_argument("--dry-run", action="store_true", help="preview only, write nothing")
    parser.add_argument("--verbose", "-v", action="store_true", help="print every file")
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="NAME",
        help="deploy only this skill (repeatable)",
    )
    parser.add_argument(
        "--mirror",
        action="store_true",
        help="delete target skill dirs that no longer exist in the repo",
    )
    args = parser.parse_args(argv)

    source_root = Path(__file__).resolve().parent / SOURCE_DIRNAME
    target_root = args.target.expanduser().resolve()

    if not source_root.is_dir():
        print(f"error: source dir not found: {source_root}", file=sys.stderr)
        return 1

    skills, invalid = discover_skills(source_root)
    for name in invalid:
        print(f"warning: skipped dir without {SKILL_FILE}: {name}", file=sys.stderr)
    if args.only:
        wanted = {name.lower() for name in args.only}
        known = {skill.name.lower() for skill in skills}
        unknown = wanted - known
        if unknown:
            print(
                "error: skill not found in source: " + ", ".join(sorted(unknown)),
                file=sys.stderr,
            )
            return 1
        skills = [skill for skill in skills if skill.name.lower() in wanted]
    if not skills:
        print(f"error: no skill found under {source_root}", file=sys.stderr)
        return 1

    print(f"source: {source_root}")
    print(f"target: {target_root}")
    print(f"mode  : {'dry-run (no writes)' if args.dry_run else 'apply'}")
    print()

    try:
        if not args.dry_run:
            target_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"error: cannot create target dir {target_root}: {exc}", file=sys.stderr)
        return 1

    changes: list[Change] = []
    failures = 0
    for skill in skills:
        try:
            change = deploy_skill(
                skill, target_root / skill.name, dry_run=args.dry_run, prune=True
            )
        except OSError as exc:
            print(f"[!] {skill.name}: failed - {exc}", file=sys.stderr)
            failures += 1
            continue
        changes.append(change)
        status = "up to date" if change.touched == 0 else f"{change.touched} file(s) changed"
        print(f"[{skill.name}] {status}")
        if args.verbose:
            for rel in change.added:
                print(f"    + {rel}")
            for rel in change.updated:
                print(f"    ~ {rel}")
            for rel in change.removed:
                print(f"    - {rel}")
            if change.unchanged:
                print(f"    = {change.unchanged} unchanged")

    # 目标里多出来的 skill：默认保留，提示一下
    deployed = {skill.name for skill in skills}
    if target_root.is_dir():
        extras = sorted(
            entry.name
            for entry in target_root.iterdir()
            if entry.is_dir()
            and entry.name not in IGNORED_NAMES
            and entry.name not in deployed
        )
        if extras and args.mirror and not args.only:
            for name in extras:
                if args.dry_run:
                    print(f"[-] {name} would be removed (--mirror)")
                    continue
                try:
                    shutil.rmtree(target_root / name)
                    print(f"[-] {name} removed (--mirror)")
                except OSError as exc:
                    print(f"[!] {name}: remove failed - {exc}", file=sys.stderr)
                    failures += 1
        elif extras and not args.only:
            print()
            print("extra skills in target (left untouched, use --mirror to remove):")
            print("  " + ", ".join(extras))

    added = sum(len(c.added) for c in changes)
    updated = sum(len(c.updated) for c in changes)
    removed = sum(len(c.removed) for c in changes)
    unchanged = sum(c.unchanged for c in changes)
    print()
    print(
        f"done: {len(changes)} skill(s) | added {added} | updated {updated} "
        f"| removed {removed} | unchanged {unchanged}"
        + ("  (dry-run)" if args.dry_run else "")
    )
    if failures:
        print(f"{failures} skill(s) failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
