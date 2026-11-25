"""Theme palette bridge and unified QSS stylesheet builder."""

from quietreader.ui.tokens import (
    RADIUS_CARD, RADIUS_CONTROL, RADIUS_WINDOW, UI_FONT_STACK, resolve_palette,
)


def colors(theme, background_color=""):
    return resolve_palette(theme, background_color)


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    cleaned = hex_color.lstrip("#")
    return (
        int(cleaned[0:2], 16),
        int(cleaned[2:4], 16),
        int(cleaned[4:6], 16),
    )


def stylesheet(palette, alpha=255, borderless=False, resize_outline=False, drop_highlight=False):
    c = palette
    paper_r, paper_g, paper_b = _hex_to_rgb(c["paper"])
    rgb = f"{paper_r},{paper_g},{paper_b}"
    alpha_percent = alpha * 100 / 255
    elevated = c.get("elevated", c["paper"])
    ink_secondary = c.get("ink_secondary", c["ink"])
    line_strong = c.get("line_strong", c["line"])
    accent_soft = c.get("accent_soft", c["selected"])
    accent_hover = c.get("accent_hover", c["button"])
    on_accent = c.get("on_accent", c["paper"])
    danger = c.get("danger", "#c7463b")
    brand_color = c["ink"] if c.get("custom_background") else c["accent"]

    if borderless:
        focus_ratio = max(0.0, min(1.0, alpha / 255.0))
        card_alpha_pct = (0.76 + 0.19 * focus_ratio) * 100.0
        if c.get("custom_background"):
            is_dark_paper = (0.2126 * paper_r + 0.7152 * paper_g + 0.0722 * paper_b) < 128.0
            if is_dark_paper:
                card_rgb = ",".join(str(round(ch + (255 - ch) * 0.12)) for ch in (paper_r, paper_g, paper_b))
                term_card_border = "1px solid rgba(255,255,255,16%)"
                term_card_divider = "rgba(255,255,255,10%)"
            else:
                card_rgb = ",".join(str(round(ch * 0.96)) for ch in (paper_r, paper_g, paper_b))
                term_card_border = "1px solid rgba(0,0,0,14%)"
                term_card_divider = "rgba(0,0,0,8%)"
            term_card_bg = f"rgba({card_rgb},{card_alpha_pct:.2f}%)"
            term_card_title = c["ink"]
            term_card_body = ink_secondary
        else:
            el_rgb = ",".join(str(ch) for ch in _hex_to_rgb(elevated))
            term_card_bg = f"rgba({el_rgb},{card_alpha_pct:.2f}%)"
            term_card_border = f"1px solid {line_strong}"
            term_card_divider = c["line"]
            term_card_title = c["accent"]
            term_card_body = ink_secondary
    else:
        term_card_bg = elevated
        term_card_border = f"1px solid {line_strong}"
        term_card_divider = c["line"]
        term_card_title = c["accent"]
        term_card_body = ink_secondary

    if resize_outline:
        surface_frame = f"border: 1px dashed {c['accent']}; border-radius: 2px;"
    elif drop_highlight:
        surface_frame = f"border: 2px dashed {c['accent']}; border-radius: {RADIUS_WINDOW}px;"
    else:
        surface_frame = (
            f"border: 1px solid {c['line']}; border-radius: {RADIUS_WINDOW}px;"
            if alpha and not borderless
            else "border: none; border-radius: 0;"
        )

    return f"""
    QWidget {{ color: {c['ink']}; font-family: {UI_FONT_STACK}; font-size: 13px; }}
    QMainWindow {{ background: transparent; }}
    #surface {{ background: rgba({rgb},{alpha_percent:.3f}%); {surface_frame} }}

    /* Unified single-row top bar */
    QWidget#titleBar {{ background: transparent; border-bottom: 1px solid {c['line']}; }}
    QLabel#brandMark {{
        background: {accent_soft};
        border: 1px solid {c['line']};
        border-radius: 7px;
    }}
    QLabel#brand {{ font-size: 15px; font-weight: 600; letter-spacing: 1px; color: {brand_color}; }}
    QLabel#eyebrow {{ font-size: 10px; letter-spacing: 1.6px; color: {c['muted']}; }}
    QLabel#headerCrumb {{ font-size: 12px; color: {c['muted']}; padding: 0 6px; }}
    QFrame#toolbarSep {{ background: {line_strong}; border: none; max-width: 1px; min-width: 1px; }}

    /* Segmented navigation & controls */
    QWidget#pageNavigation, QFrame#segmentedControl {{
        background: {c['side']};
        border: 1px solid {c['line']};
        border-radius: 8px;
    }}
    QPushButton#pageNav, QPushButton#segmentOption {{
        color: {c['muted']};
        background: transparent;
        border: 1px solid transparent;
        border-radius: 6px;
        padding: 2px 7px;
        min-height: 20px;
        font-size: 12px;
        font-weight: 500;
    }}
    QPushButton#pageNav:hover, QPushButton#segmentOption:hover {{
        color: {c['ink']};
        background: {accent_hover};
    }}
    QPushButton#pageNav:checked, QPushButton#segmentOption:checked {{
        color: {c['ink']};
        background: {elevated};
        border-color: {c['line']};
        font-weight: 600;
    }}

    /* Quiet vector icon buttons & Aa button */
    QPushButton#iconButton {{
        color: {c['muted']};
        background: transparent;
        border: 1px solid transparent;
        border-radius: {RADIUS_CONTROL}px;
        padding: 0;
        min-height: 0;
    }}
    QPushButton#iconButton:hover {{
        color: {c['ink']};
        background: {accent_hover};
        border-color: {c['line']};
    }}
    QPushButton#iconButton:checked {{
        color: {c['accent']};
        background: {accent_soft};
        border-color: {c['line']};
    }}
    QPushButton#iconButton:disabled {{
        color: {line_strong};
        background: transparent;
        border-color: transparent;
    }}
    QPushButton#typographyButton {{
        color: {c['ink']};
        background: transparent;
        border: 1px solid transparent;
        border-radius: {RADIUS_CONTROL}px;
        padding: 0 4px;
        min-height: 0;
        font-family: 'Noto Serif SC', Georgia, serif;
        font-size: 13px;
        font-weight: 600;
    }}
    QPushButton#typographyButton:hover {{
        background: {accent_hover};
        border-color: {c['line']};
    }}
    QPushButton#typographyButton:checked {{
        color: {c['accent']};
        background: {accent_soft};
        border-color: {c['line']};
    }}

    /* Window controls */
    QPushButton#windowControl, QPushButton#windowClose {{
        color: {c['muted']};
        background: transparent;
        border: none;
        padding: 0;
        min-height: 0;
        border-radius: 6px;
    }}
    QPushButton#windowControl:hover {{
        color: {c['ink']};
        background: {accent_hover};
    }}
    QPushButton#windowClose:hover {{
        color: #ffffff;
        background: {danger};
    }}

    /* Floating popover card */
    QFrame#floatingPopover {{
        background: {elevated};
        border: 1px solid {line_strong};
        border-radius: 12px;
    }}
    QScrollArea#appearanceScroll, QWidget#appearanceViewport, QWidget#appearanceContent {{
        background: {elevated};
        border: none;
    }}
    QLabel#popoverHeading {{ font-size: 13.5px; font-weight: 600; color: {c['ink']}; }}
    QLabel#popoverBadge {{
        font-size: 10.5px;
        font-weight: 600;
        color: {c['muted']};
        background: {c['side']};
        border: 1px solid {c['line']};
        border-radius: 5px;
        padding: 1px 6px;
    }}
    QLabel#popoverLabel {{ font-size: 11.5px; font-weight: 500; color: {c['muted']}; }}
    QLabel#popoverValue {{ font-size: 11.5px; font-weight: 600; color: {c['ink']}; }}
    QPushButton#popoverActionBtn {{
        color: {ink_secondary};
        background: {c['side']};
        border: 1px solid {c['line']};
        border-radius: 6px;
        padding: 4px 7px;
        min-height: 20px;
        font-size: 11.5px;
    }}
    QPushButton#popoverActionBtn:hover {{
        color: {c['ink']};
        background: {accent_hover};
        border-color: {line_strong};
    }}
    QFrame#popoverDivider {{ background: {c['line']}; border: none; }}
    QPushButton#stepperButton {{
        color: {c['ink']};
        background: {accent_hover};
        border: 1px solid {c['line']};
        border-radius: 6px;
        padding: 0;
        min-height: 0;
        font-size: 13px;
        font-weight: 600;
    }}
    QPushButton#stepperButton:hover {{ background: {c['selected']}; border-color: {line_strong}; }}
    QPushButton#popoverLink {{
        color: {c['accent']};
        background: transparent;
        border: none;
        padding: 2px 4px;
        min-height: 0;
        font-size: 11.5px;
        font-weight: 600;
    }}
    QPushButton#popoverLink:hover {{ text-decoration: underline; background: transparent; border: none; }}

    /* Integrated Foliate-style reading sidebar */
    QFrame#sidebarBookCard {{
        background: transparent;
        border: none;
        border-bottom: 1px solid {c['line']};
    }}
    QLabel#sidebarBookTitle {{ font-size: 13.5px; font-weight: 600; color: {c['ink']}; }}
    QLabel#sidebarBookMeta {{ font-size: 11px; color: {c['muted']}; }}
    QFrame#sidebarBottomBar {{
        background: transparent;
        border: none;
        border-top: 1px solid {c['line']};
    }}
    QPushButton#sidebarTabBtn, QToolButton#sidebarTabBtn {{
        color: {c['muted']};
        background: transparent;
        border: 1px solid transparent;
        border-radius: 7px;
        padding: 4px 2px;
        min-height: 24px;
        font-size: 10.5px;
        font-weight: 500;
    }}
    QPushButton#sidebarTabBtn:hover, QToolButton#sidebarTabBtn:hover {{ color: {c['ink']}; background: {accent_hover}; }}
    QPushButton#sidebarTabBtn:checked, QToolButton#sidebarTabBtn:checked {{ color: {c['accent']}; background: {accent_soft}; font-weight: 600; }}

    /* Surfaces & typography */
    QWidget#sidebar {{ background: {c['side']}; border: 1px solid {c['line']}; border-radius: {RADIUS_CARD}px; }}
    QWidget#shelfToolbar {{ background: transparent; }}
    QWidget#shelfActions QPushButton {{
        padding: 3px 11px;
        min-height: 18px;
        font-size: 12px;
    }}
    QWidget#shelfActions QPushButton#primary {{
        padding: 3px 12px;
        min-height: 18px;
        font-size: 12px;
    }}
    QLabel#bookTitle {{ font-size: 18px; font-weight: 600; color: {c['ink']}; }}
    QLabel#chapterTitle {{ font-size: 13px; color: {c['muted']}; }}
    QLabel#status {{ color: {c['muted']}; font-size: 11px; }}
    QLabel#pageStatus {{ color: {c['muted']}; font-size: 11.5px; padding: 0 4px; }}
    QLabel#toolbarLabel {{ color: {c['muted']}; font-size: 12px; }}

    /* General buttons */
    QPushButton {{
        color: {c['ink']};
        background: {c['button']};
        border: 1px solid {c['line']};
        padding: 5px 12px;
        min-height: 20px;
        border-radius: {RADIUS_CONTROL}px;
        font-size: 12.5px;
    }}
    QPushButton:hover {{ background: {c['selected']}; border-color: {line_strong}; }}
    QPushButton:pressed {{ background: {c['accent']}; color: {on_accent}; border-color: {c['accent']}; }}
    QPushButton:checked {{ background: {accent_soft}; border-color: {c['accent']}; color: {c['accent']}; }}
    QPushButton:disabled {{ color: {c['muted']}; background: transparent; border-color: transparent; }}
    QPushButton#primary {{
        color: {on_accent};
        background: {c['accent']};
        border-color: {c['accent']};
        padding: 5px 13px;
        font-weight: 600;
    }}
    QPushButton#primary:hover {{ background: {c['selected']}; color: {c['ink']}; border-color: {c['accent']}; }}
    QPushButton#quietAction {{ color: {c['muted']}; background: transparent; border-color: transparent; }}
    QPushButton#quietAction:hover {{ color: {c['ink']}; background: {accent_hover}; border-color: {c['line']}; }}
    QPushButton#secondaryAction {{ color: {ink_secondary}; background: {c['button']}; border-color: {c['line']}; }}
    QPushButton#secondaryAction:hover {{ color: {c['ink']}; background: {c['selected']}; }}
    QPushButton#bookmarkAction {{ padding: 4px 6px; min-height: 18px; font-size: 11.5px; }}
    QPushButton#pageControl {{
        color: {ink_secondary};
        background: transparent;
        border: 1px solid transparent;
        border-radius: 6px;
        padding: 4px 8px;
        min-width: 26px;
        min-height: 20px;
        font-size: 12px;
    }}
    QPushButton#pageControl:hover {{
        color: {c['ink']};
        background: {accent_hover};
        border-color: {c['line']};
    }}
    QPushButton#pageControl:disabled {{
        color: {line_strong};
        background: transparent;
        border-color: transparent;
    }}

    /* Lists & shelf (selection-color explicitly fixes QPalette.HighlightedText in Light/Sepia) */
    QListWidget {{
        background: transparent;
        border: none;
        outline: 0;
        padding: 3px;
        selection-background-color: {c['selected']};
        selection-color: {c['ink']};
    }}
    QListWidget::item {{ padding: 10px 8px; border-radius: 6px; }}
    QListWidget::item:selected {{ background: {c['selected']}; color: {c['ink']}; }}
    QListWidget::item:hover {{ background: {accent_hover}; }}
    QListWidget#termNotesList::item {{ padding: 7px 8px; border-radius: 6px; }}
    QListWidget#shelfList {{
        background: transparent;
        border: none;
        padding: 2px;
        selection-background-color: {c['selected']};
        selection-color: {c['ink']};
    }}
    QListWidget#shelfList::item {{
        padding: 0;
        border: none;
        background: transparent;
    }}
    QListWidget#shelfList::item:selected, QListWidget#shelfList::item:hover {{
        background: transparent;
    }}

    /* Phase 3: Two-pane bookshelf rail & toolbar */
    QFrame#shelfRail {{
        background: {c['side']};
        border: 1px solid {c['line']};
        border-radius: {RADIUS_CARD}px;
    }}
    QLabel#railSectionLabel {{
        font-size: 11px;
        font-weight: 600;
        color: {c['muted']};
        letter-spacing: 0.6px;
    }}
    QPushButton#railRenameBtn, QPushButton#railDeleteBtn {{
        color: {c['muted']};
        background: transparent;
        border: 1px solid transparent;
        border-radius: 5px;
        padding: 0 4px;
        min-height: 0;
        font-size: 11px;
    }}
    QPushButton#railDeleteBtn:hover {{
        color: {danger};
        background: {accent_hover};
        border-color: {c['line']};
    }}
    QPushButton#railRenameBtn:hover {{
        color: {c['accent']};
        background: {accent_hover};
        border-color: {c['line']};
    }}
    QListWidget#shelfCategoryList {{
        background: transparent;
        border: none;
        outline: 0;
        padding: 0;
        selection-background-color: {accent_soft};
        selection-color: {c['ink']};
    }}
    QListWidget#shelfCategoryList::item {{
        padding: 0;
        margin: 1px 0;
        border-radius: 7px;
    }}
    QListWidget#shelfCategoryList::item:selected {{
        background: {accent_soft};
        color: {c['ink']};
    }}
    QListWidget#shelfCategoryList::item:hover {{
        background: {accent_hover};
    }}
    QLineEdit#shelfSearch {{
        background: {elevated};
        border: 1px solid {c['line']};
        border-radius: 8px;
        padding: 4px 10px;
        min-height: 22px;
        font-size: 12.5px;
    }}
    QLineEdit#shelfSearch:hover {{ border-color: {line_strong}; }}
    QLineEdit#shelfSearch:focus {{ border-color: {c['accent']}; }}
    #sidebar QLineEdit#shelfSearch {{
        padding: 3px 8px;
        min-height: 20px;
        font-size: 12px;
    }}

    /* Sidebar book search & multi-step jump-back bar */
    QFrame#sidebarSearchBar {{
        background: {c['side']};
        border: 1px solid {c['line']};
        border-radius: 9px;
    }}
    QLineEdit#sidebarSearchInput {{
        background: {elevated};
        border: 1px solid {c['line']};
        border-radius: 6px;
        padding: 2px 8px;
        min-height: 20px;
        font-size: 12.5px;
    }}
    QLineEdit#sidebarSearchInput:hover {{ border-color: {line_strong}; }}
    QLineEdit#sidebarSearchInput:focus {{ border-color: {c['accent']}; }}
    QLabel#sidebarSearchCount {{
        color: {c['muted']};
        font-size: 11.5px;
        padding: 0 4px;
    }}
    QLabel#searchEmptyHint {{ color: {c['muted']}; font-size: 12px; padding: 14px 8px; }}
    QListView#searchResults {{
        background: transparent;
        border: none;
        outline: 0;
        padding: 2px;
    }}
    QListView#searchResults::item {{ padding: 0; border: none; }}
    QPushButton#jumpBackButton, QPushButton#jumpForwardButton {{
        color: {c['muted']};
        background: transparent;
        border: 1px solid transparent;
        border-radius: 6px;
        padding: 0;
        min-width: 20px;
        max-width: 20px;
        min-height: 18px;
        max-height: 20px;
        font-size: 14px;
    }}
    QPushButton#jumpBackButton:hover, QPushButton#jumpForwardButton:hover {{
        color: {c['accent']};
        background: {accent_soft};
        border-color: transparent;
    }}
    QPushButton#jumpBackButton:pressed, QPushButton#jumpForwardButton:pressed {{
        color: {on_accent};
        background: {c['accent']};
        border-color: {c['accent']};
    }}
    QLabel#jumpTargetLabel {{ color: {ink_secondary}; font-size: 11.5px; }}
    QComboBox#shelfSortCombo {{
        background: {c['side']};
        border: 1px solid {c['line']};
        border-radius: 8px;
        padding: 3px 26px 3px 10px;
        min-height: 22px;
        font-size: 12px;
        color: {ink_secondary};
    }}
    QComboBox#shelfSortCombo:hover {{ background: {accent_hover}; border-color: {line_strong}; color: {c['ink']}; }}
    QComboBox#shelfSortCombo:focus {{ border-color: {c['accent']}; }}
    QComboBox#shelfSortCombo::drop-down {{ subcontrol-origin: padding; subcontrol-position: top right; width: 22px; border: none; }}
    QComboBox#shelfSortCombo::down-arrow {{ image: none; width: 0; height: 0; border: none; }}
    QFrame#viewToggleBox {{
        background: {c['side']};
        border: 1px solid {c['line']};
        border-radius: 8px;
    }}

    /* Tabs */
    QTabWidget::pane {{ border: none; top: 1px; }}
    QTabBar::tab {{
        color: {c['muted']};
        padding: 8px 14px;
        margin-right: 4px;
        border-bottom: 2px solid transparent;
        font-size: 12.5px;
    }}
    QTabBar::tab:hover {{ color: {c['ink']}; background: {accent_hover}; border-top-left-radius: 6px; border-top-right-radius: 6px; }}
    QTabBar::tab:selected {{ color: {c['accent']}; border-bottom: 2px solid {c['accent']}; font-weight: 600; }}

    /* Progress & scrollbars */
    QProgressBar#bookProgress {{ background: {c.get('track', c['button'])}; border: none; border-radius: 2px; }}
    QProgressBar#bookProgress::chunk {{ background: {c['accent']}; border-radius: 2px; }}
    QScrollBar:vertical {{ background: transparent; width: 7px; margin: 2px 1px; }}
    QScrollBar::handle:vertical {{ background: {line_strong}; min-height: 24px; border-radius: 3px; }}
    QScrollBar::handle:vertical:hover {{ background: {c['muted']}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    QScrollBar:horizontal {{ background: transparent; height: 7px; margin: 1px 2px; }}
    QScrollBar::handle:horizontal {{ background: {line_strong}; min-width: 24px; border-radius: 3px; }}
    QScrollBar::handle:horizontal:hover {{ background: {c['muted']}; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}

    /* Dialogs, inputs, menus & file dialog */
    QDialog {{ background: {c['paper']}; }}
    QScrollArea#preferencesScroll {{ background: {c['paper']}; border: 1px solid {c['line']}; border-radius: 8px; }}
    QWidget#preferencesContent, QWidget#preferencesViewport {{ background: {c['paper']}; }}
    QComboBox QAbstractItemView {{ background: {elevated}; color: {c['ink']}; selection-background-color: {c['selected']}; selection-color: {c['ink']}; border: 1px solid {c['line']}; border-radius: 8px; padding: 4px; outline: 0; }}
    QComboBox, QSpinBox, QDoubleSpinBox, QFontComboBox, QLineEdit, QPlainTextEdit {{
        background: {elevated};
        border: 1px solid {c['line']};
        border-radius: {RADIUS_CONTROL}px;
        padding: 5px 9px;
        min-height: 22px;
    }}
    QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QFontComboBox:hover, QLineEdit:hover, QPlainTextEdit:hover {{ border-color: {line_strong}; }}
    QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QFontComboBox:focus, QLineEdit:focus, QPlainTextEdit:focus {{ border-color: {c['accent']}; }}
    QPushButton#dangerAction {{
        color: {danger};
        background: transparent;
        border: 1px solid {c['line']};
    }}
    QPushButton#dangerAction:hover {{
        color: #ffffff;
        background: {danger};
        border-color: {danger};
    }}

    /* Floating term note hover preview card */
    QFrame#termHoverCard {{
        background: {term_card_bg};
        border: {term_card_border};
        border-radius: 10px;
    }}
    QLabel#termHoverTitle {{
        color: {term_card_title};
        background: transparent;
        font-size: 13px;
        font-weight: 600;
    }}
    QFrame#termHoverDivider {{
        background: {term_card_divider};
        border: none;
    }}
    QLabel#termHoverBody {{
        color: {term_card_body};
        background: transparent;
        font-size: 12px;
    }}
    QCheckBox {{ padding: 5px 0; }}
    QToolTip {{ background: {elevated}; color: {c['ink']}; border: 1px solid {line_strong}; border-radius: 6px; padding: 4px 8px; }}
    QMenu {{ background: {elevated}; color: {c['ink']}; border: 1px solid {line_strong}; border-radius: 8px; padding: 5px; }}
    QMenu::item {{ color: {c['ink']}; background: transparent; padding: 7px 24px 7px 10px; border-radius: 5px; }}
    QMenu::item:selected {{ color: {c['ink']}; background: {c['selected']}; }}
    QMenu::item:disabled {{ color: {c['muted']}; }}
    QMenu::separator {{ height: 1px; background: {c['line']}; margin: 4px 8px; }}

    /* Book info & file dialog */
    QScrollArea#bookInfoScroll {{ background: transparent; border: none; }}
    QWidget#bookInfoViewport, QWidget#bookInfoContent {{ background: transparent; color: {c['ink']}; }}
    QLabel#bookInfoDetails {{ color: {c['ink']}; background: transparent; }}
    QLabel#bookCoverLabel {{ color: {c['muted']}; background: transparent; }}
    QPushButton#fileNavButton {{ padding: 0; min-height: 0; font-size: 14px; }}
    QSplitter#fileDialogSplitter::handle {{ background: {c['line']}; width: 1px; margin: 4px 4px; }}
    QListWidget#fileDialogPlaces {{ background: {c['side']}; border: 1px solid {c['line']}; border-radius: 8px; padding: 5px; outline: 0; selection-background-color: {c['selected']}; selection-color: {c['ink']}; }}
    QListWidget#fileDialogPlaces::item {{ padding: 7px 10px; border-radius: 6px; }}
    QListWidget#fileDialogPlaces::item:selected {{ background: {c['selected']}; color: {c['ink']}; }}
    QListWidget#fileDialogPlaces::item:hover {{ background: {accent_hover}; }}
    QTreeView#fileDialogView {{ background: {elevated}; color: {c['ink']}; border: 1px solid {c['line']}; border-radius: 8px; outline: 0; padding: 0; selection-background-color: {c['selected']}; selection-color: {c['ink']}; }}
    QTreeView#fileDialogView::item {{ padding: 5px 8px; }}
    QTreeView#fileDialogView::item:selected {{ background: {c['selected']}; color: {c['ink']}; }}
    QTreeView#fileDialogView::item:hover {{ background: {accent_hover}; }}
    QTreeView#fileDialogView QHeaderView::section {{ background: {c['side']}; color: {c['muted']}; border: none; border-bottom: 1px solid {c['line']}; padding: 9px 10px 6px 10px; font-weight: 600; }}
    QTreeView#fileDialogView QHeaderView::section:hover {{ color: {c['ink']}; background: {accent_hover}; }}
    QComboBox#fileFilterCombo {{ background: {c['side']}; border: 1px solid {c['line']}; border-radius: 8px; padding: 6px 32px 6px 10px; min-height: 24px; }}
    QComboBox#fileFilterCombo:hover {{ background: {accent_hover}; border-color: {line_strong}; }}
    QComboBox#fileFilterCombo:focus {{ border-color: {c['accent']}; }}
    QComboBox#fileFilterCombo::drop-down {{ subcontrol-origin: padding; subcontrol-position: top right; width: 28px; border: none; border-top-right-radius: 7px; border-bottom-right-radius: 7px; }}
    QComboBox#fileFilterCombo::drop-down:hover {{ background: {c['selected']}; }}
    QComboBox#fileFilterCombo::down-arrow {{ image: none; width: 0; height: 0; border-left: 4px solid transparent; border-right: 4px solid transparent; border-top: 5px solid {c['muted']}; margin-top: 2px; }}
    QComboBox#fileFilterCombo QAbstractItemView {{ background: {elevated}; color: {c['ink']}; selection-background-color: {c['selected']}; selection-color: {c['ink']}; border: 1px solid {c['line']}; border-radius: 8px; padding: 4px; outline: 0; }}

    /* Toast notice */
    QFrame#toastNotice {{ background: {elevated}; border: 1px solid {c['accent']}; border-radius: 10px; }}
    QLabel#toastLabel {{ color: {c['ink']}; background: transparent; font-size: 12.5px; font-weight: 500; }}
    QPushButton#toastAction {{ color: {c['accent']}; background: {accent_soft}; border: 1px solid {c['accent']}; border-radius: 6px; padding: 3px 10px; min-height: 20px; font-weight: 500; }}
    QPushButton#toastAction:hover {{ background: {c['selected']}; }}

    /* Phase 4: In-page two-column settings page */
    QPushButton#backPill {{
        color: {ink_secondary};
        background: {c['side']};
        border: 1px solid {c['line']};
        border-radius: 7px;
        padding: 3px 10px;
        min-height: 20px;
        font-size: 12px;
        font-weight: 500;
    }}
    QPushButton#backPill:hover {{
        color: {c['ink']};
        background: {accent_hover};
        border-color: {line_strong};
    }}
    QLabel#settingsTopTitle {{
        font-size: 13.5px;
        font-weight: 600;
        color: {c['ink']};
        padding-left: 2px;
    }}
    QFrame#settingsRail {{
        background: {c['side']};
        border: 1px solid {c['line']};
        border-radius: {RADIUS_CARD}px;
    }}
    QLabel#settingsRailFoot {{
        font-size: 11.5px;
        font-weight: 600;
        color: {ink_secondary};
    }}
    QLabel#settingsRailSub {{
        font-size: 10.5px;
        color: {c['muted']};
    }}
    QScrollArea#settingsScroll, QWidget#settingsViewport {{
        background: transparent;
        border: none;
    }}
    QLabel#settingsPageTitle {{
        font-size: 17px;
        font-weight: 600;
        color: {c['ink']};
    }}
    QLabel#settingsPageDesc {{
        font-size: 12px;
        color: {c['muted']};
    }}
    QFrame#settingsCard {{
        background: {elevated};
        border: 1px solid {c['line']};
        border-radius: 12px;
    }}
    QFrame#settingsDivider {{
        background: {c['line']};
        border: none;
    }}
    QLabel#settingsRowTitle {{
        font-size: 13px;
        font-weight: 600;
        color: {c['ink']};
    }}
    QLabel#settingsRowSub {{
        font-size: 11.5px;
        color: {c['muted']};
    }}
    QLabel#settingsMonoValue {{
        font-size: 11.5px;
        color: {ink_secondary};
        font-family: 'Cascadia Mono', 'Consolas', monospace;
    }}
    QPushButton#settingsBtn {{
        color: {ink_secondary};
        background: {c['side']};
        border: 1px solid {c['line']};
        border-radius: 7px;
        padding: 4px 10px;
        min-height: 20px;
        font-size: 12px;
    }}
    QPushButton#settingsBtn:hover {{
        color: {c['ink']};
        background: {accent_hover};
        border-color: {line_strong};
    }}
    QPushButton#settingsKeyBtn {{
        color: {c['ink']};
        background: {c['side']};
        border: 1px solid {c['line']};
        border-radius: 7px;
        padding: 4px 12px;
        min-height: 20px;
        min-width: 110px;
        font-size: 12px;
        font-weight: 500;
    }}
    QPushButton#settingsKeyBtn:hover {{
        background: {accent_hover};
        border-color: {c['accent']};
    }}
    QFrame#settingsFooter {{
        background: transparent;
        border: none;
        border-top: 1px solid {c['line']};
    }}
    """
