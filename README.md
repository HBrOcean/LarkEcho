# KGM Decryptor · 酷狗加密音乐转换工具

[![Build](https://github.com/HBrOcean/kgm-decryptor/actions/workflows/build.yml/badge.svg)](https://github.com/HBrOcean/kgm-decryptor/actions/workflows/build.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.9%2B-3776ab.svg)

把酷狗音乐的 **KGM / VPR 加密文件**还原成原始音频（FLAC / MP3），并自动补全
**元数据、封面、歌词**。提供**图形界面**和**命令行**两种版本。

> 本项目仅用于处理你**自己合法拥有**的音乐文件。

---

## ✨ 功能

| 能力 | 说明 |
|---|---|
| **解密** | `.kgm` / `.vpr` → 自动识别真实格式（FLAC / MP3 / WAV / OGG / M4A / AAC） |
| **元数据** | 歌名、歌手、专辑、年份。本地标签 / 文件名 / 手动参数优先，在线查询兜底 |
| **封面** | 音频内已有封面就直接用，没有才联网搜索 |
| **歌词** | 检索酷狗 KRC 逐字歌词，对比「时长 + 歌手 + 歌名」匹配 → 输出同名 `.lrc` **并嵌入音频** |
| **顺序自适应** | 文件名「歌手 - 歌名」和「歌名 - 歌手」两种顺序**都能自动识别** |
| **在线来源** | 优先级：酷狗音乐 → QQ 音乐 → 网易云 → iTunes |

---

## 📦 获取程序

### 方式一：直接下载构建好的可执行文件（推荐）

进入本仓库的 **Actions** 页面 → 选择最新的 `Build` 运行 → 在页面底部
**Artifacts** 处下载，或直接到 **Releases** 下载：

| 产物 | 平台 | 说明 |
|---|---|---|
| `kgm2gui.exe` | Windows | **图形界面版**，双击即用 |
| `kgm2all.exe` | Windows | 命令行版 |
| `kgm2all` | Linux / macOS | 命令行版 |

> 每次推送代码或手动触发（`workflow_dispatch`）都会自动构建；
> 打 `v*` 标签（如 `v1.0`）则会自动创建 Release 并附上全部产物。

### 方式二：本地自己打包

```bash
pip install pyinstaller pywebview mutagen
python tools/build.py all      # 或 cli / gui
# 产物在 dist/ 目录
```

Windows 用户也可以直接双击根目录的 `build_exe.bat`。

> ⚠️ PyInstaller **不支持交叉编译**：要 Windows 的 `.exe` 就必须在 Windows 上构建。
> 本仓库已配好 GitHub Actions，推到 GitHub 即自动产出三平台产物。

### 方式三：直接运行源码

```bash
pip install mutagen            # 图形界面版还需要：pip install pywebview
python kgm2all.py 歌曲.kgm      # 命令行版
python app.py                  # 图形界面版
```

---

## 🚀 使用

### 图形界面版（`kgm2gui.exe` / `python app.py`）

1. 点击虚线框选择 `.kgm` / `.vpr` 文件（可多选）
2. 选择输出目录（留空 = 输出到源文件所在目录）
3. 勾选需要的选项：元数据/封面、联网查询、歌词
4. 点「开始转换」，界面会实时显示每个文件的进度与识别结果

### 命令行版（`kgm2all.exe` / `python kgm2all.py`）

```bash
python kgm2all.py 周杰伦-晴天.kgm              # 解密 + 元数据 + 封面 + 歌词
python kgm2all.py -r ./音乐目录/               # 递归处理整个目录
python kgm2all.py 歌曲.kgm --no-lyric          # 不处理歌词
python kgm2all.py 歌曲.kgm --no-online         # 禁用联网，仅本地补全
python kgm2all.py -d 歌曲.kgm                  # 解密成功后删除原加密文件
python kgm2all.py 歌曲.kgm --album 叶惠美 --year 2003 --cover c.jpg
python kgm2all.py -h                           # 查看全部参数
```

---

## 📁 项目结构

```
kgm-decryptor/
├── .github/workflows/build.yml   # GitHub Actions：多平台构建 + artifacts
├── assets/
│   ├── icon.ico                  # 应用图标
│   └── kugou_key.xz              # 解密公钥（压缩后 ~92 KB）
├── tools/
│   └── build.py                  # 跨平台打包脚本
├── kgm2all.py                    # 核心逻辑 + 命令行入口
├── app.py                        # 图形界面入口（pywebview）
├── index.html                    # 图形界面
├── build_exe.bat                 # Windows 一键打包（本地用）
├── requirements.txt
└── README.md
```

---

## 🔧 技术说明

**解密算法**（公开的逆向工程成果，源自开源项目
[unlock-music](https://github.com/unlock-music/unlock-music) /
[kugou-kgm-decoder](https://github.com/ghtz08/kugou-kgm-decoder)）：

```
对第 i 个音频字节（去掉 1024 字节文件头后）：
    own  = own_key[i % 17]  ^ 密文[i]
    own ^= (own & 0x0F) << 4
    pub  = PUB_KEY_MEND[i % 272] ^ 公钥[i // 16]
    pub ^= (pub & 0x0F) << 4
    明文 = own ^ pub
```

- `own_key`：每个文件独有，取自文件头偏移 `0x1C ~ 0x2C` 的 16 字节
- `PUB_KEY_MEND`：272 字节固定修正表（已硬编码）
- 公钥：所有文件共用，存放在 `assets/kugou_key.xz`（解压后约 73 MB）

**KRC 歌词解密**：Base64 解码 → 跳过 4 字节魔数 → 16 字节密钥 XOR → zlib 解压。

---

## ❓ 常见问题

**Q：转换后没有元数据 / 封面？**
检查「补全元数据 / 封面」和「联网查询」是否开启——在线查询需要联网。

**Q：图形界面版打开是白屏？**
Windows 10/11 需要 **Edge WebView2 运行时**（系统一般自带）。
若确实没有，到微软官网安装「WebView2 Runtime」即可。

**Q：`build_exe.bat` 双击后满屏 `'xxx' 不是内部或外部命令`？**
bat 文件的**换行符**坏了。Windows 的 cmd 要求 **CRLF**（`\r\n`）。本仓库内的脚本
已是 CRLF + 纯英文提示；若你自行编辑过，保存时请选「Windows (CRLF)」。

**Q：Actions 构建失败了怎么办？**
点进失败的 job 查看日志。最常见的是依赖安装超时（重跑一次即可）。

---

## 📄 许可

本项目基于 [MIT License](LICENSE) 开源。

解密与歌词算法源自上述开源项目；请勿将本工具用于侵犯版权的用途。
