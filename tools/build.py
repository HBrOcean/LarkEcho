#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/build.py —— 跨平台打包脚本：把 Python 源码打包成单文件可执行程序。

用法
----
    python tools/build.py cli      # 只打命令行版 lark
    python tools/build.py gui      # 只打图形界面版 lark-gui（Windows）
    python tools/build.py all      # 两个都打

产物输出到 dist/ 目录。

说明
----
PyInstaller 不能交叉编译 —— 要 Windows 的 .exe 就得在 Windows 上跑本脚本。
GitHub Actions 已配置好（.github/workflows/build.yml），推到仓库即自动构建。
"""

import os
import sys

try:
    import PyInstaller.__main__ as pyi
except ImportError:
    sys.exit("[错误] 缺少 PyInstaller，请先安装：\n    pip install pyinstaller")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEP = os.pathsep          # Windows 上 ';'，Linux/macOS 上 ':' —— 自动适配
IS_WINDOWS = sys.platform.startswith("win")

ICON = os.path.join(ROOT, "assets", "icon.ico")
KEY = os.path.join(ROOT, "assets", "kugou_key.xz")
INDEX = os.path.join(ROOT, "index.html")

COMMON = [
    "--onefile",
    "--noconfirm",
    "--clean",
    "--distpath", os.path.join(ROOT, "dist"),
    "--workpath", os.path.join(ROOT, "build"),
    "--specpath", os.path.join(ROOT, "build"),
]


def _add_icon(args):
    # .ico 只对 Windows 有效
    if IS_WINDOWS and os.path.isfile(ICON):
        args += ["--icon", ICON]
    return args


def build_cli():
    """打包命令行版：解密 + 元数据 + 封面 + 歌词。"""
    print(">>> 打包 命令行版 lark ...")
    if not os.path.isfile(KEY):
        sys.exit(f"[错误] 找不到公钥文件：{KEY}")
    args = [
        os.path.join(ROOT, "lark.py"),
        *COMMON,
        "--add-data", f"{KEY}{SEP}.",
        "--name", "lark",
    ]
    pyi.run(_add_icon(args))


def build_gui():
    """打包图形界面版：pywebview 桌面窗口 + HTML 界面。"""
    print(">>> 打包 图形界面版 lark-gui ...")
    for f in (INDEX, KEY):
        if not os.path.isfile(f):
            sys.exit(f"[错误] 找不到文件：{f}")
    args = [
        os.path.join(ROOT, "app.py"),
        *COMMON,
        "--windowed",                       # 不弹控制台黑框
        "--add-data", f"{INDEX}{SEP}.",
        "--add-data", f"{KEY}{SEP}.",
        # pywebview 的平台后端是动态导入的，PyInstaller 静态分析看不到
        "--collect-all", "webview",
        "--hidden-import", "clr",
        "--name", "lark-gui",
    ]
    pyi.run(_add_icon(args))


def main():
    target = (sys.argv[1] if len(sys.argv) > 1 else "all").lower()
    if target == "cli":
        build_cli()
    elif target == "gui":
        build_gui()
    elif target == "all":
        build_cli()
        build_gui()
    else:
        sys.exit("用法: python tools/build.py [cli|gui|all]")


if __name__ == "__main__":
    main()
