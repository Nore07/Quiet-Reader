# 第三方依赖与许可

QuietReader 自编代码采用 Apache-2.0 许可，完整文本见仓库根目录的 [LICENSE](../LICENSE)。运行时依赖及其版本范围见 [`requirements.txt`](../requirements.txt)。第三方组件仍按各自的上游许可证提供，Apache-2.0 不会替代这些组件的许可。

| 组件 | 用途 | 上游许可证 / 依据 |
| --- | --- | --- |
| CPython 3.12.10 | 冻结应用运行时 | PSF-2.0，许可文本随软件包提供；见 [CPython 上游](https://github.com/python/cpython/tree/v3.12.10) |
| PySide6 Essentials 6.12.0 / shiboken6 6.12.0 | 桌面窗口与文字布局 | 上游元数据为 `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`；本发行包按 LGPL-3.0-only 选项提供动态库，见 [Qt for Python 许可说明](https://doc.qt.io/qtforpython-6/licenses.html) |
| PySide6 Addons 6.12.0 / Qt PDF | PDF 文字提取与页面渲染 | Qt PDF 按 LGPL-3.0 或 GPL-2.0 提供，并包含 PDFium（BSD）及其第三方组件；发行时保留 Addons wheel 和 Qt PDF 随附的许可文件，见 [Qt PDF 许可说明](https://doc.qt.io/qt-6/qtpdf-licensing.html) |
| charset-normalizer 3.5.2 | 文本编码识别 | MIT，见 [上游项目](https://github.com/jawah/charset_normalizer) |
| defusedxml 0.7.1 | 安全解析 EPUB XML | PSFL，见 [上游项目](https://github.com/tiran/defusedxml) |
| PyInstaller 6.22.3 bootloader | 冻结应用启动器 | GPLv2-or-later，并附带允许将 bootloader 合并分发的例外；见上游 [`COPYING.txt`](https://github.com/pyinstaller/pyinstaller/blob/v6.22.3/COPYING.txt) |

Windows Release 的 Qt 动态库及 Python 扩展模块保留在 `_internal` 目录，没有静态链接进 QuietReader 的应用代码。软件包中的 `third-party/` 提供 LGPL-3.0、GPL-3.0、PyInstaller bootloader 许可、依赖元数据和上游 wheel 随附的许可文件；根目录 `LICENSE` 提供 QuietReader 的 Apache-2.0 许可。对应的 QuietReader 源码可从本仓库获取。

查看 [GNU LGPL-3.0](https://www.gnu.org/licenses/lgpl-3.0.html)、[GNU GPL-3.0](https://www.gnu.org/licenses/gpl-3.0.html) 与 [Qt for Python 许可说明](https://doc.qt.io/qtforpython-6/licenses.html)。重新分发第三方组件时，请保留适用的许可证和版权通知，并按对应组件的上游条款履行义务。
