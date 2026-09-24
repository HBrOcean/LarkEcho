#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lark.py —— 酷狗音乐 KGM 解密 + 元数据 + 封面 + 歌词（一体化单文件版）。

把原来拆分的 kgm2F / kgm2M / kgm2O / kgm2L / kgm2F_L 合并成这一个文件，
一条命令完成「解密 → 补元数据 → 补封面 → 查歌词」，歌词会：
  - 输出同名的 .lrc 歌词文件
  - 同时嵌入到音频文件自身的标签里（FLAC 的 LYRICS / MP3 的 USLT）

核心能力
--------
1. KGM 解密：逐字节异或 + 位移混淆（公开逆向算法），自动识别真实音频格式。
2. 元数据：本地标签 / 文件名 / 手动参数 优先，在线查询兜底。
   文件名「歌手 - 歌名」和「歌名 - 歌手」两种顺序都能自动识别（双向搜索裁决）。
3. 在线查询：优先酷狗音乐（歌名/专辑/封面/年份最全），失败回退 QQ / 网易云 / iTunes。
4. 封面：音频流已有封面就直接用，没有才在线下载。
5. 歌词：检索酷狗 KRC 逐字歌词，对比「时长 + 歌手 + 歌名」匹配后输出。

依赖
----
解密仅需 Python 标准库 + 公钥文件 kugou_key.xz（放同目录）。
元数据 / 封面 / 歌词写入需要 mutagen：
    pip install mutagen

用法
----
    python3 lark.py 周杰伦-晴天.kgm            # 解密 + 元数据 + 封面 + 歌词
    python3 lark.py -r ./音乐目录/             # 递归处理整个目录
    python3 lark.py 晴天.kgm --album 叶惠美 --year 2003 --cover c.jpg
    python3 lark.py 歌曲.kgm --no-lyric        # 不处理歌词
    python3 lark.py 歌曲.kgm --no-online       # 禁用在线查询，仅本地补全
    python3 lark.py -d 歌曲.kgm                # 解密成功后删除原加密文件

打包成 exe 后（双击即用）
------------------------
    - 直接双击 exe        ：弹窗选文件夹 → 自动处理其中的 .kgm / .vpr
    - 把文件拖到 exe 上   ：直接处理拖进来的文件（可多个）
    - 结束后窗口会停住等按键，不会一闪而过

    打包方法见随附的 build_exe.bat（Windows 上一键打包）。

公钥文件获取
------------
    https://github.com/ghtz08/kugou-kgm-decoder/raw/main/assets/kugou_key.xz
"""

import argparse
import base64
import json
import lzma
import os
import re
import sys
import urllib.parse
import urllib.request
import zlib
from datetime import datetime

# Windows 控制台默认编码不是 UTF-8，放宽输出错误处理，
# 避免非 ASCII 字符（中文）print 时抛 UnicodeEncodeError。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:
        pass

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# KRC 歌词解密密钥
KRC_KEY = [64, 71, 97, 119, 94, 50, 116, 71, 81, 54, 49, 45, 206, 210, 110, 105]
# 歌词时长匹配容差（毫秒）
DURATION_TOLERANCE_MS = 5000

# KGM 文件头固定长度
HEADER_LEN = 1024

# 魔数（28 字节）
MAGIC_HEADER = bytes.fromhex(
    "7cd532eb86027f4ba8afa68e0fff9914"
    "000400000300000001000000"
)

# 272 字节固定修正表
PUB_KEY_MEND = bytes.fromhex(
    "b8d53db2e9af788c8333715176a0"
    "cd372f3e358da9be98b7e78c22ce"
    "5a61df686989fea5b6dea977fcc8"
    "bdbde56d3e5a36ef694ebee1e966"
    "1cf3d902b6f2129b44d06fb93589"
    "b6466d73820669c1edd785c230df"
    "a262be792d62623d0d7ebe488923"
    "02a0e4d57551320253fd163a213b"
    "160fc3b2bbb3e2ba3a3d13ecf601"
    "4584a5700f93490c64cd31d5cc4c"
    "07019e001a2390bf881e3baba63e"
    "c47347107e3b5ebce30084ff09d4"
    "e0890f5b58704ffb65d85c531bd3"
    "c8c6bfef98b0504f0feae583588c"
    "282c8467cdd09e47db2750caf463"
    "63e8977f1b4b0cc2c1214ccc58f5"
    "9452a3f3d3e068f40023f35e0a7b"
    "93ddab12b213e884d7a79f0f324c"
    "551d043652dc03f3f94e42e93d61"
    "ef7cb6b39350"
)

assert len(PUB_KEY_MEND) == 272, "PUB_KEY_MEND 长度应为 272 字节"


# ===========================================================================
# mutagen 可选依赖（元数据 / 封面 / 歌词写入需要）
# ===========================================================================

def _mutagen():
    """返回 mutagen 相关类的 dict；未安装则返回 None。"""
    try:
        from mutagen.flac import FLAC, Picture
        from mutagen.id3 import (ID3, TIT2, TPE1, TALB, TPE2, TDRC, TCON,
                                 APIC, USLT)
        from mutagen.mp3 import MP3
    except ImportError:
        return None
    return {
        "FLAC": FLAC, "Picture": Picture, "ID3": ID3, "TIT2": TIT2,
        "TPE1": TPE1, "TALB": TALB, "TPE2": TPE2, "TDRC": TDRC,
        "TCON": TCON, "APIC": APIC, "USLT": USLT, "MP3": MP3,
    }


# ===========================================================================
# 第 1 部分：KGM 解密
# ===========================================================================

def _app_dir():
    """返回程序所在目录（打包成 exe 后是 exe 自身所在目录）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def find_key_file(script_dir):
    """按优先级查找 kugou_key.xz 文件路径。"""
    candidates = []
    # PyInstaller 打包后：优先从解包临时目录找（--add-data 打进 exe 的公钥）
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(os.path.join(meipass, "kugou_key.xz"))
    env = os.environ.get("KUGOU_KEY")
    if env:
        candidates.append(env)
    for b in (script_dir, os.getcwd()):
        candidates.append(os.path.join(b, "kugou_key.xz"))
        candidates.append(os.path.join(b, "assets", "kugou_key.xz"))
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return ""


