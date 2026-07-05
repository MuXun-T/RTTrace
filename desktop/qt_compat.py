from __future__ import annotations

from ctypes.util import find_library
import os
import sys

os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")


def _should_force_offscreen_qpa() -> bool:
    if os.name == "nt" or os.environ.get("QT_QPA_PLATFORM"):
        return False
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return True
    if not sys.platform.startswith("linux") or not os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return False
    return (
        find_library("xcb-cursor") is None
        and find_library("xcb_cursor") is None
        and find_library("libxcb-cursor") is None
    )


if _should_force_offscreen_qpa():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtCore import QObject as QtObject
    from PySide6.QtCore import QAbstractTableModel as QtAbstractTableModel
    from PySide6.QtCore import QTimer as QtTimer
    from PySide6.QtWidgets import QApplication as QtApplication

    QT_AVAILABLE = True
except Exception:  # pragma: no cover - exercised via fallback runtime
    QtCore = None
    QtGui = None
    QtWidgets = None
    QtObject = object
    QtAbstractTableModel = object
    QtTimer = object
    QtApplication = object
    QT_AVAILABLE = False

try:
    import pyqtgraph as pg

    PYQTGRAPH_AVAILABLE = True
except Exception:  # pragma: no cover - exercised via fallback runtime
    pg = None
    PYQTGRAPH_AVAILABLE = False


class CallbackSignal:
    def __init__(self) -> None:
        self._callbacks: list = []

    def connect(self, callback) -> None:
        self._callbacks.append(callback)

    def emit(self, *args, **kwargs) -> None:
        for callback in list(self._callbacks):
            callback(*args, **kwargs)


class _FallbackQt:
    Horizontal = 1
    Vertical = 2
    AlignCenter = 4


if QT_AVAILABLE:
    class QtCallbackSignal(QtCore.QObject):
        triggered = QtCore.Signal(object)

        def connect(self, callback) -> None:
            self.triggered.connect(callback)

        def emit(self, *args, **kwargs) -> None:
            if kwargs or len(args) > 1:
                payload = {"args": args, "kwargs": kwargs}
            elif args:
                payload = args[0]
            else:
                payload = None
            self.triggered.emit(payload)


def new_signal() -> CallbackSignal:
    if QT_AVAILABLE:
        return QtCallbackSignal()
    return CallbackSignal()


class QObject(QtObject):
    pass


class QAbstractTableModel(QtAbstractTableModel):
    pass


class QTimer(QtTimer):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if not QT_AVAILABLE:
            self.timeout = CallbackSignal()
            self._active = False

    def start(self, interval: int | None = None) -> None:
        if QT_AVAILABLE:
            if interval is None:
                super().start()
            else:
                super().start(interval)
            return
        self._active = True

    def stop(self) -> None:
        if QT_AVAILABLE:
            super().stop()
            return
        self._active = False

    def isActive(self) -> bool:
        if QT_AVAILABLE:
            return super().isActive()
        return getattr(self, "_active", False)


class QApplication(QtApplication):
    pass


