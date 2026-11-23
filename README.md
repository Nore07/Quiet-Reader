# QuietReader · 静读 —— 偷懒的大图书馆

[简体中文](README.md) · [English](README.en.md)

<p align="center"><img src="src/quietreader/assets/quietreader.png" alt="QuietReader 图标" width="128"></p>

QuietReader 是一款面向 Windows 的离线本地阅读器，使用 Python 与 PySide6 构建。它支持 TXT 和 EPUB，提供书架、目录、书签、排版调节和专注阅读；书籍与阅读进度保存在本机。

## 界面预览

### 阅读界面

![QuietReader 阅读界面](docs/screenshots/reading.png)

**本地阅读：** 在侧栏查看目录、书签和资料，阅读章节内容与 EPUB 内嵌插图，并使用底部控件翻页。

### 专注模式

![专注模式融入取色后的桌面背景](docs/screenshots/focus1.png)

**背景融合：** 先在配置中使用桌面取色器获取想融入的背景颜色，再进入专注模式；阅读窗口会尽量融入桌面背景。

![按住 Alt 显示窗口轮廓并移动或缩放](docs/screenshots/focus2.png)

**按住 Alt 调整窗口：** 专注模式下按住 `Alt` 会显示窗口轮廓，此时可拖动窗口或调整大小；松开按键后继续纯文字阅读。

### 书架

![最近阅读](docs/screenshots/bookshelf1.png)

**最近阅读：** 从最近打开的书籍继续阅读，并快速回到上次阅读位置。

![所有书籍](docs/screenshots/bookshelf2.png)

**所有书籍：** 浏览已添加到本地书架的书籍，并从书架页面添加新书。

![书籍分类](docs/screenshots/bookshelf3.png)

**书籍分类：** 创建分类并整理书架中的书籍，让不同主题的藏书更容易查找。

### 配置与排版

![阅读配置页面](docs/screenshots/peizhiyemian.png)

**阅读器配置：** 集中设置界面语言、主题和阅读外观等偏好。

![排版调整实时预览](docs/screenshots/yuedupeizhi.png)

**实时排版：** 调整字号、行距、字距、段距或页边距时，阅读区域会同步更新，方便直接比较效果。

## 功能

- **TXT 阅读**：自动识别常见编码，遇到乱码时可手动选择编码。
- **EPUB 阅读**：支持常见的 EPUB 2/3 重排版书籍、目录导航、书内链接、页码列表、封面、元数据、文字格式和内嵌插图。
- **本地书架**：最近阅读、全部书籍与自建分类。添加书籍会复制到 QuietReader 的书架目录，分类时会移动书架内的副本，原始文件不受影响。
- **阅读辅助**：目录、书签与笔记、书籍资料、阅读位置恢复、可折叠侧栏。
- **实时排版**：字号、字体、行距、字距、段距和页边距可在阅读界面中即时预览。
- **外观和语言**：系统、纸白、暖纸、夜读主题；支持简体中文、English 或跟随系统语言。
- **专注模式**：隐藏阅读界面控件，只保留书中文字；支持背景透明度、桌面取色、文字颜色与窗口置顶。按住调整键可移动或缩放窗口；只有背景为 0% 时才临时提高到 1% 以接收鼠标操作，已有的非零透明度保持不变。
- **键盘操作**：翻页、切换章节、进入专注模式、显示/隐藏窗口和紧急退出均可配置。专注模式中可按住调整键移动或缩放窗口。
- **离线优先**：不需要账户或在线书源；书籍、阅读位置和偏好都保存在本机。

## 快捷键

以下为默认按键，除固定保留的应用操作外，可在“配置”中录入其他按键或组合键。

| 操作 | 默认快捷键 |
| --- | --- |
| 下一页 / 上一页 | `PageDown` / `PageUp` |
| 下一章 / 上一章 | `Ctrl+PageDown` / `Ctrl+PageUp` |
| 进入或退出专注模式 | `F10` 或 `Esc` |
| 专注模式透明度切换（0% / 100%） | `F9` |
| 添加书籍 / 配置 | `Ctrl+O` / `Ctrl+,` |
| 显示 / 隐藏窗口 | `Ctrl+Alt+R` |
| 紧急退出 | `Ctrl+Alt+Q` |

在「配置 → 快捷键」开启全局快捷键后，只要窗口仍显示，即使正在使用其他应用，也能触发显示/隐藏或紧急退出。窗口隐藏或最小化后，只有显示/隐藏快捷键仍然生效，可用它唤回窗口。专注模式的窗口调整键默认为 `Alt`。

## 获取与运行

**下载软件包：** [QuietReader 0.1.6 Windows x64 便携版](https://github.com/Nore07/Quiet-Reader/releases/download/v0.1.6/QuietReader-0.1.6-Windows-x64.zip)（[SHA-256 校验文件](https://github.com/Nore07/Quiet-Reader/releases/download/v0.1.6/QuietReader-0.1.6-Windows-x64.zip.sha256)）可在 [GitHub Releases](https://github.com/Nore07/Quiet-Reader/releases) 下载。压缩包不包含个人配置或书籍；解压后运行 `QuietReader.exe`，程序会在其旁边创建 `data\`。运行时请保留解压目录中的全部文件。

**从源码运行：** 下载或克隆仓库后，需要 Python 3.12 或更新版本。在仓库根目录运行 PowerShell 命令：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

也可直接打开一本本地书籍：

```powershell
.\.venv\Scripts\python.exe run.py "D:\Books\sample.epub"
```

## 格式与数据

目前面向常见的 TXT 与未加密、重排版 EPUB。DRM 加密资源、固定版式 EPUB、活动内容和外部网络资源不在支持范围内；不同 EPUB 的样式复杂度可能影响排版效果。

配置和书籍都保存在阅读器根目录下的 `data` 文件夹中：源码运行时位于仓库根目录的 `data\`，Releases 版位于 `QuietReader.exe` 所在目录的 `data\`。配置、阅读进度和书签保存在 `data\state.json`；导入的书籍副本保存在 `data\bookshelf\uncategorized\` 或 `data\bookshelf\<分类名>\` 中。磁盘上的 `bookshelf` 和 `uncategorized` 使用英文名称，界面仍会根据语言设置显示“书架 / Bookshelf”和“未分类 / Uncategorized”。

源码版和 Releases 版分别使用各自根目录的 `data`，默认不会互相同步。若要共享数据，可给两种启动方式传入相同的 `--data-dir <目录>`。首次使用默认目录且目标 `data` 为空时，旧版 `%LOCALAPPDATA%\QuietReader\QuietReader\` 中的数据会复制过去，原目录会保留。更新 Releases 版时请保留原目录中的 `data`；若解压到新目录，请把旧目录的 `data` 一并复制过去。

将 `data` 复制到新程序目录后，首次启动会检查书架记录是否还指向旧目录。若新 `data\bookshelf` 中有对应的书籍文件，程序会更新书籍路径和书籍 ID，并同步保留阅读进度、书签与最近阅读记录。迁移前建议备份 `data`；未找到对应书籍时，原记录会保留，不会删除旧数据。

## 参与项目

欢迎通过 [GitHub Issues](https://github.com/Nore07/Quiet-Reader/issues) 报告问题或提出改进建议。协作分支、提交和 Pull Request 流程见[贡献指南](CONTRIBUTING.md)。

## 许可

QuietReader 项目代码采用 [Apache License 2.0](LICENSE)（SPDX：`Apache-2.0`）。第三方依赖及其分发说明见 [`docs/third-party.md`](docs/third-party.md)。