def load_pub_key(script_dir):
    path = find_key_file(script_dir)
    if not path:
        sys.exit(
            "[错误] 找不到公钥文件 kugou_key.xz。\n"
            "请从以下地址下载后放到脚本同目录（或 assets/ 目录），"
            "或用环境变量 KUGOU_KEY 指定路径：\n"
            "  https://github.com/ghtz08/kugou-kgm-decoder/raw/main/assets/kugou_key.xz"
        )
    with open(path, "rb") as f:
        raw = f.read()
    try:
        data = lzma.decompress(raw)
    except Exception as e:
        sys.exit(f"[错误] 解压公钥文件失败：{e}")
    if len(data) != 73155904:
        sys.exit(f"[错误] 公钥大小异常（{len(data)} 字节），应为 73155904 字节。")
    return data


def decrypt_audio(encrypted, own_key, pub_key):
    """逐字节解密音频数据。encrypted 为去掉 1024 字节头后的密文。"""
    n = len(encrypted)
    out = bytearray(n)
    mend = PUB_KEY_MEND
    for i in range(n):
        own = own_key[i % 17] ^ encrypted[i]
        own ^= (own & 0x0F) << 4
        pub = mend[i % 272] ^ pub_key[i // 16]
        pub ^= (pub & 0x0F) << 4
        out[i] = (own ^ pub) & 0xFF
    return bytes(out)


def decrypt_file(path, pub_key):
    """读取并校验 KGM 文件，返回解密后的完整音频字节。"""
    with open(path, "rb") as f:
        raw = f.read()
    if len(raw) < HEADER_LEN:
        raise ValueError("文件太小，不是合法的 KGM 文件")
    header = raw[:HEADER_LEN]
    if not header.startswith(MAGIC_HEADER):
        raise ValueError("魔数不匹配，不是酷狗 KGM 文件（或文件已损坏）")
    own_key = header[0x1C:0x2C] + b"\x00"
    encrypted = raw[HEADER_LEN:]
    return decrypt_audio(encrypted, own_key, pub_key)


def detect_extension(data):
    """根据解密后数据的文件头，返回对应的音频扩展名。"""
    if data.startswith(b"fLaC"):
        return ".flac"
    if data.startswith(b"ID3") or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return ".mp3"
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return ".wav"
    if data.startswith(b"OggS"):
        return ".ogg"
    if data.startswith(b"ftyp"):
        return ".m4a"
    if data.startswith(b"\xff\xf1") or data.startswith(b"ADIF") or data.startswith(b"ADTS"):
        return ".aac"
    return ".mp3"


def make_output_path(path, ext):
    """去掉加密后缀 (.kgm/.vpr) 和多余音频后缀，再拼上真实扩展名。"""
    audio_exts = (".flac", ".mp3", ".wav", ".ogg", ".m4a", ".aac")
    base = path
    lower = base.lower()
    while True:
        changed = False
        for s in (".kgm", ".vpr"):
            if lower.endswith(s):
                base = base[:-len(s)]
                lower = base.lower()
                changed = True
        for s in audio_exts:
            if lower.endswith(s):
                base = base[:-len(s)]
                lower = base.lower()
                changed = True
        if not changed:
            break
    return base + ext


def _audio_duration_ms(path, ext, M):
    """获取音频文件实际时长（毫秒），失败返回 None。"""
    try:
        if ext == ".flac":
            return int(M["FLAC"](path).info.length * 1000)
        if ext == ".mp3":
            return int(M["MP3"](path).info.length * 1000)
    except Exception:
        pass
    return None


# ===========================================================================
# 第 2 部分：文件名解析
# ===========================================================================

def strip_suffixes(name):
    """去掉文件名中的 .kgm/.vpr 及音频扩展名，返回基础名。"""
    audio_exts = (".flac", ".mp3", ".wav", ".ogg", ".m4a", ".aac")
    lower = name.lower()
    while True:
        changed = False
        for s in (".kgm", ".vpr"):
            if lower.endswith(s):
                name = name[:-len(s)]
                lower = name.lower()
                changed = True
        for s in audio_exts:
            if lower.endswith(s):
                name = name[:-len(s)]
                lower = name.lower()
                changed = True
        if not changed:
            break
    return name


def parse_filename(path):
    """从文件名解析出 (左, 右)。注意：顺序未知，可能是「歌手 - 歌名」或「歌名 - 歌手」。

    返回 (left, right)。顺序由后面的双向搜索裁决。
    无分隔符时返回 (None, 基础名)。
    """
    base = strip_suffixes(os.path.basename(path))
    seps = (" - ", "－", "—", "-", "–")
    for sep in seps:
        if sep in base:
            left, right = base.split(sep, 1)
            left, right = left.strip(), right.strip()
            if left and right:
                return left, right
    return None, base.strip()


# ===========================================================================
# 第 3 部分：元数据读写
# ===========================================================================

def _id3_text(tags, fid):
    f = tags.get(fid)
    if f is not None:
        try:
            return str(f.text[0])
        except Exception:
            return ""
    return ""


def read_existing_meta(path, ext, M):
    """读取音频文件里已有的元数据。返回 dict，缺失字段为空。"""
    meta = {
        "title": "", "artist": "", "album": "", "albumartist": "",
        "date": "", "genre": "", "cover": None, "cover_mime": "",
    }
    try:
        if ext == ".flac":
            audio = M["FLAC"](path)
            meta["title"] = (audio.get("title") or [""])[0]
            meta["artist"] = (audio.get("artist") or [""])[0]
            meta["album"] = (audio.get("album") or [""])[0]
            meta["albumartist"] = (audio.get("albumartist") or [""])[0]
            meta["date"] = (audio.get("date") or audio.get("year") or [""])[0]
            meta["genre"] = (audio.get("genre") or [""])[0]
            if audio.pictures:
                p = audio.pictures[0]
                meta["cover"] = p.data
                meta["cover_mime"] = p.mime or ""
        elif ext == ".mp3":
            audio = M["MP3"](path, ID3=M["ID3"])
            if audio.tags is not None:
                meta["title"] = _id3_text(audio.tags, "TIT2")
                meta["artist"] = _id3_text(audio.tags, "TPE1")
                meta["album"] = _id3_text(audio.tags, "TALB")
                meta["albumartist"] = _id3_text(audio.tags, "TPE2")
                meta["date"] = _id3_text(audio.tags, "TDRC") or _id3_text(audio.tags, "TYER")
                meta["genre"] = _id3_text(audio.tags, "TCON")
                apic = audio.tags.getall("APIC")
                if apic:
                    meta["cover"] = apic[0].data
                    meta["cover_mime"] = apic[0].mime or ""
    except Exception as e:
        print(f"  [提示] 读取元数据时出现问题（不影响转换）：{e}")
    return meta


def _set_vorbis(audio, key, value):
    if value:
        audio[key] = value


def _set_id3(tags, fid, frame_cls, value):
    tags.delall(fid)
    if value:
        tags.add(frame_cls(encoding=3, text=value))


def write_meta(path, ext, meta, M, replace_cover):
    """把 meta 写入音频文件。replace_cover 为 True 时才替换封面。"""
    if ext == ".flac":
        audio = M["FLAC"](path)
        _set_vorbis(audio, "title", meta["title"])
        _set_vorbis(audio, "artist", meta["artist"])
        _set_vorbis(audio, "album", meta["album"])
        _set_vorbis(audio, "albumartist", meta["albumartist"])
        _set_vorbis(audio, "date", meta["date"])
        _set_vorbis(audio, "genre", meta["genre"])
        if replace_cover and meta["cover"]:
            audio.clear_pictures()
            pic = M["Picture"]()
            pic.type = 3
            pic.mime = meta["cover_mime"] or "image/jpeg"
            pic.data = meta["cover"]
            audio.add_picture(pic)
        audio.save()
    elif ext == ".mp3":
        audio = M["MP3"](path, ID3=M["ID3"])
        if audio.tags is None:
            audio.add_tags()
        tags = audio.tags
        _set_id3(tags, "TIT2", M["TIT2"], meta["title"])
        _set_id3(tags, "TPE1", M["TPE1"], meta["artist"])
        _set_id3(tags, "TALB", M["TALB"], meta["album"])
        _set_id3(tags, "TPE2", M["TPE2"], meta["albumartist"])
        _set_id3(tags, "TDRC", M["TDRC"], meta["date"])
        _set_id3(tags, "TCON", M["TCON"], meta["genre"])
        if replace_cover and meta["cover"]:
            tags.delall("APIC")
            tags.add(M["APIC"](encoding=3, mime=meta["cover_mime"] or "image/jpeg",
                               type=3, desc="Cover", data=meta["cover"]))
        audio.save()
    else:
        print(f"  [提示] 暂不支持为 {ext} 格式写元数据，仅保留原标签。")


def detect_image_mime(data):
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


# ===========================================================================
# 第 4 部分：在线搜索（酷狗优先 + 双向顺序裁决）
# ===========================================================================

def _http_json(url, params=None, headers=None, timeout=15):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    hdrs = {"User-Agent": UA}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _http_bytes(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _norm(s):
    return (s or "").strip().lower()


def _clean_text(s):
    """归一化文本用于匹配：小写、去括号/连字符修饰、去修饰词、去标点空格。"""
    s = (s or "").strip().lower()
    if not s:
        return ""
    s = re.sub(r"[\(\[（【].*?[\)\]）】]", "", s)
    s = re.split(r"\s*[-–—~]\s*", s)[0]
    for w in ("live", "remix", "cover", "bootleg", "伴奏", "翻唱", "纯音乐",
              "inst", "instrumental", "acoustic", "demo", "现场", "演唱会",
              "完整版", "片段", "混音", "串烧"):
        s = re.sub(re.escape(w), "", s)
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "", s, flags=re.UNICODE)
    return s


def _is_title_match(song_name, title):
    """歌名硬约束：归一化后必须相等。"""
    a = _clean_text(title)
    b = _clean_text(song_name)
    if not a or not b:
        return False
    return a == b


def _is_singer_match(singers, artist):
    """歌手列表里是否存在与 artist 匹配的（归一化后相等或互相包含）。"""
    a = _clean_text(artist)
    if not a:
        return False
    for sg in singers:
        b = _clean_text(sg)
        if not b:
            continue
        if a == b or a in b or b in a:
            return True
    return False


def _match_score(song_name, title, singers, artist):
    """匹配评分：歌名硬约束 + 歌手加分。返回 -1 表示歌名不匹配（直接淘汰）。"""
    if not _is_title_match(song_name, title):
        return -1
    score = 0
    if _norm(title) == _norm(song_name):
        score += 3
    else:
        score += 2
    if _is_singer_match(singers, artist):
        score += 3
    return score


def _search_kugou(title, artist, timeout):
    """酷狗音乐：歌名/歌手/专辑名/封面直接返回，年份查专辑详情。"""
    keyword = f"{artist} {title}".strip()
    data = _http_json(
        "http://ioscdn.kugou.com/api/v3/search/song",
        {"keyword": keyword, "page": 1, "pagesize": 10, "showtype": 10,
         "plat": 2, "version": 7910, "tag": 1, "correct": 1, "privilege": 1, "sver": 5},
        timeout=timeout,
    )
    infos = data.get("data", {}).get("info") or []
    if not infos:
        return None

    best, best_score = None, -1
    for info in infos:
        song_name = info.get("songname", "")
        singers = [x for x in re.split(r"[,、/;|&]+", info.get("singername", "")) if x.strip()]
        score = _match_score(song_name, title, singers, artist)
        if score < 0:
            continue
        if score > best_score:
            best_score, best = score, info
    if best is None:
        return None

    album = best.get("album_name", "")
    album_id = best.get("album_id", "")
    tp = best.get("trans_param") or {}
    cover_url = (tp.get("union_cover") or "").replace("{size}", "480")
    year = ""
    if album_id:
        try:
            detail = _http_json(
                "http://mobilecdn.kugou.com/api/v3/album/info",
                {"albumid": album_id, "plat": 0},
                timeout=timeout,
            )
            pt = (detail.get("data") or {}).get("publishtime", "")
            if pt:
                year = str(pt)[:4]
        except Exception:
            year = ""
    return {
        "title": best.get("songname", title),
        "artist": best.get("singername", "") or artist,
        "album": album,
        "year": year,
        "cover_url": cover_url,
        "source": "kugou",
    }


def _search_qq(title, artist, timeout):
    keyword = f"{artist} {title}".strip()
    data = _http_json(
        "https://c.y.qq.com/soso/fcgi-bin/client_search_cp",
        {"w": keyword, "format": "json", "n": 10, "p": 1},
        timeout=timeout,
    )
    songs = data.get("data", {}).get("song", {}).get("list", [])
    if not songs:
        return None

    best, best_score = None, -1
    for s in songs:
        singers = [sg.get("name", "") for sg in s.get("singer", [])]
        score = _match_score(s.get("songname", ""), title, singers, artist)
        if score < 0:
            continue
        if score > best_score:
            best_score, best = score, s
    if best is None:
        return None

    album = best.get("albumname", "")
    albummid = best.get("albummid", "")
    pubtime = best.get("pubtime", 0)
    year = ""
    if pubtime:
        try:
            year = str(datetime.fromtimestamp(pubtime).year)
        except Exception:
            year = ""
    cover_url = ""
    if albummid:
        cover_url = f"https://y.gtimg.cn/music/photo_new/T002R300x300M000{albummid}.jpg"
    return {
        "title": best.get("songname", title),
        "artist": best.get("singer", [{}])[0].get("name", "") or artist,
        "album": album,
        "year": year,
        "cover_url": cover_url,
        "source": "qq",
    }


def _search_netease(title, artist, timeout):
    keyword = f"{artist} {title}".strip()
    data = _http_json(
        "https://music.163.com/api/search/get/web",
        {"s": keyword, "type": 1, "limit": 10, "offset": 0},
        headers={"Referer": "https://music.163.com"},
        timeout=timeout,
    )
    songs = data.get("result", {}).get("songs", [])
    if not songs:
        return None

    best, best_score = None, -1
    for s in songs:
        singers = [a.get("name", "") for a in s.get("artists", [])]
        score = _match_score(s.get("name", ""), title, singers, artist)
        if score < 0:
            continue
        if score > best_score:
            best_score, best = score, s
    if best is None:
        return None

    album = best.get("album", {}).get("name", "")
    publish = best.get("album", {}).get("publishTime", 0)
    year = ""
    if publish:
        try:
            year = str(datetime.fromtimestamp(publish / 1000).year)
        except Exception:
            year = ""
    cover_url = ""
    album_id = best.get("album", {}).get("id")
    if album_id:
        try:
            detail = _http_json(
                f"https://music.163.com/api/album/{album_id}",
                headers={"Referer": "https://music.163.com"},
                timeout=timeout,
            )
            cover_url = detail.get("album", {}).get("picUrl", "") or ""
        except Exception:
            cover_url = ""
    return {
        "title": best.get("name", title),
        "artist": best.get("artists", [{}])[0].get("name", "") or artist,
        "album": album,
        "year": year,
        "cover_url": cover_url,
        "source": "netease",
    }


def _search_itunes(title, artist, timeout):
    keyword = f"{artist} {title}".strip()
    data = _http_json(
        "https://itunes.apple.com/search",
        {"term": keyword, "media": "music", "limit": 10},
        timeout=timeout,
    )
    results = data.get("results", [])
    if not results:
        return None

    best, best_score = None, -1
    for r in results:
        score = _match_score(r.get("trackName", ""), title, [r.get("artistName", "")], artist)
        if score < 0:
            continue
        if score > best_score:
            best_score, best = score, r
    if best is None:
        return None

    release = best.get("releaseDate", "") or ""
    year = release[:4] if len(release) >= 4 else ""
    cover_url = (best.get("artworkUrl100", "") or "").replace("100x100", "600x600")
    return {
        "title": best.get("trackName", title),
        "artist": best.get("artistName", artist),
        "album": best.get("collectionName", ""),
        "year": year,
        "cover_url": cover_url,
        "source": "itunes",
    }


def search_metadata(title, artist, timeout=15):
    """按顺序尝试酷狗 / QQ / 网易云 / iTunes，返回元数据 dict 或 None。"""
    if not title and not artist:
        return None
    for func in (_search_kugou, _search_qq, _search_netease, _search_itunes):
        try:
            r = func(title, artist, timeout)
        except Exception:
            r = None
        # 给了歌手但「歌手+歌名」搜不到，或结果歌手与输入不符，说明歌手字段可能不准，
        # 改用纯歌名补搜，取更热门/正确的版本。
        if artist and title and (not r or not _is_singer_match([r.get("artist", "")], artist)):
            try:
                r2 = func(title, "", timeout)
            except Exception:
                r2 = None
            if r2:
                r = r2
        if r:
            return r
    return None


def fetch_cover(cover_url, timeout=15):
    if not cover_url:
        return None
    try:
        return _http_bytes(cover_url, timeout=timeout)
    except Exception:
        return None


def search_bidirectional(token_a, token_b, timeout=15):
    """两个 token 顺序未知（哪个是歌手、哪个是歌名不确定），双向搜索裁决。

    返回 (online, artist, title)：
    - online 为搜索到的元数据 dict 或 None；
    - artist/title 是根据命中方向纠正后的正确顺序。
    若两个方向都搜不到，保持传入顺序（token_a 视为歌手、token_b 视为歌名）。

    裁决依据是「返回结果的歌手是否与假设的歌手 token 一致」——因为歌名硬约束
    下两个方向都可能命中（歌手名恰好也是某首歌名时），只有歌手一致性才能区分。
    """
    # 正向：假设 token_a=歌手, token_b=歌名
    r1 = search_metadata(token_b, token_a, timeout)
    if r1 and _is_singer_match([r1.get("artist", "")], token_a):
        return r1, token_a, token_b
    # 正向不理想（无结果或歌手不符），反向再试：假设 token_a=歌名, token_b=歌手
    r2 = None
    if token_a and token_b:
        r2 = search_metadata(token_a, token_b, timeout)
    if r2 and _is_singer_match([r2.get("artist", "")], token_b):
        return r2, token_b, token_a
    # 两个方向歌手都不匹配（或歌手字段本身不准），退回有结果的那个
    if r2 and not r1:
        return r2, token_b, token_a
    return r1, token_a, token_b


# ===========================================================================
# 第 5 部分：歌词检索（酷狗 KRC）
# ===========================================================================

def _text_match(a, b):
    """返回两个文本匹配度：2=完全相同，1=互相包含，0=不匹配。"""
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0
    if a == b:
        return 2
    if a in b or b in a:
        return 1
    return 0


def search_song(keyword, expect_singer=None, timeout=15):
    data = _http_json(
        "http://ioscdn.kugou.com/api/v3/search/song",
        {"keyword": keyword, "page": 1, "pagesize": 10, "showtype": 10,
         "plat": 2, "version": 7910, "tag": 1, "correct": 1, "privilege": 1, "sver": 5},
        timeout=timeout,
    )
    infos = data.get("data", {}).get("info") or []
    if not infos:
        return None

    def extract(info):
        return (info.get("hash", ""), int(info.get("duration", 0)) * 1000,
                info.get("album_audio_id", ""), info.get("songname", ""),
                info.get("singername", ""))

    if expect_singer:
        for info in infos:
            if _text_match(info.get("singername", ""), expect_singer) > 0:
                return extract(info)
        return None
    return extract(infos[0])


def search_lyrics(keyword, hash_, duration_ms, album_audio_id, timeout=15):
    data = _http_json(
        "http://krcs.kugou.com/search",
        {"ver": 1, "man": "no", "client": "pc", "keyword": keyword,
         "duration": duration_ms, "hash": hash_,
         "album_audio_id": album_audio_id, "lrctxt": 1},
        timeout=timeout,
    )
    result = []
    for c in (data.get("candidates") or []):
        result.append({
            "accesskey": c.get("accesskey", ""),
            "id": c.get("id", ""),
            "singer": c.get("singer", ""),
            "song": c.get("song", ""),
            "duration_ms": int(c.get("duration", 0)),
        })
    return result


def download_krc(accesskey, id_, timeout=15):
    data = _http_json(
        "http://lyrics2.kugou.com/download",
        {"accesskey": accesskey, "charset": "utf8", "client": "pc",
         "fmt": "krc", "id": id_, "ver": 1},
        timeout=timeout,
    )
    content = data.get("content", "")
    if not content:
        return ""
    return decrypt_krc(content)


def decrypt_krc(content_base64):
    """解密 KRC：Base64 解码 -> 跳 4 字节魔数 -> XOR -> zlib 解压。"""
    raw = base64.b64decode(content_base64)
    krc = bytearray(raw[4:])
    for i in range(len(krc)):
        krc[i] ^= KRC_KEY[i % len(KRC_KEY)]
    return zlib.decompress(bytes(krc)).decode("utf-8", errors="replace")


def select_candidate(candidates, expect_title=None, expect_artist=None,
                     expect_duration_ms=None):
    """从候选中选出「时长 + 歌手 + 歌名」最匹配的一个，无匹配返回 None。"""
    best, best_score = None, -1
    for c in candidates:
        cd = c.get("duration_ms", 0)
        if expect_duration_ms and cd:
            if abs(cd - expect_duration_ms) > DURATION_TOLERANCE_MS:
                continue
        score = (_text_match(c.get("singer", ""), expect_artist) * 3 +
                 _text_match(c.get("song", ""), expect_title) * 3)
        if expect_duration_ms and cd:
            diff_sec = abs(cd - expect_duration_ms) / 1000.0
            score += max(0, 2 - diff_sec)
        if score > best_score:
            best_score, best = score, c
    return best


def _ms_to_lrc(ms):
    m = ms // 60000
    s = (ms % 60000) / 1000
    return f"[{m:02d}:{s:05.2f}]"


def krc_to_lrc(krc_text):
    """把 KRC 逐字歌词转成 LRC 逐行歌词。"""
    tags = []
    lines = []
    for line in krc_text.split("\n"):
        line = line.strip()
        if not line:
            continue
        m_tag = re.match(r"^\[(\w+):([^\]]*)\]$", line)
        if m_tag:
            if m_tag.group(1) != "language":
                tags.append(line)
            continue
        m_line = re.match(r"^\[(\d+),(\d+)\](.*)$", line)
        if m_line:
            start = int(m_line.group(1))
            text = re.sub(r"<\d+,\d+,\d+>", "", m_line.group(3)).strip()
            if text:
                lines.append(_ms_to_lrc(start) + text)
    return "\n".join(tags + lines)


def fetch_lyric(keyword, singer=None, expect_duration_ms=None,
                expect_title=None, expect_artist=None, timeout=15):
    """检索歌词，返回 {songname, singer, krc, lrc}；找不到或匹配失败返回 None。"""
    all_candidates = []
    seen = set()

    def add_candidates(result):
        if not result:
            return
        hash_, dur, album_id, songname, singer_name = result
        for c in search_lyrics(f"{singer_name} - {songname}", hash_, dur, album_id, timeout):
            key = (c.get("accesskey"), c.get("id"))
            if key not in seen:
                seen.add(key)
                all_candidates.append(c)

    full_kw = f"{singer} {keyword}".strip() if singer else keyword
    add_candidates(search_song(full_kw, timeout=timeout))
    if singer:
        add_candidates(search_song(keyword, timeout=timeout))

    if not all_candidates:
        return None

    best = select_candidate(all_candidates, expect_title or keyword,
                            expect_artist or singer, expect_duration_ms)
    if not best:
        return None

    krc_text = download_krc(best["accesskey"], best["id"], timeout)
    if not krc_text:
        return None
    return {
        "songname": best.get("song") or keyword,
        "singer": best.get("singer") or singer,
        "krc": krc_text,
        "lrc": krc_to_lrc(krc_text),
    }


# ===========================================================================
# 第 6 部分：歌词嵌入
# ===========================================================================

def embed_lyrics(path, ext, lrc_text, M):
    """把歌词嵌入音频文件标签（FLAC 的 LYRICS / MP3 的 USLT）。"""
    if ext == ".flac":
        audio = M["FLAC"](path)
        audio["lyrics"] = lrc_text
        audio.save()
    elif ext == ".mp3":
        audio = M["MP3"](path, ID3=M["ID3"])
        if audio.tags is None:
            audio.add_tags()
        audio.tags.delall("USLT")
        audio.tags.add(M["USLT"](encoding=3, lang="chi", desc="", text=lrc_text))
        audio.save()


# ===========================================================================
# 第 7 部分：元数据补全核心（含双向搜索）
# ===========================================================================

def _resolve_online(existing, fn_artist, fn_title, timeout=15):
    """确定在线搜索词，处理文件名顺序不确定（双向搜索）。

    返回 (online, fn_artist, fn_title)，fn_* 可能被纠正顺序。
    """
    # 标签有完整 title+artist：顺序确定，直接用标签搜
    if existing["title"] and existing["artist"]:
        return search_metadata(existing["title"], existing["artist"], timeout), fn_artist, fn_title
    # 依赖文件名，顺序未知：双向搜索裁决
    if fn_artist and fn_title:
        return search_bidirectional(fn_artist, fn_title, timeout)
    # 只有部分信息：单向搜
    t = existing["title"] or fn_title or ""
    a = existing["artist"] or fn_artist or ""
    if t or a:
        return search_metadata(t, a, timeout), fn_artist, fn_title
    return None, fn_artist, fn_title


def enrich_metadata(out_path, src_path, ext, M, overrides=None,
                    print_only=False, priority="meta", no_online=False):
    """给已解密的音频补全元数据并写回，返回合并后的 meta dict。"""
    overrides = overrides or {}
    existing = read_existing_meta(out_path, ext, M)
    fn_artist, fn_title = parse_filename(src_path)

    online = None
    if not no_online:
        online, fn_artist, fn_title = _resolve_online(existing, fn_artist, fn_title)

    o_title = online.get("title") if online else ""
    o_artist = online.get("artist") if online else ""
    o_album = online.get("album") if online else ""
    o_year = online.get("year") if online else ""

    if priority == "meta":
        title = overrides.get("title") or existing["title"] or fn_title or o_title or ""
        artist = overrides.get("artist") or existing["artist"] or fn_artist or o_artist or ""
        album = overrides.get("album") or existing["album"] or o_album or ""
        date = overrides.get("year") or existing["date"] or o_year or ""
    else:
        title = overrides.get("title") or o_title or existing["title"] or fn_title or ""
        artist = overrides.get("artist") or o_artist or existing["artist"] or fn_artist or ""
        album = overrides.get("album") or o_album or existing["album"] or ""
        date = overrides.get("year") or o_year or existing["date"] or ""

    genre = overrides.get("genre") or existing["genre"] or ""
    albumartist = overrides.get("albumartist") or existing["albumartist"] or ""

    # 封面：手动 > 已有标签 > 在线下载
    cover = existing["cover"]
    cover_mime = existing["cover_mime"]
    replace_cover = False
    if overrides.get("cover"):
        try:
            with open(overrides["cover"], "rb") as cf:
                cover = cf.read()
            cover_mime = detect_image_mime(cover)
            replace_cover = True
        except Exception as e:
            print(f"  [提示] 读取封面失败：{e}")
    elif not cover and online and online.get("cover_url"):
        downloaded = fetch_cover(online["cover_url"])
        if downloaded:
            cover = downloaded
            cover_mime = detect_image_mime(cover)
            replace_cover = True

    meta = {
        "title": title, "artist": artist, "album": album,
        "albumartist": albumartist, "date": date, "genre": genre,
        "cover": cover, "cover_mime": cover_mime,
        "online_source": online.get("source") if online else "",
    }

    if print_only:
        _print_meta(src_path, out_path, ext, meta)
    else:
        write_meta(out_path, ext, meta, M, replace_cover)

    return meta


def _print_meta(path, out_path, ext, meta):
    print(f"\n{path}  ->  {out_path} ({ext[1:].upper()})")
    for k in ("title", "artist", "album", "albumartist", "date", "genre"):
        label = {"title": "歌名", "artist": "歌手", "album": "专辑",
                 "albumartist": "专辑歌手", "date": "年份", "genre": "流派"}[k]
        print(f"  {label:6s}: {meta[k] or '(无)'}")
    src = f"（在线来源：{meta['online_source']}）" if meta.get("online_source") else ""
    print(f"  {'封面':6s}: {'有' if meta['cover'] else '(无)'} {src}")


# ===========================================================================
# 第 8 部分：主流程
# ===========================================================================

def process_file(path, pub_key, args, M):
    # 1. 解密
    try:
        data = decrypt_file(path, pub_key)
    except Exception as e:
        print(f"[跳过] {path}: {e}")
        return

    ext = detect_extension(data)
    out_path = make_output_path(path, ext)
    with open(out_path, "wb") as f:
        f.write(data)

    meta_info = ""
    title, artist = "", ""

    # 2. 元数据 + 封面
    if not args.no_meta:
        if M is None:
            print("  [提示] 缺少 mutagen 库，跳过元数据补全。可 pip install mutagen")
        else:
            overrides = {
                "title": args.title, "artist": args.artist, "album": args.album,
                "albumartist": args.albumartist, "year": args.year,
                "genre": args.genre, "cover": args.cover,
            }
            try:
                meta = enrich_metadata(out_path, path, ext, M, overrides,
                                       print_only=args.print_only,
                                       priority=args.priority,
                                       no_online=args.no_online)
                title = meta["title"] or ""
                artist = meta["artist"] or ""
                meta_info = f" 标题={meta['title']!r} 歌手={meta['artist']!r} 专辑={meta['album']!r} 年份={meta['date']!r}"
                if meta.get("online_source"):
                    meta_info += f" [在线:{meta['online_source']}]"
                if meta["cover"]:
                    meta_info += " [封面]"
            except Exception as e:
                print(f"  [提示] 元数据补全失败（不影响音频）：{e}")

    # 3. 歌词：同名 .lrc + 嵌入音频
    if not args.no_lyric and not args.no_meta and not args.print_only and M is not None and title:
        try:
            duration_ms = _audio_duration_ms(out_path, ext, M)
            lyric = fetch_lyric(
                title, singer=artist,
                expect_duration_ms=duration_ms,
                expect_title=title, expect_artist=artist,
            )
            if lyric:
                lrc_path = os.path.splitext(out_path)[0] + ".lrc"
                with open(lrc_path, "w", encoding="utf-8") as f:
                    f.write(lyric["lrc"] + "\n")
                embed_lyrics(out_path, ext, lyric["lrc"], M)
                meta_info += " [歌词.lrc+嵌入]"
        except Exception as e:
            print(f"  [提示] 歌词处理失败（不影响音频）：{e}")

    # 4. 删除原文件（可选）
    if not args.print_only and args.delete:
        os.remove(path)

    print(f"[完成] {path} -> {out_path} ({ext[1:].upper()}){meta_info}")


def collect_targets(target, recursive):
    exts = {".kgm", ".vpr"}
    if os.path.isfile(target):
        return [target]
    result = []
    if recursive:
        for root, _dirs, files in os.walk(target):
            for fn in files:
                if os.path.splitext(fn)[1].lower() in exts:
                    result.append(os.path.join(root, fn))
    else:
        for fn in sorted(os.listdir(target)):
            p = os.path.join(target, fn)
            if os.path.isfile(p) and os.path.splitext(fn)[1].lower() in exts:
                result.append(p)
    return result


def _choose_folder():
    """双击启动时弹窗选择文件夹；不可用时返回空串。"""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        folder = filedialog.askdirectory(title="选择包含 .kgm / .vpr 文件的文件夹")
        root.destroy()
        return folder or ""
    except Exception:
        return ""


def _pause(interactive):
    """双击启动时，处理结束后等待按键，避免窗口一闪而过。"""
    if not interactive:
        return
    try:
        input("\n按回车键退出...")
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(
        description="解密酷狗 KGM 并补全元数据/封面/歌词（同名 .lrc + 嵌入音频，单文件版）"
    )
    parser.add_argument("target", nargs="*",
                        help="要处理的 .kgm/.vpr 文件或目录（可拖拽多个；留空则弹窗选择）")
    parser.add_argument("-r", "--recursive", action="store_true", help="递归处理目录")
    parser.add_argument("-d", "--delete", action="store_true", help="解密后删除原加密文件（默认保留）")
    parser.add_argument("--no-meta", action="store_true", help="只解密，不补元数据")
    parser.add_argument("--no-lyric", action="store_true", help="不处理歌词")
    parser.add_argument("--no-online", action="store_true", help="禁用在线查询（含封面），仅本地补全")
    parser.add_argument("--priority", choices=["meta", "online"], default="meta",
                        help="元数据优先级：meta=本地优先(默认)，online=在线优先")
    parser.add_argument("--title", help="手动指定歌名")
    parser.add_argument("--artist", help="手动指定歌手")
    parser.add_argument("--album", help="手动指定专辑")
    parser.add_argument("--albumartist", help="手动指定专辑歌手")
    parser.add_argument("--year", help="手动指定年份（如 2003）")
    parser.add_argument("--genre", help="手动指定流派")
    parser.add_argument("--cover", help="手动指定封面图片路径")
    parser.add_argument("--print", dest="print_only", action="store_true",
                        help="只查看元数据，不写回")
    args = parser.parse_args()

    script_dir = _app_dir()
    pub_key = load_pub_key(script_dir)

    # 收集目标：有参数（命令行 / 拖拽文件）→ 按参数处理；
    # 无参数（直接双击 exe）→ 弹窗选文件夹，不弹窗时退回扫描程序所在目录。
    interactive = not args.target
    targets = []
    if args.target:
        for t in args.target:
            targets += collect_targets(t, args.recursive)
    else:
        print("未指定文件，弹出文件夹选择框...")
        folder = _choose_folder()
        if folder:
            targets = collect_targets(folder, args.recursive)
        else:
            print("未选择文件夹，改为扫描程序所在目录...")
            targets = collect_targets(script_dir, args.recursive)

    if not targets:
        print("没有找到可处理的 .kgm / .vpr 文件。")
        _pause(interactive)
        return

    # 需要写元数据/歌词时才加载 mutagen
    M = None
    if not args.no_meta:
        M = _mutagen()

    print(f"共找到 {len(targets)} 个文件，开始处理...")
    for t in targets:
        process_file(t, pub_key, args, M)
    print("全部处理完毕。")
    _pause(interactive)


if __name__ == "__main__":
    main()
