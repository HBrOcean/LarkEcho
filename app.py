#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app.py —— KGM 转换工具 · 桌面图形版

用 pywebview 把 index.html 界面装进一个原生桌面窗口，双击 exe 即可使用。
核心转换逻辑复用同目录的 kgm2all.py（解密 / 元数据 / 封面 / 歌词）。

依赖
----
    pip install pywebview mutagen
（其余为标准库；kgm2all.py 与 kugou_key.xz 需在同目录）

打包
----
    Windows 上双击 build_exe.bat 一键打包成 dist\\kgm2gui.exe
"""

import json
import os
import sys
import threading
import traceback

try:
    import webview
except ImportError:
    sys.exit("[错误] 缺少 pywebview，请先安装：\n    pip install pywebview")

import kgm2all as K


# ---------------------------------------------------------------------------
# 路径工具（兼容 PyInstaller 打包）
# ---------------------------------------------------------------------------

def app_dir():
    """程序所在目录（打包后为 exe 所在目录）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(name):
    """资源文件路径：打包后先找 PyInstaller 解包目录，再找程序目录。"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        p = os.path.join(base, name)
        if os.path.isfile(p):
            return p
    return os.path.join(app_dir(), name)


PUB_KEY = None      # 解密公钥
M = None            # mutagen 模块集
window = None       # pywebview 窗口


# ---------------------------------------------------------------------------
# 转换核心
# ---------------------------------------------------------------------------

def convert_one(path, out_dir, opts):
    """转换单个文件，返回 (输出路径, 结果信息 dict)。"""
    data = K.decrypt_file(path, PUB_KEY)
    ext = K.detect_extension(data)

    if out_dir:
        base = K.strip_suffixes(os.path.basename(path))
        out_path = os.path.join(out_dir, base + ext)
    else:
        out_path = K.make_output_path(path, ext)

    with open(out_path, "wb") as f:
        f.write(data)

    info = {"title": "", "artist": "", "album": "", "year": "",
            "cover": False, "lyric": False, "source": ""}

    if opts.get("no_meta") or M is None:
        return out_path, info

    # 元数据 + 封面（在线查询按需开关）
    meta = K.enrich_metadata(
        out_path, path, ext, M, {}, print_only=False,
        priority="meta", no_online=bool(opts.get("no_online")),
    )
    info.update(
        title=meta["title"], artist=meta["artist"], album=meta["album"],
        year=meta["date"], cover=bool(meta["cover"]),
        source=meta.get("online_source", ""),
    )

    # 歌词：同名 .lrc + 嵌入音频
    if not opts.get("no_lyric") and meta["title"]:
        try:
            dur = K._audio_duration_ms(out_path, ext, M)
            lyric = K.fetch_lyric(
                meta["title"], singer=meta["artist"],
                expect_duration_ms=dur,
                expect_title=meta["title"], expect_artist=meta["artist"],
            )
            if lyric:
                lrc_path = os.path.splitext(out_path)[0] + ".lrc"
                with open(lrc_path, "w", encoding="utf-8") as f:
                    f.write(lyric["lrc"] + "\n")
                K.embed_lyrics(out_path, ext, lyric["lrc"], M)
                info["lyric"] = True
        except Exception:
            pass  # 歌词失败不影响音频

    return out_path, info


# ---------------------------------------------------------------------------
# 暴露给前端的 API
# ---------------------------------------------------------------------------

class Api:
    def _push(self, payload):
        """把进度推给前端 JS（onProgress 函数）。"""
        try:
            window.evaluate_js("onProgress(" + json.dumps(payload, ensure_ascii=False) + ")")
        except Exception:
            pass

    def pick_files(self):
        """弹出文件选择框，返回所选文件路径列表。"""
        try:
            result = window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=True,
                file_types=("KGM 文件 (*.kgm;*.vpr)", "所有文件 (*.*)"),
            )
        except Exception:
            return []
        return list(result) if result else []

    def pick_folder(self):
        """弹出文件夹选择框，返回目录路径。"""
        try:
            result = window.create_file_dialog(webview.FOLDER_DIALOG)
        except Exception:
            return ""
        if not result:
            return ""
        if isinstance(result, (list, tuple)):
            return result[0] if result else ""
        return str(result)

    def start_convert(self, files, out_dir, opts):
        """启动后台转换（立刻返回，进度通过 onProgress 推送）。"""
        threading.Thread(
            target=self._run, args=(files or [], out_dir or "", opts or {}),
            daemon=True,
        ).start()
        return {"ok": True}

    def _run(self, files, out_dir, opts):
        total = len(files)
        ok = 0
        if out_dir:
            try:
                os.makedirs(out_dir, exist_ok=True)
            except Exception:
                out_dir = ""

        for i, path in enumerate(files, 1):
            name = os.path.basename(path)
            self._push({"type": "start", "index": i, "total": total, "name": name})
            try:
                out_path, info = convert_one(path, out_dir, opts)
                ok += 1
                payload = {"type": "done", "index": i, "total": total,
                           "name": name, "out": os.path.basename(out_path)}
                payload.update(info)
                self._push(payload)
            except Exception as e:
                msg = str(e) or e.__class__.__name__
                self._push({"type": "fail", "index": i, "total": total,
                            "name": name, "error": msg})

        self._push({"type": "all_done", "ok": ok, "total": total})


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main():
    global PUB_KEY, M, window

    PUB_KEY = K.load_pub_key(app_dir())
    M = K._mutagen()

    api = Api()
    window = webview.create_window(
        "KGM 转换工具",
        resource_path("index.html"),
        js_api=api,
        width=880,
        height=800,
        min_size=(640, 560),
    )
    webview.start()


if __name__ == "__main__":
    main()
