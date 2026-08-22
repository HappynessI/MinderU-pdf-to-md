#!/usr/bin/env python3
"""Store a Markdown translation API key without printing it."""

from __future__ import annotations

import argparse
import getpass
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

from translate_markdown import KEYCHAIN_SERVICE, TranslationError, read_api_key_file


def store_in_keychain(api_key: str, account: str) -> None:
    if platform.system() != "Darwin" or not shutil.which("security"):
        raise TranslationError("当前系统没有可用的 macOS Keychain security 命令")
    completed = subprocess.run(
        [
            "security",
            "add-generic-password",
            "-U",
            "-a",
            account,
            "-s",
            KEYCHAIN_SERVICE,
            "-w",
            api_key,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise TranslationError(f"写入 macOS Keychain 失败：{completed.stderr.strip()}")


def store_in_file(api_key: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, (api_key + "\n").encode("utf-8"))
    finally:
        os.close(descriptor)
    os.chmod(path, 0o600)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="安全配置 Markdown 翻译 API Key")
    parser.add_argument("--source-file", type=Path, help="从现有文本或 Markdown 文件读取密钥")
    parser.add_argument("--storage", choices=["auto", "keychain", "file"], default="auto")
    parser.add_argument("--account", default=getpass.getuser(), help="Keychain 账户名")
    parser.add_argument(
        "--output-file",
        type=Path,
        default=Path.home() / ".config" / "mineru-pdf-to-md" / "translation-token",
        help="非 macOS 或选择 file 时的凭据文件",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        api_key = (
            read_api_key_file(args.source_file.expanduser())
            if args.source_file
            else getpass.getpass("Markdown 翻译 API Key: ").strip()
        )
        if len(api_key) < 12 or any(character.isspace() for character in api_key):
            raise TranslationError("API Key 格式无效")
        storage = args.storage
        if storage == "auto":
            storage = (
                "keychain"
                if platform.system() == "Darwin" and shutil.which("security")
                else "file"
            )
        if storage == "keychain":
            store_in_keychain(api_key, args.account)
            print(f"已把 API Key 保存到 macOS Keychain 服务：{KEYCHAIN_SERVICE}")
        else:
            output = args.output_file.expanduser()
            store_in_file(api_key, output)
            print(f"已把 API Key 保存到权限为 600 的文件：{output}")
        return 0
    except TranslationError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