class _FallbackWidget:
    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs
        self.clicked = CallbackSignal()
        self.currentChanged = CallbackSignal()
        self.currentRowChanged = CallbackSignal()
        self.currentTextChanged = CallbackSignal()
        self.itemSelectionChanged = CallbackSignal()
        self.cellClicked = CallbackSignal()
        self.cellEntered = CallbackSignal()
        self.viewportEntered = CallbackSignal()
        self.returnPressed = CallbackSignal()
        self.activated = CallbackSignal()
        self._items: list = []
        self._rows: list[list[object | None]] = []
        self._row_count = 0
        self._column_count = 0
        self._current_index = 0
        self._current_row = -1
        self._text = str(args[0]) if args and isinstance(args[0], str) else ""
        self._plain_text = ""
        self._enabled = True
        self._orientation = args[0] if args else None

    def show(self) -> None:
        return None

    def hide(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def setVisible(self, visible: bool) -> None:
        self.visible = visible

    def setLayout(self, layout) -> None:
        self.layout = layout

    def setCentralWidget(self, widget) -> None:
        self.central_widget = widget

    def setWindowTitle(self, title: str) -> None:
        self.window_title = title

    def windowTitle(self) -> str:
        return getattr(self, "window_title", "")

    def resize(self, width: int, height: int) -> None:
        self.size = (width, height)

    def addWidget(self, widget, *args, **kwargs) -> None:
        return None

    def addLayout(self, layout, *args, **kwargs) -> None:
        return None

    def addRow(self, *args, **kwargs) -> None:
        return None

    def addStretch(self, stretch: int = 0) -> None:
        return None

    def setReadOnly(self, readonly: bool) -> None:
        self.readonly = readonly

    def setPlainText(self, text: str) -> None:
        self._plain_text = text

    def setText(self, text: str) -> None:
        self._text = text

    def text(self) -> str:
        return self._text

    def toPlainText(self) -> str:
        return self._plain_text

    def clear(self) -> None:
        self._items = []
        self._rows = []
        self._row_count = 0
        return None

    def clearContents(self) -> None:
        self._rows = [[None for _ in range(self._column_count)] for _ in range(self._row_count)]

    def setObjectName(self, name: str) -> None:
        self.object_name = name

    def setStyleSheet(self, style: str) -> None:
        self.style_sheet = style

    def setMaximumWidth(self, width: int) -> None:
        self.maximum_width = width

    def setMinimumWidth(self, width: int) -> None:
        self.minimum_width = width

    def setMaximumHeight(self, height: int) -> None:
        self.maximum_height = height

    def setMinimumHeight(self, height: int) -> None:
        self.minimum_height = height

    def setWordWrap(self, enabled: bool) -> None:
        self.word_wrap = enabled

    def setAlignment(self, alignment) -> None:
        self.alignment = alignment

    def setContentsMargins(self, *args) -> None:
        self.contents_margins = args

    def setSpacing(self, spacing: int) -> None:
        self.spacing = spacing

    def setPlaceholderText(self, text: str) -> None:
        self.placeholder_text = text

    def setToolTip(self, text: str) -> None:
        self.tooltip = text

    def setEnabled(self, enabled: bool) -> None:
        self._enabled = enabled

    def isEnabled(self) -> bool:
        return self._enabled

    def setTitle(self, title: str) -> None:
        self.title = title

    def addItems(self, items) -> None:
        self._items.extend(items)

    def insertItem(self, index: int, item) -> None:
        self._items.insert(index, item)

    def setCurrentIndex(self, index: int) -> None:
        self._current_index = index

    def currentIndex(self) -> int:
        return self._current_index

    def setCurrentText(self, text: str) -> None:
        self._text = text

    def currentText(self) -> str:
        if self._text:
            return self._text
        if 0 <= self._current_index < len(self._items):
            return str(self._items[self._current_index])
        return ""

    def setCurrentRow(self, row: int) -> None:
        self._current_row = row

    def currentRow(self) -> int:
        return self._current_row

    def count(self) -> int:
        return len(self._items)

    def item(self, row: int, column: int | None = None):
        if column is None:
            return self._items[row]
        if 0 <= row < len(self._rows) and 0 <= column < len(self._rows[row]):
            return self._rows[row][column]
        return None

    def addTab(self, widget, label: str) -> int:
        self._items.append((widget, label))
        return len(self._items) - 1

    def setOrientation(self, orientation) -> None:
        self._orientation = orientation

    def orientation(self):
        return self._orientation

    def widget(self, index: int):
        if 0 <= index < len(self._items):
            item = self._items[index]
            if isinstance(item, tuple):
                return item[0]
        return None

    def currentWidget(self):
        return self.widget(self._current_index)

    def setTabToolTip(self, index: int, text: str) -> None:
        return None

    def setColumnCount(self, count: int) -> None:
        self._column_count = count
        self._rows = [[None for _ in range(count)] for _ in range(self._row_count)]

    def setRowCount(self, count: int) -> None:
        self._row_count = count
        self._rows = [[None for _ in range(self._column_count)] for _ in range(count)]

    def setHorizontalHeaderLabels(self, labels) -> None:
        self.header_labels = list(labels)

    def setItem(self, row: int, column: int, value) -> None:
        while len(self._rows) <= row:
            self._rows.append([None for _ in range(self._column_count)])
        while len(self._rows[row]) <= column:
            self._rows[row].append(None)
        self._rows[row][column] = value

    def horizontalHeader(self):
        return self

    def setStretchLastSection(self, enabled: bool) -> None:
        self.stretch_last_section = enabled

    def setSectionResizeMode(self, *args, **kwargs) -> None:
        return None

    def setAlternatingRowColors(self, enabled: bool) -> None:
        self.alternating_row_colors = enabled

    def setSelectionBehavior(self, behavior) -> None:
        self.selection_behavior = behavior

    def setSelectionMode(self, mode) -> None:
        self.selection_mode = mode

    def setMouseTracking(self, enabled: bool) -> None:
        self.mouse_tracking = enabled

    def viewport(self):
        return self

    def setEditTriggers(self, triggers) -> None:
        self.edit_triggers = triggers

    def setSizes(self, sizes) -> None:
        self.sizes = list(sizes)

    def setStretchFactor(self, index: int, factor: int) -> None:
        if not hasattr(self, "stretch_factors"):
            self.stretch_factors = {}
        self.stretch_factors[index] = factor

    def setChildrenCollapsible(self, enabled: bool) -> None:
        self.children_collapsible = enabled

    def setCollapsible(self, index: int, enabled: bool) -> None:
        if not hasattr(self, "collapsible_by_index"):
            self.collapsible_by_index = {}
        self.collapsible_by_index[index] = enabled

    def setHandleWidth(self, width: int) -> None:
        self.handle_width = width

    def setOpaqueResize(self, enabled: bool) -> None:
        self.opaque_resize = enabled

    def setFrameStyle(self, style) -> None:
        self.frame_style = style

    def plot(self, *args, **kwargs) -> None:
        return None

    def addLegend(self, *args, **kwargs) -> None:
        return None

    def addItem(self, item, *args, **kwargs) -> None:  # type: ignore[override]
        self._items.append(item)

    def setBackground(self, *args, **kwargs) -> None:
        return None

    def setLabel(self, *args, **kwargs) -> None:
        return None

    def showGrid(self, *args, **kwargs) -> None:
        return None

    def setXRange(self, *args, **kwargs) -> None:
        return None

    def setYRange(self, *args, **kwargs) -> None:
        return None

    def addLine(self, *args, **kwargs) -> None:
        return None

    def getAxis(self, *args, **kwargs):
        return self

    def setTicks(self, *args, **kwargs) -> None:
        return None


if QT_AVAILABLE:
    Qt = QtCore.Qt
    QWidget = QtWidgets.QWidget
    QMainWindow = QtWidgets.QMainWindow
    QLabel = QtWidgets.QLabel
    QPushButton = QtWidgets.QPushButton
    QSplitter = QtWidgets.QSplitter
    QVBoxLayout = QtWidgets.QVBoxLayout
    QHBoxLayout = QtWidgets.QHBoxLayout
    QGridLayout = QtWidgets.QGridLayout
    QFormLayout = QtWidgets.QFormLayout
    QPlainTextEdit = QtWidgets.QPlainTextEdit
    QTableWidget = QtWidgets.QTableWidget
    QTableWidgetItem = QtWidgets.QTableWidgetItem
    QTabWidget = QtWidgets.QTabWidget
    QGroupBox = QtWidgets.QGroupBox
    QLineEdit = QtWidgets.QLineEdit
    QComboBox = QtWidgets.QComboBox
    QListWidget = QtWidgets.QListWidget
    QListWidgetItem = QtWidgets.QListWidgetItem
    QFrame = QtWidgets.QFrame
    QFileDialog = QtWidgets.QFileDialog
    QMessageBox = QtWidgets.QMessageBox
else:
    Qt = _FallbackQt
    QWidget = _FallbackWidget
    QMainWindow = _FallbackWidget
    QLabel = _FallbackWidget
    QPushButton = _FallbackWidget
    QSplitter = _FallbackWidget
    QVBoxLayout = _FallbackWidget
    QHBoxLayout = _FallbackWidget
    QGridLayout = _FallbackWidget
    QFormLayout = _FallbackWidget
    QPlainTextEdit = _FallbackWidget
    QTableWidget = _FallbackWidget
    QTableWidgetItem = _FallbackWidget
    QTabWidget = _FallbackWidget
    QGroupBox = _FallbackWidget
    QLineEdit = _FallbackWidget
    QComboBox = _FallbackWidget
    QListWidget = _FallbackWidget
    QListWidgetItem = _FallbackWidget
    QFrame = _FallbackWidget
    QFileDialog = _FallbackWidget
    QMessageBox = _FallbackWidget


if PYQTGRAPH_AVAILABLE:
    PlotWidget = pg.PlotWidget
    mkPen = pg.mkPen
    mkBrush = pg.mkBrush
    BarGraphItem = pg.BarGraphItem
    InfiniteLine = pg.InfiniteLine
else:
    class PlotWidget(_FallbackWidget):
        pass

    def mkPen(*args, **kwargs):  # type: ignore[override]
        return None

    def mkBrush(*args, **kwargs):  # type: ignore[override]
        return None

    class BarGraphItem(_FallbackWidget):
        pass

    class InfiniteLine(_FallbackWidget):
        pass


def ensure_qapplication(argv: list[str] | None = None):
    if not QT_AVAILABLE:
        raise RuntimeError("PySide6 is not available")
    app = QtApplication.instance()
    if app is None:
        app = QtApplication(argv or [])
    return app


Signal = CallbackSignal
PYSIDE_AVAILABLE = QT_AVAILABLE
PG_AVAILABLE = PYQTGRAPH_AVAILABLE
