# QuietReader 更新日志 / Changelog

## [0.1.8] — 2026-11-24

### 简体中文

QuietReader 0.1.8 修复设置滑块和专注模式窗口调整时的交互问题，提供 Windows x64 便携版与安装升级包。

#### 下载与升级

- 便携版：[QuietReader-0.1.8-Windows-x64.zip](https://github.com/Nore07/Quiet-Reader/releases/download/v0.1.8/QuietReader-0.1.8-Windows-x64.zip)。
- SHA-256：`568cc15c8fc8a620484747c4ebfaece680e129a22324551c193eafb4c477709d`；[校验文件](https://github.com/Nore07/Quiet-Reader/releases/download/v0.1.8/QuietReader-0.1.8-Windows-x64.zip.sha256)。
- 安装升级包：[QuietReader-Setup-0.1.8-x64.exe](https://github.com/Nore07/Quiet-Reader/releases/download/v0.1.8/QuietReader-Setup-0.1.8-x64.exe)。
- SHA-256：`6fe9a95f6c49fd47c9f6b550fc71e792493ffbbd6933728ae4504507d26f1001`；[校验文件](https://github.com/Nore07/Quiet-Reader/releases/download/v0.1.8/QuietReader-Setup-0.1.8-x64.exe.sha256)。
- 更新时保留原来的 `data` 文件夹；安装程序原地升级会保留现有数据。便携包不包含个人书籍或配置，且软件包未进行代码签名。

#### 本次改进

- 「配置 → 专注与窗口」和「配置 → 阅读与排版」的滑动条不再响应鼠标滚轮，避免误调数值。
- 「排版外观」与「专注模式」浮窗中的滑动条在拖动时只更新数值，松开鼠标后才应用到阅读页面；配置页隐藏阅读内容时延后排版重排，减少拖动卡顿。
- 修复专注模式下按住 `Alt` 拖动或缩放窗口时误选中正文的问题。
- 更新中英文 README，增加专注浮窗截图与说明，并让下载入口自动指向最新正式版。

本版本不改变配置格式；升级后保留配置、阅读进度、书签和书籍数据。

### English

QuietReader 0.1.8 is a Windows x64 maintenance release with smoother settings sliders and improved Focus window adjustment.

#### Download and upgrade

- Portable package: [QuietReader-0.1.8-Windows-x64.zip](https://github.com/Nore07/Quiet-Reader/releases/download/v0.1.8/QuietReader-0.1.8-Windows-x64.zip).
- SHA-256: `568cc15c8fc8a620484747c4ebfaece680e129a22324551c193eafb4c477709d`; [checksum file](https://github.com/Nore07/Quiet-Reader/releases/download/v0.1.8/QuietReader-0.1.8-Windows-x64.zip.sha256).
- Setup and upgrade package: [QuietReader-Setup-0.1.8-x64.exe](https://github.com/Nore07/Quiet-Reader/releases/download/v0.1.8/QuietReader-Setup-0.1.8-x64.exe).
- SHA-256: `6fe9a95f6c49fd47c9f6b550fc71e792493ffbbd6933728ae4504507d26f1001`; [checksum file](https://github.com/Nore07/Quiet-Reader/releases/download/v0.1.8/QuietReader-Setup-0.1.8-x64.exe.sha256).
- Keep the existing `data` folder when updating. Setup preserves it during an in-place upgrade. The portable package excludes personal books and settings. The packages are unsigned.

#### Changes

- Mouse-wheel input no longer changes sliders in Configuration → Focus & window or Reading & typography.
- Typography and Focus popover sliders update their values while dragging and apply the reading-page change once on release. The settings page defers reader reflow while the reader is hidden, reducing drag lag.
- Fixed accidental text selection when holding `Alt` to move or resize the Focus window.
- Updated the Chinese and English READMEs with a Focus popover screenshot and a link to the latest stable release.

This release does not change the settings format. Existing preferences, reading progress, bookmarks, and books are preserved.

[完整变更 / Full changelog](https://github.com/Nore07/Quiet-Reader/compare/v0.1.7...v0.1.8)

## [0.1.6] — 2026-11-19

### 简体中文

本版本提供 Windows x64 便携软件包，包含近期阅读进度、主题配色和界面改进，并提升了含有大量插图引用的 EPUB 兼容性。

#### 下载与运行

- 下载：[QuietReader-0.1.6-Windows-x64.zip](https://github.com/Nore07/Quiet-Reader/releases/download/v0.1.6/QuietReader-0.1.6-Windows-x64.zip)。
- SHA-256：`46fc4378c3d716bb05e5c4d7ff11db90f4df845e7a62d7c048b7dc3db7637957`；校验文件：[QuietReader-0.1.6-Windows-x64.zip.sha256](https://github.com/Nore07/Quiet-Reader/releases/download/v0.1.6/QuietReader-0.1.6-Windows-x64.zip.sha256)。
- 解压后运行 `QuietReader.exe`，并保留程序目录中的所有文件。
- 软件包包含应用程序、运行时依赖、Apache-2.0 许可文本和第三方许可信息；不包含仓库源码、开发脚本、测试内容、个人设置或书籍。
- 当前 Windows 软件包未进行代码签名。

#### 本次改进

- 阅读页进度条显示当前章节进度，全书进度显示在左侧栏；书架列表显示当前章节和全书百分比。
- 重复启动程序时会唤醒已有窗口，避免打开多个实例。
- 修复夜读主题下托盘菜单和书籍资料页文字对比度不足的问题。
- 暖纸主题改用协调的暖米色、砂岩色和咖啡棕配色；更新书架分类下拉选择器样式。
- EPUB 插图引用支持上限提高至 4096，并避免重复读取时重复校验同一图片资源。

#### 配置与书籍位置

- 配置、阅读进度和书签：`data\state.json`。
- 导入的书籍副本：`data\bookshelf\uncategorized\` 或 `data\bookshelf\<分类名>\`。
- `data` 位于阅读器根目录；Release 版中，它与 `QuietReader.exe` 同级。磁盘目录使用英文名称 `bookshelf` 和 `uncategorized`，界面名称由语言设置翻译。
- 更新软件时保留原来的 `data` 文件夹；若解压到新目录，请把旧 `data` 复制到新 `QuietReader.exe` 旁边。

### English

This release provides a portable Windows x64 package with recent reading-progress, theme, and interface improvements, plus better compatibility with EPUBs containing many image references.

#### Download and run

- Download: [QuietReader-0.1.6-Windows-x64.zip](https://github.com/Nore07/Quiet-Reader/releases/download/v0.1.6/QuietReader-0.1.6-Windows-x64.zip).
- SHA-256: `46fc4378c3d716bb05e5c4d7ff11db90f4df845e7a62d7c048b7dc3db7637957`; checksum file: [QuietReader-0.1.6-Windows-x64.zip.sha256](https://github.com/Nore07/Quiet-Reader/releases/download/v0.1.6/QuietReader-0.1.6-Windows-x64.zip.sha256).
- Extract the archive, run `QuietReader.exe`, and keep every file in the application directory.
- The package contains the application, runtime dependencies, the Apache-2.0 license, and third-party notices. It excludes repository source, developer scripts, tests, personal settings, and books.
- This Windows package is not code-signed.

#### Changes

- The reader progress bar now tracks the current chapter; whole-book progress appears in the sidebar, and shelf entries show the current chapter and whole-book percentage.
- Repeated launches now bring the existing window forward instead of opening another instance.
- Fixed low text contrast in the tray menu and book details under the Night theme.
- Updated the Sepia theme with coordinated warm-paper, sandstone, and coffee-brown colors; refreshed the bookshelf category selectors.
- Raised the EPUB image-reference limit to 4,096 while avoiding repeated validation of the same image resource.

#### Settings and books

- Settings, reading positions, and bookmarks: `data\state.json`.
- Imported book copies: `data\bookshelf\uncategorized\` or `data\bookshelf\<category>\`.
- `data` is under the reader root; in the release package it sits beside `QuietReader.exe`. On-disk folder names are `bookshelf` and `uncategorized`; the interface translates their labels according to the selected language.
- Keep the existing `data` folder when updating. If you extract into a new location, copy the old `data` folder beside the new `QuietReader.exe`.

## [0.1.5] — 2026-11-18

### 简体中文

本版本提供 Windows x64 便携软件包，适合直接解压运行。程序包含 TXT 与 EPUB 本地阅读、书架、目录和书签、排版偏好、专注模式等功能。具体支持范围见 [中文 README](README.md)。

#### 下载与运行

- 下载：[QuietReader-0.1.5-Windows-x64.zip](https://github.com/Nore07/Quiet-Reader/releases/download/v0.1.5/QuietReader-0.1.5-Windows-x64.zip)。
- SHA-256：`db34a63c31b847501ec2504e94c9ae96272efa27ef6ca91a9c877e21f022fc9b`；校验文件：[QuietReader-0.1.5-Windows-x64.zip.sha256](https://github.com/Nore07/Quiet-Reader/releases/download/v0.1.5/QuietReader-0.1.5-Windows-x64.zip.sha256)。
- 解压后运行 `QuietReader.exe`，并保留程序目录中的所有文件。
- 软件包包含应用程序、运行时依赖、Apache-2.0 许可文本和第三方许可信息（包括 CPython、Qt for Python、PyInstaller 及其他运行依赖）；不包含仓库源码、开发脚本、测试内容、个人设置或书籍。
- 当前 Windows 软件包未进行代码签名。

#### 配置与书籍位置

- 配置、阅读进度和书签：`data\state.json`。
- 书籍文件：`data\bookshelf\uncategorized\` 或 `data\bookshelf\<分类名>\`。
- `data` 位于阅读器根目录：源码运行时在仓库根目录下，Release 版在 `QuietReader.exe` 旁边。磁盘目录使用英文名称 `bookshelf` 和 `uncategorized`，界面名称由语言设置翻译。
- 源码版和 Release 版的数据目录相互独立；需要共享时，启动两者时使用相同的 `--data-dir <目录>`。
- 首次启动时，如果目标 `data` 为空，程序会把旧版 `%LOCALAPPDATA%\QuietReader\QuietReader\` 中的配置和书架副本迁移到新位置；旧目录会保留。
- 更新软件时保留原来的 `data` 文件夹。若解压到新目录，将旧目录中的 `data` 复制到新 `QuietReader.exe` 旁边。

#### 本次调整

- 改进窄窗口下的阅读翻页控件与书签操作按钮布局。
- 将配置和书架数据改为保存在应用根目录的 `data` 文件夹，并在默认路径首次启用时迁移旧数据。
- 书架与未分类文件夹在磁盘上使用稳定的英文名称，同时保留界面的中英文显示。

### English

This release provides a portable Windows x64 package. It includes local TXT and EPUB reading, a bookshelf, table of contents and bookmarks, reading layout preferences, and Focus Mode. See the [English README](README.en.md) for supported formats and features.

#### Download and run

- Download: [QuietReader-0.1.5-Windows-x64.zip](https://github.com/Nore07/Quiet-Reader/releases/download/v0.1.5/QuietReader-0.1.5-Windows-x64.zip).
- SHA-256: `db34a63c31b847501ec2504e94c9ae96272efa27ef6ca91a9c877e21f022fc9b`; checksum file: [QuietReader-0.1.5-Windows-x64.zip.sha256](https://github.com/Nore07/Quiet-Reader/releases/download/v0.1.5/QuietReader-0.1.5-Windows-x64.zip.sha256).
- Extract the archive, run `QuietReader.exe`, and keep every file in the application directory.
- The package contains the application, runtime dependencies, the Apache-2.0 license, and third-party notices for CPython, Qt for Python, PyInstaller, and other runtime dependencies. It excludes repository source, developer scripts, tests, personal settings, and books.
- This Windows package is not code-signed.

#### Settings and books

- Settings, reading positions, and bookmarks: `data\state.json`.
- Book files: `data\bookshelf\uncategorized\` or `data\bookshelf\<category>\`.
- `data` is under the reader root: under the repository root when run from source, and beside `QuietReader.exe` in a release. On-disk folder names are `bookshelf` and `uncategorized`; the interface translates their labels according to the selected language.
- Source and release builds keep separate data directories. To share data, launch both with the same `--data-dir <directory>`.
- On first launch, if the target `data` directory is empty, the app copies settings and bookshelf files from the previous `%LOCALAPPDATA%\QuietReader\QuietReader\` location. The old directory is retained.
- Keep the existing `data` folder when updating. If you extract into a new location, copy the old `data` folder beside the new `QuietReader.exe`.

#### Changes

- Improved reader navigation and bookmark action layouts in narrow windows.
- Moved settings and bookshelf data to the application-root `data` folder, with first-run migration from the previous default location.
- Standardized on-disk bookshelf folder names in English while keeping translated interface labels.
