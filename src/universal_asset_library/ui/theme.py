APP_STYLESHEET = """
QWidget { background: #1A1D23; color: #D4D4D4; font-family: "Consolas", "JetBrains Mono", "SF Mono", monospace; font-size: 12px; }
QLabel { background: transparent; }
QMainWindow, QTabWidget::pane { background: #1A1D23; }
QTabWidget::pane { border: 1px solid #2F3542; top: -1px; }
QTabBar { background: #1A1D23; }
QTabBar::tab { background: #2F3542; color: #9CA3AF; padding: 12px 24px; border: 1px solid #404754; border-bottom: 0; margin-right: 2px; font-weight: bold; }
QTabBar::tab:selected { color: #FFFFFF; background: #FF6B35; border-color: #D85A28; }
QTabBar::tab:hover:!selected { color: #FFFFFF; background: #404754; }
QTabBar#assetTypeTabs { background: #1E2228; border: 1px solid #2F3542; border-radius: 3px; }
QTabBar#assetTypeTabs::tab { min-width: 92px; padding: 8px 16px; border: 0; border-radius: 2px; margin: 2px; }
QTabBar#assetTypeTabs::tab:selected { background: #FF6B35; color: #FFFFFF; }
QTabBar#assetTypeTabs::tab:hover:!selected { background: #404754; color: #FFFFFF; }
QLineEdit, QComboBox, QTextEdit, QTableWidget, QListWidget { background: #1E2228; border: 1px solid #2F3542; border-radius: 2px; padding: 8px; selection-background-color: #FF6B35; }
QLineEdit:focus, QComboBox:focus, QTextEdit:focus { border-color: #FF6B35; }
QComboBox::drop-down { border: 0; width: 24px; }
QPushButton { background: #2F3542; color: #D4D4D4; border: 1px solid #404754; border-radius: 2px; padding: 8px 14px; font-weight: bold; }
QPushButton:hover { background: #FF6B35; border-color: #FF6B35; color: #FFFFFF; }
QPushButton:pressed { background: #D85A28; border-color: #D85A28; }
QPushButton#primaryButton { background: #FF6B35; border-color: #D85A28; color: #FFFFFF; }
QPushButton#primaryButton:hover { background: #FF8357; border-color: #FF8357; }
QPushButton#primaryButton:disabled { color: #6B7280; background: #252931; border-color: #2F3542; }
QPushButton:disabled { color: #6B7280; background: #252931; border-color: #2F3542; }
QLabel#pageTitle { font-size: 24px; font-weight: bold; color: #FFFFFF; }
QLabel#mutedLabel { color: #9CA3AF; }
QLabel#sectionTitle { font-size: 15px; font-weight: 700; }
QFrame#panel, QGroupBox { background: #1E2228; border: 1px solid #2F3542; border-radius: 2px; }
QFrame#assetsToolbar { background: #20242A; border: 1px solid #2F3542; border-radius: 3px; }
QFrame#assetsToolbar QLineEdit, QFrame#assetsToolbar QComboBox { padding: 6px 8px; }
QFrame#categoryRail { background:#20242A; border:1px solid #2F3542; border-radius:3px; }
QWidget#categoryRailContent, QScrollArea#categoryRailScroll { background:transparent; border:0; }
QToolButton#categoryRailToggle { background:transparent; color:#9CA3AF; border:0; min-height:26px; font-size:18px; }
QToolButton#categoryRailToggle:hover { color:#FFFFFF; background:#2F3542; }
QToolButton#categoryRailButton { background:transparent; color:#B7BDC8; border:1px solid transparent; border-radius:3px; padding:6px; text-align:left; }
QToolButton#categoryRailButton:hover { background:#2B3038; color:#FFFFFF; }
QToolButton#categoryRailButton:checked { background:#FF6B35; color:#FFFFFF; border-color:#D85A28; }
QScrollArea#inspectorScroll, QWidget#inspectorContent { background: #1E2228; border: 0; }
QLabel#inspectorHero { background: #141619; border: 1px solid #2F3542; border-radius: 3px; color: #6B7280; }
QFrame#inspectorSection { background: #1A1D23; border: 1px solid #2F3542; border-radius: 2px; }
QToolButton#inspectorSectionToggle { background: #23272D; color: #D4D4D4; border: 0; padding: 9px 10px; font-weight: bold; text-align: left; }
QToolButton#inspectorSectionToggle:hover { background: #2B3038; color: #FFFFFF; }
QWidget#ratingControl { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #30232D, stop:1 #24252D); border: 1px solid #684457; border-radius: 18px; }
QToolButton#ratingStar { background: #292631; color: #796875; border: 1px solid #493B47; border-radius: 13px; font-size: 18px; padding: 0; }
QToolButton#ratingStar[filled="true"] { color: #FFD86B; background: #5A3B49; border-color: #A8667C; }
QToolButton#ratingStar:hover { color: #FFE9A8; background: #724B5C; border-color: #E18AA7; }
QToolButton#ratingStar[popped="true"] { color: #FFF4C7; background: #8A596D; border: 2px solid #FFB5CC; }
QToolButton#ratingStar:pressed { background: #86576A; }
QToolButton#ratingStar:disabled { color: #59616D; background: transparent; border-color: transparent; }
QWidget#inspectorSectionBody { background: #1A1D23; border: 0; }
QFrame#exportFooter { background: #171A1F; border: 0; border-top: 1px solid #3A404A; }
QFrame#exportFooter QComboBox { padding: 6px 8px; }
QGroupBox { margin-top: 12px; padding: 15px; font-weight: 700; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }
QListView { background: transparent; border: 0; outline: 0; }
QListView::item:selected { background: transparent; }
QScrollBar:vertical { background: transparent; width: 9px; margin: 0; }
QScrollBar::handle:vertical { background: #404754; border-radius: 2px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QHeaderView::section { background: #252931; color: #9CA3AF; border: 0; padding: 8px; }
QTableWidget { gridline-color: #2F3542; }
QStatusBar { background: #1E2228; color: #9CA3AF; border-top: 1px solid #2F3542; }
"""
