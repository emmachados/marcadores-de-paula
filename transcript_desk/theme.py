"""Colour and spacing tokens for the light and dark themes, in sRGB."""

LIGHT = {
    "paper": "#f3f3f3", "surface": "#ffffff", "ink": "#242424",
    "muted": "#646464", "line": "#dcdcdc", "accent": "#245ea8",
    "accent_hover": "#1a4b8b", "accent_ink": "#ffffff", "selection": "#e2ecf8",
    "disabled": "#ededed", "error": "#ac2935", "success": "#326847",
    "highlight": "#ffe5a3", "highlight_ink": "#332400",
    "body": "Segoe UI", "mono": "Consolas",
}
DARK = {
    **LIGHT, "paper": "#252525", "surface": "#1c1c1c", "ink": "#e8e8e8",
    "muted": "#aaaaaa", "line": "#3c3c3c", "accent": "#96bbec",
    "accent_hover": "#b0ccf2", "accent_ink": "#182b45", "selection": "#293c55",
    "disabled": "#303030", "error": "#f3a5aa", "success": "#a4c7af",
    "highlight": "#d4b46b", "highlight_ink": "#231c0c",
}
TOKENS = DARK


def palette_tokens(mode="dark"):
    return LIGHT if mode == "light" else DARK


def apply_theme(application, mode="dark"):
    from PySide6.QtGui import QColor, QPalette
    t = palette_tokens(mode)
    palette = QPalette()
    for role, key in ((QPalette.Window, "paper"), (QPalette.WindowText, "ink"),
                      (QPalette.Base, "surface"), (QPalette.AlternateBase, "paper"),
                      (QPalette.Text, "ink"), (QPalette.Button, "surface"),
                      (QPalette.ButtonText, "ink"), (QPalette.Highlight, "selection"),
                      (QPalette.HighlightedText, "ink"), (QPalette.ToolTipBase, "surface"),
                      (QPalette.ToolTipText, "ink"), (QPalette.PlaceholderText, "muted")):
        palette.setColor(role, QColor(t[key]))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor(t["muted"]))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(t["muted"]))
    application.setPalette(palette)
    application.setStyleSheet(stylesheet(mode))


def stylesheet(mode="dark"):
    t = palette_tokens(mode)
    from pathlib import Path
    arrow = (Path(__file__).parent / "assets" / "chevron.svg").as_posix()
    return f"""
    QWidget {{ color: {t['ink']}; font-family: '{t['body']}'; font-size: 13px; }}
    QMainWindow, QDialog {{ background: {t['paper']}; }}
    QWidget#player {{ background: {t['paper']}; border-top: 1px solid {t['line']}; }}
    QWidget#surface {{ background: {t['paper']}; }}
    QWidget#document {{ background: {t['surface']}; }}
    QWidget#commandBar {{ background: {t['paper']}; border-bottom: 1px solid {t['line']}; }}
    QLabel#title {{ font-size: 14px; font-weight: 600; }}
    QLabel#recordingTitle {{ font-size: 22px; font-weight: 600; }}
    QLabel#section {{ font-size: 17px; font-weight: 600; }}
    QLabel#muted {{ color: {t['muted']}; }}
    QLabel#error {{ color: {t['error']}; }}
    QLabel#success {{ color: {t['success']}; }}
    QGroupBox {{ border: 1px solid {t['line']}; border-radius: 6px; margin-top: 14px; padding: 14px 10px 10px; font-weight: 600; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; }}
    QPushButton, QToolButton {{ background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 4px; padding: 5px 10px; min-height: 20px; }}
    QPushButton:hover, QToolButton:hover {{ border-color: {t['accent']}; background: {t['selection']}; }}
    QPushButton:pressed, QToolButton:pressed {{ background: {t['line']}; }}
    QPushButton#primary {{ background: {t['accent']}; color: {t['accent_ink']}; border-color: {t['accent']}; font-weight: 600; }}
    QPushButton#primary:hover {{ background: {t['accent_hover']}; }}
    QPushButton:disabled, QToolButton:disabled {{ background: {t['disabled']}; color: {t['muted']}; border-color: {t['line']}; }}
    QPushButton#primary:disabled {{ background: {t['disabled']}; color: {t['muted']}; border-color: {t['line']}; }}
    QPushButton:focus, QToolButton:focus {{ border: 2px solid {t['ink']}; padding: 4px 9px; }}
    QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox {{ background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 4px; padding: 6px; selection-background-color: {t['selection']}; selection-color: {t['ink']}; }}
    QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus {{ border: 2px solid {t['accent']}; padding: 5px; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox::down-arrow {{ image: url('{arrow}'); width: 10px; height: 6px; }}
    QComboBox QAbstractItemView {{ background: {t['surface']}; selection-background-color: {t['selection']}; selection-color: {t['ink']}; }}
    QCheckBox {{ spacing: 8px; padding: 3px 0; }}
    QCheckBox::indicator {{ width: 17px; height: 17px; }}
    QTableWidget {{ background: {t['surface']}; alternate-background-color: {t['paper']}; border: 1px solid {t['line']}; border-radius: 4px; gridline-color: {t['line']}; selection-background-color: {t['selection']}; selection-color: {t['ink']}; }}
    QTableWidget::item {{ padding: 6px; }}
    QTableWidget#transcript {{ border: none; font-size: 16px; }}
    QTableWidget#recordings {{ border: none; background: {t['paper']}; }}
    QTableWidget#recordings::item {{ padding: 8px 6px; }}
    QHeaderView::section {{ background: {t['paper']}; color: {t['muted']}; border: none; border-bottom: 1px solid {t['line']}; padding: 8px; font-weight: 600; }}
    QProgressBar {{ border: none; background: {t['disabled']}; border-radius: 3px; height: 7px; max-height: 7px; }}
    QProgressBar::chunk {{ background: {t['accent']}; border-radius: 3px; }}
    QTabWidget::pane {{ border: none; background: {t['surface']}; }}
    QTabBar::tab {{ padding: 10px 16px; color: {t['muted']}; background: {t['paper']}; border: none; }}
    QTabBar::tab:selected {{ color: {t['ink']}; background: {t['surface']}; }}
    QSlider::groove:horizontal {{ height: 4px; background: {t['line']}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {t['accent']}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ background: {t['accent']}; width: 12px; margin: -5px 0; border-radius: 6px; }}
    QSplitter::handle {{ background: {t['line']}; width: 1px; }}
    QToolTip {{ color: {t['ink']}; background: {t['surface']}; border: 1px solid {t['line']}; padding: 6px; }}
    QScrollArea {{ border: none; background: transparent; }}
    QMenu {{ background: {t['surface']}; border: 1px solid {t['line']}; padding: 4px; }}
    QMenu::item {{ padding: 7px 20px; }}
    QMenu::item:selected {{ background: {t['selection']}; }}
    """
