"""Desktop-facing service layer and minimal shell."""

from .context import ContextStore
from .qt_compat import (
    PG_AVAILABLE,
    PYSIDE_AVAILABLE,
    QT_AVAILABLE,
    QApplication,
    QAbstractTableModel,
    QObject,
    PlotWidget,
    new_signal,
)
from .repository import DatasetRecord, DatasetRepository
from .services import (
    BackgroundJobManager,
    BookmarkService,
    CompareService,
    ExportService,
    ReplayService,
    ReproService,
    WorkspaceController,
)

__all__ = [
    "BackgroundJobManager",
    "BookmarkService",
    "CompareService",
    "ContextStore",
    "DatasetRecord",
    "DatasetRepository",
    "ExportService",
    "PG_AVAILABLE",
    "PYSIDE_AVAILABLE",
    "PlotWidget",
    "QAbstractTableModel",
    "QApplication",
    "QT_AVAILABLE",
    "QObject",
    "ReplayService",
    "ReproService",
    "WorkspaceController",
    "new_signal",
]
