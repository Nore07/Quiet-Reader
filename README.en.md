# QuietReader · 静读 — A Lazy Library

[简体中文](README.md) · [English](README.en.md)

<p align="center"><img src="src/quietreader/assets/quietreader.png" alt="QuietReader icon" width="128"></p>

QuietReader is an offline-first desktop reader for Windows, built with Python and PySide6. It reads local TXT and EPUB books and includes a bookshelf, navigation, bookmarks, live layout controls, and a distraction-free Focus mode. Books and reading progress stay on your computer.

## Screenshots

### Reading view

![QuietReader reading view](docs/screenshots/reading.png)

**Read locally:** Browse the table of contents, bookmarks, and book details in the sidebar; read chapter text and embedded EPUB illustrations; and turn pages with the controls below.

### Focus mode

![Focus mode blending into a sampled desktop color](docs/screenshots/focus1.png)

**Blend with the desktop:** Sample the color you want to blend into with the desktop color picker in Configuration, then enter Focus mode to make the reader blend into the desktop.

![Hold Alt to show the window outline and move or resize it](docs/screenshots/focus2.png)

**Hold Alt to adjust the window:** In Focus mode, hold `Alt` to show the window outline, then drag or resize the window. Release the key to return to reading with only the text visible.

### Bookshelf

![Recently read books](docs/screenshots/bookshelf1.png)

**Recent books:** Continue a recently opened book and return to your saved reading position.

![All books](docs/screenshots/bookshelf2.png)

**All books:** Browse books in your local library and add new titles from the bookshelf page.

![Book categories](docs/screenshots/bookshelf3.png)

**Categories:** Create categories and organize books in your library so they are easier to find.

### Configuration and layout

![Configuration page](docs/screenshots/peizhiyemian.png)

**Reader configuration:** Set the interface language, theme, and reading appearance in one place.

![Live typography preview](docs/screenshots/yuedupeizhi.png)

**Live layout preview:** Change font size, line height, letter spacing, paragraph spacing, or page padding and see the reading area update immediately.

## Features

- **TXT reading** with automatic detection for common encodings and manual encoding selection when text looks garbled.
- **EPUB reading** for common EPUB 2/3 reflowable books, including navigation, internal links, page lists, covers, metadata, text formatting, and embedded illustrations.
- **Local bookshelf** with recent books, all books, and custom categories. Adding a book copies it into the QuietReader library; moving it to a category moves that managed copy and leaves the original file untouched.
- **Reading tools** including a table of contents, bookmarks and notes, book information, saved positions, and a collapsible sidebar.
- **Live typography controls** for font size, font family, line height, letter spacing, paragraph spacing, and page padding.
- **Themes and languages** with system, paper, sepia, and night themes, plus Simplified Chinese, English, or the system language.
- **Focus mode** hides reader controls and shows the book text, with configurable background opacity, desktop color sampling, text color, and always-on-top behavior. Hold the adjustment key to move or resize the window; only a fully transparent background is temporarily raised to 1% for mouse input, while other opacity levels remain unchanged.
- **Keyboard controls** for paging, chapter navigation, Focus mode, window visibility, and emergency exit. Hold the configured adjustment key to move or resize the window in Focus mode.
- **Offline by design**: no account or online book source is required; books, positions, and preferences stay local.

## Keyboard shortcuts

These are the defaults. Shortcuts can be changed in Configuration, except for a small set reserved for application actions.

| Action | Default |
| --- | --- |
| Next / previous page | `PageDown` / `PageUp` |
| Next / previous chapter | `Ctrl+PageDown` / `Ctrl+PageUp` |
| Enter or exit Focus mode | `F10` or `Esc` |
| Toggle Focus mode opacity (0% / 100%) | `F9` |
| Add books / Configuration | `Ctrl+O` / `Ctrl+,` |
| Show / hide the window | `Ctrl+Alt+R` |
| Emergency exit | `Ctrl+Alt+Q` |

Enable global shortcuts under Configuration → Shortcuts. While the window is visible, Show / Hide and Emergency Exit work even when another app is active. When the window is hidden or minimized, only Show / Hide remains active so you can bring the window back. The default Focus mode window adjustment key is `Alt`.

## Download and run

**Download the app:** [QuietReader 0.1.6 for Windows x64](https://github.com/Nore07/Quiet-Reader/releases/download/v0.1.6/QuietReader-0.1.6-Windows-x64.zip) ([SHA-256 checksum](https://github.com/Nore07/Quiet-Reader/releases/download/v0.1.6/QuietReader-0.1.6-Windows-x64.zip.sha256)) is available on [GitHub Releases](https://github.com/Nore07/Quiet-Reader/releases). The archive does not include personal settings or books. After extraction, run `QuietReader.exe`; it creates a `data\` folder beside the executable. Keep all files in the extracted directory while running the app.

**Run from source:** Download or clone this repository. Python 3.12 or newer is required. From the repository root in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

To open a local book directly:

```powershell
.\.venv\Scripts\python.exe run.py "D:\Books\sample.epub"
```

## Formats and local data

QuietReader currently targets common TXT and unencrypted, reflowable EPUB files. DRM-protected resources, fixed-layout EPUB, active content, and external network resources are not supported. Complex publisher styles may affect layout.

Preferences and books are stored in a `data` folder under the reader root. For source runs, this is `data\` under the repository root. For a release, it is `data\` beside `QuietReader.exe`. Configuration, reading positions, and bookmarks are in `data\state.json`; imported book copies are under `data\bookshelf\uncategorized\` or `data\bookshelf\<category>\`. The on-disk names `bookshelf` and `uncategorized` are English; the interface still translates the labels according to the selected language.

The source version and release use their own root-level `data` folders and do not synchronize by default. To share data between them, pass the same `--data-dir <directory>` to both. On first use of the default path, if the target `data` folder is empty, data from the previous `%LOCALAPPDATA%\QuietReader\QuietReader\` location is copied there; the old directory is retained. Keep the release's `data` folder when updating. If you extract a release to a new directory, copy the old `data` folder alongside the new `QuietReader.exe`.

When a copied `data` folder is used from a new application directory, startup checks whether saved bookshelf records still point to the previous location. If the matching book files exist under the new `data\bookshelf`, QuietReader updates their paths and IDs while retaining reading progress, bookmarks, and recent-reading state. Back up `data` before relocating it. Records are left unchanged when a matching file cannot be found; the old data is not deleted.

## Contributing

Report bugs and suggest improvements through [GitHub Issues](https://github.com/Nore07/Quiet-Reader/issues). See the [contribution guide](CONTRIBUTING.md) for branch, commit, and pull request workflow.

## License

QuietReader source code is licensed under the [Apache License 2.0](LICENSE) (SPDX: `Apache-2.0`). See [`docs/third-party.md`](docs/third-party.md) for third-party dependency and distribution notes.
