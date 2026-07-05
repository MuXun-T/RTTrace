from __future__ import annotations

import argparse
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from parser.models import AnalysisContext, EventTableQuery, TaskStateQuery, dataclass_to_dict

from ..qt_compat import (
    BarGraphItem,
    QFileDialog,
    InfiniteLine,
    PG_AVAILABLE,
    PYSIDE_AVAILABLE,
    QApplication,
    Qt,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTimer,
    QVBoxLayout,
    QWidget,
    PlotWidget,
    ensure_qapplication,
    mkBrush,
    mkPen,
)
from ..sample_data import write_scenario
from ..services import CompareService, ExportService, ReplayService, ReproService, Result, WorkspaceController


def _format_window(window: tuple[float, float] | list[float] | None) -> str:
    if not window:
        return "n/a"
    return f"{float(window[0]):.1f} -> {float(window[1]):.1f}"


def _coerce_int(raw: str) -> int | None:
    value = raw.strip()
    if not value:
        return None
    return int(value, 0)


def _json_text(payload: Any) -> str:
    return json.dumps(dataclass_to_dict(payload), indent=2, ensure_ascii=False)


def _color_for_key(key: str) -> str:
    palette = [
        "#0f6cbd",
        "#2d9d78",
        "#ff7a59",
        "#8e6cef",
        "#da3b01",
        "#008272",
        "#c239b3",
        "#69797e",
    ]
    return palette[sum(ord(ch) for ch in key) % len(palette)]


def _safe_cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _vertical_axis_text(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    if "\n" in cleaned:
        return cleaned
    return "\n".join(ch for ch in cleaned if ch != " ")


def _compact_mapping(payload: dict[str, Any] | None) -> dict[str, Any]:
    return {key: value for key, value in dict(payload or {}).items() if value is not None}


def _format_selection_summary(selection: dict[str, Any] | None) -> str:
    compact = _compact_mapping(selection)
    if not compact:
        return "-"
    return ", ".join(f"{key}={_safe_cell(value)}" for key, value in compact.items())


def _format_hover_target_summary(target: dict[str, Any] | None) -> str:
    if not target:
        return "-"
    target = dict(target)
    view = str(target.get("view") or target.get("kind") or "hover")
    for key in ("ref_key", "label", "event_uid", "task_id", "resource_id", "alert_type", "detail_target"):
        if target.get(key) is not None:
            return f"{view}:{_safe_cell(target[key])}"
    return view


def _compare_detail_dimension(detail: dict[str, Any]) -> str:
    return str((detail.get("target") or {}).get("dimension") or "")


def _compare_detail_target_value(detail: dict[str, Any], id_key: str) -> str:
    return str((detail.get("target") or {}).get(id_key))


def _compare_role_label(role: Any) -> str:
    role_text = str(role or "").strip()
    if role_text == "baseline":
        return "Baseline"
    if role_text == "candidate":
        return "Candidate"
    if role_text == "single":
        return "Single"
    return role_text.title() if role_text else ""


def _compare_main_jump_target(detail: dict[str, Any] | None) -> dict[str, Any] | None:
    jump_target = dict((detail or {}).get("jump_target") or {})
    if not jump_target:
        return None
    jump_target.pop("peer_target", None)
    return jump_target


def _compare_peer_jump_target(detail: dict[str, Any] | None) -> dict[str, Any] | None:
    jump_target = (detail or {}).get("jump_target") or {}
    peer_target = jump_target.get("peer_target")
    return dict(peer_target) if isinstance(peer_target, dict) and peer_target else None


def _compare_jump_button_text(action: str, target: dict[str, Any] | None, fallback: str) -> str:
    role_label = _compare_role_label((target or {}).get("dataset_role"))
    if role_label:
        return f"{action} {role_label} 证据"
    return fallback


def _format_compare_jump_summary(target: dict[str, Any] | None) -> str:
    if not target:
        return "无"
    selection = dict(target.get("selection") or {})
    selection_text = ", ".join(f"{key}={_safe_cell(value)}" for key, value in selection.items()) or "-"
    return (
        f"{_compare_role_label(target.get('dataset_role')) or '-'} | "
        f"time_window={_format_window(target.get('time_window'))} | "
        f"focused_view={target.get('focused_view') or '-'} | "
        f"evidence_anchor={_safe_cell((target.get('evidence_anchor') or {}).get('ref_key'))} | "
        f"selection={selection_text}"
    )


def _format_state_counts(counts: dict[str, Any], legend: dict[str, str] | None = None) -> str:
    if not counts:
        return "-"
    labels = legend or {}
    return ", ".join(f"{labels.get(state, state)}={_safe_cell(count)}" for state, count in counts.items())


def _task_preview_label(task_ids: list[Any]) -> str:
    if not task_ids:
        return "预览"
    visible = [str(item) for item in task_ids[:4]]
    if len(task_ids) > 4:
        visible.append("...")
    return ",".join(visible)


def _task_query_task_filter(
    filter_spec: dict[str, Any],
    effective_selection: dict[str, Any],
) -> list[int]:
    task_ids: list[int] = []
    raw_values = list(filter_spec.get("task_ids") or [])
    if filter_spec.get("task_id") is not None:
        raw_values.append(filter_spec["task_id"])
    if effective_selection.get("task_id") is not None:
        raw_values.append(effective_selection["task_id"])
    for value in raw_values:
        try:
            task_id = int(value)
        except (TypeError, ValueError):
            continue
        if task_id not in task_ids:
            task_ids.append(task_id)
    return task_ids


def _task_query_runtime_filter(
    filter_spec: dict[str, Any],
    effective_selection: dict[str, Any],
) -> dict[str, Any]:
    resource_id = effective_selection.get("resource_id")
    if resource_id is None:
        resource_id = filter_spec.get("resource_id")
    if resource_id is None:
        resource_id = filter_spec.get("obj_id")
    if resource_id is None:
        return {}
    return {"resource_id": resource_id}


def _playback_step_index(state: Any, runtime_payload: dict[str, Any] | None = None) -> int:
    cursor = dict(getattr(state, "cursor", None) or {})
    if cursor.get("step_index") is not None:
        return int(cursor["step_index"])
    runtime = dict(runtime_payload or {})
    if runtime.get("current_index") is not None:
        return int(runtime["current_index"])
    return 0


def _playback_timestamp(state: Any, runtime_payload: dict[str, Any] | None = None) -> float:
    cursor = dict(getattr(state, "cursor", None) or {})
    if cursor.get("timestamp") is not None:
        return float(cursor["timestamp"])
    runtime = dict(runtime_payload or {})
    if runtime.get("cursor_ts") is not None:
        return float(runtime["cursor_ts"])
    return 0.0


def _event_brief(event: dict[str, Any] | None) -> str:
    if not event:
        return "-"
    return (
        f"{event.get('event_name', '?')} "
        f"ts={_safe_cell(event.get('timestamp'))} "
        f"task={_safe_cell(event.get('task_id'))} "
        f"ref={event.get('ref_key', '-')}"
    )


def _format_resource_drilldown_text(item: dict[str, Any], selection: dict[str, Any], drilldown: dict[str, Any] | None) -> str:
    if not drilldown:
        return _json_text({"selected_resource": item, "selection": selection, "drilldown": None})
    if drilldown.get("error") is not None:
        return (
            "资源下钻\n"
            f"资源: {_safe_cell(selection.get('resource_id'))}\n"
            f"错误: {drilldown['error']}"
        )
    summary = drilldown.get("summary") or {}
    hotspot = drilldown.get("hotspot") or {}
    lines = [
        "资源下钻",
        f"资源: obj:{drilldown.get('resource_id', '-')}",
        f"选择: task={_safe_cell(selection.get('task_id'))} resource={_safe_cell(selection.get('resource_id'))}",
        f"热点计数: {_safe_cell(hotspot.get('count'))}",
        f"等待链数: {_safe_cell(summary.get('wait_chain_count'))}",
        f"等待边数: {_safe_cell(summary.get('wait_edge_count'))}",
        f"持有边数: {_safe_cell(summary.get('hold_edge_count'))}",
        "",
        "关键等待链",
    ]
    wait_chains = drilldown.get("wait_chains") or []
    if not wait_chains:
        lines.append("无")
    for index, chain in enumerate(wait_chains[:3], 1):
        lines.extend(
            [
                f"{index}. task {_safe_cell(chain.get('task_id'))} -> obj {_safe_cell(chain.get('obj_id'))} -> owner {_safe_cell(chain.get('owner_task_id'))}",
                f"   wait_event: {_event_brief(chain.get('wait_event'))}",
                f"   hold_event: {_event_brief(chain.get('hold_event'))}",
                f"   trusted: {_safe_cell(chain.get('trusted'))}",
            ]
        )
    lines.extend(["", "相关事件"])
    related_events = drilldown.get("related_events") or []
    if not related_events:
        lines.append("无")
    for event in related_events[:5]:
        lines.append(f"- {_event_brief(event)}")
    jump_target = drilldown.get("jump_target") or {}
    lines.extend(
        [
            "",
            "推荐跳转",
            f"time_window: {_format_window(jump_target.get('time_window'))}",
            f"focused_view: {jump_target.get('focused_view', '-')}",
            f"evidence_anchor: {_safe_cell((jump_target.get('evidence_anchor') or {}).get('ref_key'))}",
        ]
    )
    return "\n".join(lines)


def _format_compare_detail_text(target_label: str, detail_row: dict[str, Any] | None, error: str | None = None) -> str:
    if error is not None:
        return f"对比明细\n对象: {target_label}\n错误: {error}"
    primary = detail_row or {}
    main_jump_target = _compare_main_jump_target(primary)
    peer_jump_target = _compare_peer_jump_target(primary)
    related_events = list(primary.get("related_events") or [])
    lines = [
        "对比明细",
        f"对象: {target_label}",
        f"维度: {_safe_cell(_compare_detail_dimension(primary))}",
        f"diff_id: {_safe_cell(primary.get('diff_id'))}",
        f"证据引用: {len(primary.get('evidence_refs') or [])}",
        f"相关事件: {len(related_events)}",
        "",
        "跳转摘要",
        f"主侧: {_format_compare_jump_summary(main_jump_target)}",
        f"对侧: {_format_compare_jump_summary(peer_jump_target)}",
        "",
        "主侧跳转",
        _json_text(main_jump_target) if main_jump_target is not None else "无",
        "",
        "对侧跳转",
        _json_text(peer_jump_target) if peer_jump_target is not None else "无",
        "",
        "Baseline 视图",
        _json_text(primary.get("baseline_view")) if primary.get("baseline_view") is not None else "无",
        "",
        "Candidate 视图",
        _json_text(primary.get("candidate_view")) if primary.get("candidate_view") is not None else "无",
        "",
        "Delta 摘要",
        _json_text(primary.get("delta_payload")) if primary.get("delta_payload") is not None else "无",
        "",
        "相关事件样本",
    ]
    if not related_events:
        lines.append("无")
    for event in related_events[:5]:
        lines.append(f"- {_event_brief(event)}")
    return "\n".join(lines)


@dataclass
class DatasetSummary:
    dataset_id: str
    source: str
    event_count: int
    slice_count: int
    task_count: int
    irq_count: int
    untrusted_count: int
    window: tuple[float, float]


@dataclass
class PanelAvailability:
    metric_bucket_chart: bool = True
    resource_wait_chain_detail: bool = True
    compare_evidence_drilldown: bool = True
    async_jobs: bool = True

    def notes(self) -> list[str]:
        return [
            (
                "指标时间桶曲线"
                + ("已接后端" if self.metric_bucket_chart else "待后端提供 bucketed series")
            ),
            (
                "资源完整等待链"
                + ("已接后端" if self.resource_wait_chain_detail else "当前仅展示热点与等待边摘要")
            ),
            (
                "对比证据下钻"
                + ("已接后端" if self.compare_evidence_drilldown else "当前仅展示指标差异与明细")
            ),
            (
                "后台任务进度/取消"
                + ("已接后端" if self.async_jobs else "当前导出为同步执行")
            ),
        ]


@dataclass
class PanelRefreshRequest:
    dataset_id: str
    time_window: tuple[float, float]
    filter_spec: dict[str, Any]
    selection: dict[str, Any]
    transient_selection: dict[str, Any]
    effective_selection: dict[str, Any]
    hover_target: dict[str, Any] | None
    lod: int
    focused_view: str | None


@dataclass
class EvidenceJumpRequest:
    time_window: tuple[float, float]
    selection: dict[str, Any] = field(default_factory=dict)
    evidence_anchor: dict[str, Any] | None = None
    focused_view: str | None = None


@dataclass
class WorkbenchState:
    active_dataset_id: str | None = None
    compare_baseline_id: str | None = None
    compare_candidate_id: str | None = None
    current_package_path: str | None = None
    last_export_path: str | None = None
    active_tab: str = "analysis"
    compare_dimension: str = "metric"
    timeline_lod: int = 1
    timeline_grouping: str = "按核"


class PlotPreviewWindow(QMainWindow):
    def __init__(
        self,
        *,
        title: str,
        axis_label: str,
        note: str = "",
        legend_items: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle(title)
        self.resize(1240, 820)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout()
        if hasattr(root, "setContentsMargins"):
            root.setContentsMargins(12, 12, 12, 12)
        if hasattr(root, "setSpacing"):
            root.setSpacing(8)
        central.setLayout(root)

        plot_row = QWidget()
        plot_layout = QHBoxLayout()
        if hasattr(plot_layout, "setContentsMargins"):
            plot_layout.setContentsMargins(0, 0, 0, 0)
        plot_row.setLayout(plot_layout)

        self.axis_label = QLabel(_vertical_axis_text(axis_label))
        if hasattr(self.axis_label, "setObjectName"):
            self.axis_label.setObjectName("plot_axis_label")
        if hasattr(self.axis_label, "setAlignment"):
            self.axis_label.setAlignment(Qt.AlignCenter)
        if hasattr(self.axis_label, "setMinimumWidth"):
            self.axis_label.setMinimumWidth(42)
        plot_layout.addWidget(self.axis_label)

        self.plot = PlotWidget()
        plot_layout.addWidget(self.plot, 1)
        root.addWidget(plot_row, 1)

        self.legend_widget, self.legend_labels = self._build_legend_strip(legend_items or [])
        if self.legend_widget is not None:
            root.addWidget(self.legend_widget)

        self.note_label = QLabel(note)
        if hasattr(self.note_label, "setWordWrap"):
            self.note_label.setWordWrap(True)
        if hasattr(self.note_label, "setObjectName"):
            self.note_label.setObjectName("muted")
        root.addWidget(self.note_label)

    def _build_legend_strip(self, items: list[dict[str, str]]) -> tuple[QWidget | None, list[QLabel]]:
        if not items:
            return None, []
        strip = QWidget()
        layout = QHBoxLayout()
        if hasattr(layout, "setContentsMargins"):
            layout.setContentsMargins(0, 0, 0, 0)
        if hasattr(layout, "setSpacing"):
            layout.setSpacing(8)
        strip.setLayout(layout)
        labels: list[QLabel] = []
        for item in items:
            label = QLabel(item["label"])
            if hasattr(label, "setStyleSheet"):
                label.setStyleSheet(
                    "background: {bg}; color: {fg}; border-radius: 8px; padding: 4px 10px; font-weight: 600;".format(
                        bg=item["bg"],
                        fg=item.get("fg", "#0b1220"),
                    )
                )
            labels.append(label)
            layout.addWidget(label)
        layout.addStretch(1)
        return strip, labels


class RuntimeProbeWindow(QMainWindow):
    def __init__(
        self,
        source: str | None = None,
        baseline: str | None = None,
        candidate: str | None = None,
        package: str | None = None,
        default_tab: str = "analysis",
    ) -> None:
        super().__init__()
        self.controller = WorkspaceController()
        self.compare_service = CompareService(self.controller.repository, self.controller.context_store)
        self.export_service = ExportService(self.controller.repository, self.controller.context_store, self.controller.jobs)
        self.repro_service = ReproService(self.controller.repository, self.controller.context_store, self.controller.jobs)
        self.replay_service = ReplayService(self.controller.context_store)
        self.replay_timer = QTimer()
        self.load_timer = QTimer()
        self.export_timer = QTimer()
        self.availability = PanelAvailability()
        self.state = WorkbenchState(active_tab=default_tab)
        self._temp_root = Path(tempfile.mkdtemp(prefix="rttrace-workbench-"))
        self._analysis_cache: dict[str, Any] = {}
        self._compare_cache: dict[str, Any] = {}
        self._export_cache: dict[str, Any] = {}
        self._event_rows: list[dict[str, Any]] = []
        self._task_state_rows: list[dict[str, Any]] = []
        self._task_state_lane_order: list[str] = []
        self._resource_rows: list[dict[str, Any]] = []
        self._alert_rows: list[dict[str, Any]] = []
        self._compare_rows: list[dict[str, Any]] = []
        self._compare_selected_detail: dict[str, Any] | None = None
        self._compare_restore_request: dict[str, str] | None = None
        self._bookmark_rows: list[dict[str, Any]] = []
        self._active_load_job_id: str | None = None
        self._active_load_source: str | None = None
        self._active_load_preview: dict[str, Any] | None = None
        self._complete_load_on_next_poll = False
        self._active_export_job_id: str | None = None
        self._active_export_mode: str | None = None
        self._active_export_output_dir: str | None = None
        self._last_context_snapshot = self.controller.context_store.get()
        self._plot_preview_windows: dict[str, PlotPreviewWindow] = {}

        self.setWindowTitle("RTTrace Analysis Workbench")
        self.resize(1680, 1040)
        self._apply_theme()
        self._build_ui()
        self._bind_events()
        self._render_empty_state()

        if source:
            self.quick_source_input.setText(source)
            self.load_single_dataset(source)
        if baseline and candidate:
            self.compare_baseline_input.setText(baseline)
            self.compare_candidate_input.setText(candidate)
            self.load_compare_pair(baseline, candidate)
        if package:
            self.repro_package_input.setText(package)
            self.open_repro_package(package)
        self._set_active_tab(default_tab)

    def _apply_theme(self) -> None:
        if hasattr(self, "setStyleSheet"):
            self.setStyleSheet(
                """
                QWidget {
                    background: #f4f7fb;
                    color: #1f2937;
                    font-size: 12px;
                }
                QGroupBox {
                    background: #ffffff;
                    border: 1px solid #dce4ee;
                    border-radius: 10px;
                    margin-top: 12px;
                    padding-top: 8px;
                }
                QGroupBox::title {
                    subcontrol-origin: margin;
                    left: 12px;
                    padding: 0 6px;
                    color: #35516a;
                    font-weight: bold;
                }
                QLineEdit, QComboBox, QPlainTextEdit, QTableWidget, QListWidget {
                    background: #ffffff;
                    border: 1px solid #d5dee8;
                    border-radius: 8px;
                    padding: 4px 6px;
                }
                QPushButton {
                    background: #0f6cbd;
                    color: #ffffff;
                    border: none;
                    border-radius: 8px;
                    padding: 6px 12px;
                    font-weight: 600;
                }
                QPushButton:disabled {
                    background: #b8c4d2;
                    color: #eef3f8;
                }
                QLabel#muted {
                    color: #5a7086;
                }
                QLabel#badge {
                    background: #e8f0fb;
                    border: 1px solid #c9d8eb;
                    border-radius: 8px;
                    padding: 4px 8px;
                }
                QLabel#plot_axis_label {
                    color: #35516a;
                    font-size: 13px;
                    font-weight: 700;
                    padding: 0 6px;
                }
                QSplitter::handle {
                    background: #dbe5f1;
                    border-radius: 3px;
                }
                QSplitter::handle:hover {
                    background: #9ec3e8;
                }
                """
            )

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout()
        if hasattr(root, "setContentsMargins"):
            root.setContentsMargins(10, 8, 10, 10)
        if hasattr(root, "setSpacing"):
            root.setSpacing(8)
        central.setLayout(root)

        root.addWidget(self._build_command_bar())

        self.sidebar = self._build_sidebar()
        if hasattr(self.sidebar, "setMinimumWidth"):
            self.sidebar.setMinimumWidth(240)

        self.tabs = QTabWidget()
        self.analysis_tab = self._build_analysis_tab()
        self.compare_tab = self._build_compare_tab()
        self.export_tab = self._build_export_tab()
        self.diagnostic_tab = self._build_diagnostic_tab()
        self.tabs.addTab(self.analysis_tab, "分析")
        self.tabs.addTab(self.compare_tab, "对比")
        self.tabs.addTab(self.export_tab, "导出 / 复现")
        self.tabs.addTab(self.diagnostic_tab, "诊断")

        self.body_splitter = self._create_splitter(
            Qt.Horizontal,
            [self.sidebar, self.tabs],
            sizes=[290, 1310],
            stretch_factors=[1, 5],
        )
        root.addWidget(self.body_splitter, 1)

        self.footer_label = QLabel("工作区就绪。当前能力严格对齐已实现后台接口。")
        if hasattr(self.footer_label, "setObjectName"):
            self.footer_label.setObjectName("muted")
        root.addWidget(self.footer_label)

    def _build_command_bar(self) -> QWidget:
        bar = QGroupBox("工作区命令栏")
        layout = QGridLayout()
        bar.setLayout(layout)

        self.quick_source_input = QLineEdit()
        self.quick_source_input.setPlaceholderText("输入 trace 或导出包路径，留空可加载演示数据")
        self.load_button = QPushButton("加载数据集")
        self.load_pair_button = QPushButton("载入双基线")
        self.load_demo_button = QPushButton("演示数据")
        self.refresh_button = QPushButton("刷新")
        self.open_package_button = QPushButton("打开复现包")

        self.bookmark_quick_button = QPushButton("保存书签")
        self.replay_prev_button = QPushButton("前进一步")
        self.replay_play_button = QPushButton("播放")
        self.replay_pause_button = QPushButton("暂停")
        self.replay_next_button = QPushButton("后进一步")
        self.replay_rate_input = QLineEdit("1.0")
        self.replay_rate_input.setPlaceholderText("倍率")

        self.header_dataset_label = QLabel("数据集: 未加载")
        self.header_window_label = QLabel("时间窗: n/a")
        self.header_untrusted_label = QLabel("可信度: 未知")
        for label in [self.header_dataset_label, self.header_window_label, self.header_untrusted_label]:
            if hasattr(label, "setObjectName"):
                label.setObjectName("badge")

        layout.addWidget(self.quick_source_input, 0, 0, 1, 4)
        layout.addWidget(self.load_button, 0, 4)
        layout.addWidget(self.load_demo_button, 0, 5)
        layout.addWidget(self.load_pair_button, 0, 6)
        layout.addWidget(self.open_package_button, 0, 7)
        layout.addWidget(self.refresh_button, 0, 8)
        layout.addWidget(self.bookmark_quick_button, 0, 9)

        layout.addWidget(self.replay_prev_button, 1, 0)
        layout.addWidget(self.replay_play_button, 1, 1)
        layout.addWidget(self.replay_pause_button, 1, 2)
        layout.addWidget(self.replay_next_button, 1, 3)
        layout.addWidget(QLabel("倍率"), 1, 4)
        layout.addWidget(self.replay_rate_input, 1, 5)
        layout.addWidget(self.header_dataset_label, 1, 6)
        layout.addWidget(self.header_window_label, 1, 7)
        layout.addWidget(self.header_untrusted_label, 1, 8, 1, 2)
        return bar

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        layout = QVBoxLayout()
        sidebar.setLayout(layout)

        summary_group = QGroupBox("数据集摘要")
        summary_layout = QVBoxLayout()
        summary_group.setLayout(summary_layout)
        self.dataset_summary_label = QLabel("未加载数据集")
        self.dataset_source_label = QLabel("来源: n/a")
        self.dataset_counts_label = QLabel("事件/片段/任务/IRQ: -")
        self.dataset_window_label = QLabel("窗口: n/a")
        for label in [
            self.dataset_summary_label,
            self.dataset_source_label,
            self.dataset_counts_label,
            self.dataset_window_label,
        ]:
            summary_layout.addWidget(label)
        layout.addWidget(summary_group)

        filter_group = QGroupBox("筛选器")
        filter_layout = QFormLayout()
        filter_group.setLayout(filter_layout)
        self.filter_task_input = QLineEdit()
        self.filter_core_input = QLineEdit()
        self.filter_event_input = QLineEdit()
        self.filter_task_input.setPlaceholderText("例如 0x1 / 1")
        self.filter_core_input.setPlaceholderText("例如 0 / 1")
        self.filter_event_input.setPlaceholderText("例如 CTX_SWITCH")
        filter_layout.addRow("任务 ID", self.filter_task_input)
        filter_layout.addRow("核 ID", self.filter_core_input)
        filter_layout.addRow("事件名", self.filter_event_input)
        self.apply_filter_button = QPushButton("应用筛选")
        self.clear_filter_button = QPushButton("清空筛选")
        filter_layout.addRow(self.apply_filter_button)
        filter_layout.addRow(self.clear_filter_button)
        self.filter_note_label = QLabel("当前只开放 task/core/event 三类真实筛选，资源/告警筛选待后端补齐。")
        if hasattr(self.filter_note_label, "setWordWrap"):
            self.filter_note_label.setWordWrap(True)
        filter_layout.addRow(self.filter_note_label)
        layout.addWidget(filter_group)

        bookmark_group = QGroupBox("书签")
        bookmark_layout = QVBoxLayout()
        bookmark_group.setLayout(bookmark_layout)
        self.bookmark_label_input = QLineEdit()
        self.bookmark_label_input.setPlaceholderText("留空则自动生成书签名")
        self.bookmark_list = QListWidget()
        self.bookmark_apply_button = QPushButton("恢复书签")
        self.bookmark_delete_button = QPushButton("删除书签")
        bookmark_layout.addWidget(self.bookmark_label_input)
        bookmark_layout.addWidget(self.bookmark_list)
        bookmark_layout.addWidget(self.bookmark_apply_button)
        bookmark_layout.addWidget(self.bookmark_delete_button)
        layout.addWidget(bookmark_group)

        capability_group = QGroupBox("能力状态")
        capability_layout = QVBoxLayout()
        capability_group.setLayout(capability_layout)
        self.capability_label = QLabel("\n".join(self.availability.notes()))
        if hasattr(self.capability_label, "setWordWrap"):
            self.capability_label.setWordWrap(True)
        capability_layout.addWidget(self.capability_label)
        layout.addWidget(capability_group)
        layout.addStretch(1)
        return sidebar

    def _build_analysis_tab(self) -> QWidget:
        tab = QWidget()
        root = QVBoxLayout()
        if hasattr(root, "setContentsMargins"):
            root.setContentsMargins(0, 0, 0, 0)
        tab.setLayout(root)

        main_column = QWidget()
        main_layout = QVBoxLayout()
        if hasattr(main_layout, "setContentsMargins"):
            main_layout.setContentsMargins(0, 0, 0, 0)
        main_column.setLayout(main_layout)

        metrics_group = QGroupBox("指标概览")
        metrics_layout = QVBoxLayout()
        metrics_group.setLayout(metrics_layout)
        metric_cards = QWidget()
        cards_layout = QGridLayout()
        metric_cards.setLayout(cards_layout)
        self.metric_value_labels: dict[str, QLabel] = {}
        metric_specs = [
            ("cpu_utilization", "平均 CPU 利用率"),
            ("blocked_time", "总阻塞时长"),
            ("context_switch_count", "上下文切换次数"),
            ("irq_busy_time", "IRQ 次数"),
        ]
        for index, (metric_id, title) in enumerate(metric_specs):
            card = QGroupBox(title)
            card_layout = QVBoxLayout()
            card.setLayout(card_layout)
            value_label = QLabel("--")
            detail_label = QLabel(metric_id)
            if hasattr(detail_label, "setObjectName"):
                detail_label.setObjectName("muted")
            card_layout.addWidget(value_label)
            card_layout.addWidget(detail_label)
            cards_layout.addWidget(card, 0, index)
            self.metric_value_labels[metric_id] = value_label
        metrics_layout.addWidget(metric_cards)
        metric_plot_toolbar = QWidget()
        metric_plot_toolbar_layout = QHBoxLayout()
        if hasattr(metric_plot_toolbar_layout, "setContentsMargins"):
            metric_plot_toolbar_layout.setContentsMargins(0, 0, 0, 0)
        metric_plot_toolbar.setLayout(metric_plot_toolbar_layout)
        metric_plot_toolbar_layout.addStretch(1)
        self.metric_popout_button = QPushButton("新窗口查看")
        metric_plot_toolbar_layout.addWidget(self.metric_popout_button)
        metrics_layout.addWidget(metric_plot_toolbar)
        self.metric_plot = PlotWidget()
        if hasattr(self.metric_plot, "setMinimumHeight"):
            self.metric_plot.setMinimumHeight(180)
        self._prepare_plot(self.metric_plot, left="数值", bottom="指标")
        self.metric_plot_frame, self.metric_axis_label = self._build_plot_frame(self.metric_plot, "数值")
        metrics_layout.addWidget(self.metric_plot_frame, 1)
        self.metric_note_label = QLabel(self.availability.notes()[0])
        if hasattr(self.metric_note_label, "setWordWrap"):
            self.metric_note_label.setWordWrap(True)
        metrics_layout.addWidget(self.metric_note_label)
        main_layout.addWidget(metrics_group)

        timeline_group = QGroupBox("时间线 / 甘特图")
        timeline_layout = QVBoxLayout()
        timeline_group.setLayout(timeline_layout)
        timeline_toolbar = QWidget()
        timeline_toolbar_layout = QHBoxLayout()
        timeline_toolbar.setLayout(timeline_toolbar_layout)
        self.timeline_lod_combo = QComboBox()
        self.timeline_lod_combo.addItems(["LOD 0", "LOD 1", "LOD 2"])
        self.timeline_lod_combo.setCurrentText("LOD 1")
        self.timeline_grouping_combo = QComboBox()
        self.timeline_grouping_combo.addItems(["按核", "按任务"])
        timeline_toolbar_layout.addWidget(QLabel("显示粒度"))
        timeline_toolbar_layout.addWidget(self.timeline_lod_combo)
        timeline_toolbar_layout.addWidget(QLabel("泳道"))
        timeline_toolbar_layout.addWidget(self.timeline_grouping_combo)
        timeline_toolbar_layout.addStretch(1)
        self.timeline_popout_button = QPushButton("新窗口查看")
        timeline_toolbar_layout.addWidget(self.timeline_popout_button)
        timeline_layout.addWidget(timeline_toolbar)
        self.timeline_plot = PlotWidget()
        if hasattr(self.timeline_plot, "setMinimumHeight"):
            self.timeline_plot.setMinimumHeight(260)
        self._prepare_plot(self.timeline_plot, left="泳道", bottom="时间")
        self.timeline_plot_frame, self.timeline_axis_label = self._build_plot_frame(self.timeline_plot, "泳道")
        timeline_layout.addWidget(self.timeline_plot_frame, 1)
        self.timeline_legend_widget, self.timeline_legend_labels = self._build_legend_strip(
            [
                {"label": "ExecSlice", "bg": "#c239b3", "fg": "#ffffff"},
                {"label": "IRQ", "bg": "#f97316", "fg": "#111827"},
                {"label": "Untrusted 窗口", "bg": "#da7b01", "fg": "#111827"},
            ]
        )
        timeline_layout.addWidget(self.timeline_legend_widget)
        self.timeline_note_label = QLabel("LOD1 对齐真实 ExecSlice + SwitchPoint + IrqSpan；LOD0/LOD2 先复用同一数据并显式提示当前能力边界。")
        if hasattr(self.timeline_note_label, "setWordWrap"):
            self.timeline_note_label.setWordWrap(True)
        timeline_layout.addWidget(self.timeline_note_label)
        main_layout.addWidget(timeline_group)

        events_group = QGroupBox("事件表")
        events_layout = QVBoxLayout()
        events_group.setLayout(events_layout)
        self.event_table = QTableWidget()
        if hasattr(self.event_table, "setMinimumHeight"):
            self.event_table.setMinimumHeight(170)
        self._configure_table(self.event_table, ["时间", "核", "任务", "事件", "对象", "可信"])
        events_layout.addWidget(self.event_table)
        self.analysis_main_splitter = self._create_splitter(
            Qt.Vertical,
            [metrics_group, timeline_group, events_group],
            sizes=[260, 390, 210],
            stretch_factors=[3, 5, 2],
        )
        main_layout.addWidget(self.analysis_main_splitter)

        inspector_column = QWidget()
        inspector_layout = QVBoxLayout()
        if hasattr(inspector_layout, "setContentsMargins"):
            inspector_layout.setContentsMargins(0, 0, 0, 0)
        inspector_column.setLayout(inspector_layout)

        side_tabs = QTabWidget()

        task_tab = QWidget()
        task_layout = QVBoxLayout()
        task_tab.setLayout(task_layout)
        self.task_state_table = QTableWidget()
        self._configure_table(self.task_state_table, ["任务", "泳道", "时间窗", "状态分布", "片段数"])
        task_layout.addWidget(self.task_state_table)
        side_tabs.addTab(task_tab, "任务状态")

        resource_tab = QWidget()
        resource_layout = QVBoxLayout()
        resource_tab.setLayout(resource_layout)
        self.resource_table = QTableWidget()
        self._configure_table(self.resource_table, ["节点/热点", "值", "类型", "证据"])
        resource_layout.addWidget(self.resource_table)
        self.resource_note_label = QLabel(self.availability.notes()[1])
        if hasattr(self.resource_note_label, "setWordWrap"):
            self.resource_note_label.setWordWrap(True)
        resource_layout.addWidget(self.resource_note_label)
        side_tabs.addTab(resource_tab, "资源争用")
        inspector_layout.addWidget(side_tabs)

        alerts_group = QGroupBox("告警面板")
        alerts_layout = QVBoxLayout()
        alerts_group.setLayout(alerts_layout)
        self.alert_table = QTableWidget()
        if hasattr(self.alert_table, "setMinimumHeight"):
            self.alert_table.setMinimumHeight(150)
        self._configure_table(self.alert_table, ["类型", "严重度", "对象", "时间窗", "可信"])
        alerts_layout.addWidget(self.alert_table)

        details_group = QGroupBox("证据 / 上下文详情")
        details_layout = QVBoxLayout()
        details_group.setLayout(details_layout)
        self.analysis_details = QPlainTextEdit()
        if hasattr(self.analysis_details, "setReadOnly"):
            self.analysis_details.setReadOnly(True)
        details_layout.addWidget(self.analysis_details)
        self.analysis_inspector_splitter = self._create_splitter(
            Qt.Vertical,
            [side_tabs, alerts_group, details_group],
            sizes=[280, 190, 250],
            stretch_factors=[4, 2, 3],
        )
        inspector_layout.addWidget(self.analysis_inspector_splitter)

        if hasattr(inspector_column, "setMaximumWidth"):
            inspector_column.setMaximumWidth(640)
            inspector_column.setMinimumWidth(360)

        self.analysis_content_splitter = self._create_splitter(
            Qt.Horizontal,
            [main_column, inspector_column],
            sizes=[1100, 500],
            stretch_factors=[5, 3],
        )
        root.addWidget(self.analysis_content_splitter, 1)
        return tab

    def _build_compare_tab(self) -> QWidget:
        tab = QWidget()
        root = QVBoxLayout()
        if hasattr(root, "setContentsMargins"):
            root.setContentsMargins(0, 0, 0, 0)
        tab.setLayout(root)

        source_group = QGroupBox("双基线载入")
        source_layout = QGridLayout()
        source_group.setLayout(source_layout)
        self.compare_baseline_input = QLineEdit()
        self.compare_candidate_input = QLineEdit()
        self.compare_load_button = QPushButton("加载对比")
        self.compare_demo_button = QPushButton("演示对比")
        source_layout.addWidget(QLabel("Baseline"), 0, 0)
        source_layout.addWidget(self.compare_baseline_input, 0, 1)
        source_layout.addWidget(QLabel("Candidate"), 1, 0)
        source_layout.addWidget(self.compare_candidate_input, 1, 1)
        source_layout.addWidget(self.compare_load_button, 0, 2)
        source_layout.addWidget(self.compare_demo_button, 1, 2)
        self.compare_scope_label = QLabel("CompareScope: 未初始化")
        source_layout.addWidget(self.compare_scope_label, 2, 0, 1, 4)
        self.compare_dimension_combo = QComboBox()
        self.compare_dimension_combo.addItems(["指标"])
        self.compare_jump_button = QPushButton("跳转证据")
        self.compare_jump_button.setEnabled(False)
        self.compare_peer_jump_button = QPushButton("切换到对侧证据")
        self.compare_peer_jump_button.setEnabled(False)
        source_layout.addWidget(QLabel("维度"), 3, 0)
        source_layout.addWidget(self.compare_dimension_combo, 3, 1)
        source_layout.addWidget(self.compare_jump_button, 3, 2)
        source_layout.addWidget(self.compare_peer_jump_button, 3, 3)
        if hasattr(source_group, "setMinimumHeight"):
            source_group.setMinimumHeight(140)

        self.compare_plot = PlotWidget()
        if hasattr(self.compare_plot, "setMinimumHeight"):
            self.compare_plot.setMinimumHeight(280)
        self._prepare_plot(self.compare_plot, left="值", bottom="指标")
        compare_plot_group = QGroupBox("差异图")
        compare_plot_layout = QVBoxLayout()
        compare_plot_group.setLayout(compare_plot_layout)
        compare_plot_toolbar = QWidget()
        compare_plot_toolbar_layout = QHBoxLayout()
        if hasattr(compare_plot_toolbar_layout, "setContentsMargins"):
            compare_plot_toolbar_layout.setContentsMargins(0, 0, 0, 0)
        compare_plot_toolbar.setLayout(compare_plot_toolbar_layout)
        compare_plot_toolbar_layout.addStretch(1)
        self.compare_popout_button = QPushButton("新窗口查看")
        compare_plot_toolbar_layout.addWidget(self.compare_popout_button)
        compare_plot_layout.addWidget(compare_plot_toolbar)
        self.compare_plot_frame, self.compare_axis_label = self._build_plot_frame(self.compare_plot, "值")
        compare_plot_layout.addWidget(self.compare_plot_frame, 1)
        self.compare_legend_widget, self.compare_legend_labels = self._build_legend_strip(
            [
                {"label": "Baseline", "bg": "#94a3b8", "fg": "#111827"},
                {"label": "Candidate", "bg": "#38bdf8", "fg": "#111827"},
            ]
        )
        compare_plot_layout.addWidget(self.compare_legend_widget)

        self.compare_table = QTableWidget()
        if hasattr(self.compare_table, "setMinimumHeight"):
            self.compare_table.setMinimumHeight(180)
        self._configure_table(self.compare_table, ["对象", "Baseline", "Candidate", "Delta", "可信"])
        compare_table_group = QGroupBox("差异表")
        compare_table_layout = QVBoxLayout()
        compare_table_group.setLayout(compare_table_layout)
        compare_table_layout.addWidget(self.compare_table)

        self.compare_note_label = QLabel("当前 compare 支持多维差异切换、明细面板和证据跳转。")
        if hasattr(self.compare_note_label, "setWordWrap"):
            self.compare_note_label.setWordWrap(True)
        self.compare_details = QPlainTextEdit()
        if hasattr(self.compare_details, "setReadOnly"):
            self.compare_details.setReadOnly(True)
        compare_detail_group = QGroupBox("差异明细")
        compare_detail_layout = QVBoxLayout()
        compare_detail_group.setLayout(compare_detail_layout)
        compare_detail_layout.addWidget(self.compare_note_label)
        compare_detail_layout.addWidget(self.compare_details)

        self.compare_splitter = self._create_splitter(
            Qt.Vertical,
            [source_group, compare_plot_group, compare_table_group, compare_detail_group],
            sizes=[150, 360, 220, 210],
            stretch_factors=[1, 5, 3, 3],
        )
        root.addWidget(self.compare_splitter, 1)
        return tab

    def _build_export_tab(self) -> QWidget:
        tab = QWidget()
        root = QVBoxLayout()
        if hasattr(root, "setContentsMargins"):
            root.setContentsMargins(0, 0, 0, 0)
        tab.setLayout(root)

        export_group = QGroupBox("导出")
        export_layout = QVBoxLayout()
        export_group.setLayout(export_layout)
        self.export_output_input = QLineEdit(str(self._temp_root / "exports" / "full"))
        self.export_context_label = QLabel("当前上下文: n/a")
        self.export_full_button = QPushButton("全量导出")
        self.export_clipped_button = QPushButton("裁剪导出")
        self.export_evidence_button = QPushButton("Evidence 导出")
        self.export_status_label = QLabel("导出状态: 未执行")
        self.export_note_label = QLabel("当前导出通过后台作业异步执行；可轮询状态并在完成后打开导出包。")
        if hasattr(self.export_note_label, "setWordWrap"):
            self.export_note_label.setWordWrap(True)
        evidence_form = QFormLayout()
        self.evidence_mode_combo = QComboBox()
        self.evidence_mode_combo.addItems(["mode_a", "mode_b"])
        self.evidence_rule_family_input = QLineEdit("ref_ref")
        self.evidence_seed_spec_input = QPlainTextEdit()
        if hasattr(self.evidence_seed_spec_input, "setPlainText"):
            self.evidence_seed_spec_input.setPlainText(
                _json_text({"source_kind": "analysis_context", "source_payload": {}})
            )
        self.evidence_sidecar_input = QLineEdit()
        self.evidence_sidecar_manifest_input = QLineEdit()
        self.export_closure_mode_label = QLabel("Closure Mode: -")
        self.export_halt_reason_label = QLabel("Halt Reason: -")
        if hasattr(self.export_closure_mode_label, "setObjectName"):
            self.export_closure_mode_label.setObjectName("badge")
        if hasattr(self.export_halt_reason_label, "setObjectName"):
            self.export_halt_reason_label.setObjectName("muted")
        evidence_form.addRow("Evidence 模式", self.evidence_mode_combo)
        evidence_form.addRow("Rule Family", self.evidence_rule_family_input)
        evidence_form.addRow("Seed Spec JSON", self.evidence_seed_spec_input)
        evidence_form.addRow("Sidecar Source", self.evidence_sidecar_input)
        evidence_form.addRow("Sidecar Manifest", self.evidence_sidecar_manifest_input)
        self.export_result_text = QPlainTextEdit()
        if hasattr(self.export_result_text, "setReadOnly"):
            self.export_result_text.setReadOnly(True)
        export_layout.addWidget(QLabel("输出目录"))
        export_layout.addWidget(self.export_output_input)
        export_layout.addWidget(self.export_context_label)
        export_layout.addWidget(self.export_full_button)
        export_layout.addWidget(self.export_clipped_button)
        export_layout.addWidget(self.export_evidence_button)
        export_layout.addLayout(evidence_form)
        export_layout.addWidget(self.export_status_label)
        export_layout.addWidget(self.export_closure_mode_label)
        export_layout.addWidget(self.export_halt_reason_label)
        export_layout.addWidget(self.export_note_label)
        export_layout.addWidget(self.export_result_text)
        root.addWidget(export_group)

        repro_group = QGroupBox("复现")
        repro_layout = QVBoxLayout()
        repro_group.setLayout(repro_layout)
        self.repro_package_input = QLineEdit()
        self.repro_role_combo = QComboBox()
        self.repro_role_combo.addItems(["single", "baseline", "candidate"])
        self.repro_open_button = QPushButton("打开包")
        self.repro_restore_button = QPushButton("恢复上下文")
        self.repro_load_button = QPushButton("按角色装载")
        self.repro_status_label = QLabel("复现状态: 未打开")
        proof_digest_group = QGroupBox("Proof Digest")
        proof_digest_layout = QFormLayout()
        proof_digest_group.setLayout(proof_digest_layout)
        self.proof_digest_labels = {
            "closure_mode": QLabel("-"),
            "frontier_halt_reason": QLabel("-"),
            "proof_hash": QLabel("-"),
            "snapshot_id": QLabel("-"),
            "rule_family": QLabel("-"),
            "missing_required_refs": QLabel("-"),
            "truncated_frontier_count": QLabel("-"),
            "metrics": QLabel("-"),
        }
        for label in self.proof_digest_labels.values():
            if hasattr(label, "setWordWrap"):
                label.setWordWrap(True)
        proof_digest_layout.addRow("Closure Mode", self.proof_digest_labels["closure_mode"])
        proof_digest_layout.addRow("Halt Reason", self.proof_digest_labels["frontier_halt_reason"])
        proof_digest_layout.addRow("Proof Hash", self.proof_digest_labels["proof_hash"])
        proof_digest_layout.addRow("Snapshot ID", self.proof_digest_labels["snapshot_id"])
        proof_digest_layout.addRow("Rule Family", self.proof_digest_labels["rule_family"])
        proof_digest_layout.addRow("Missing Refs", self.proof_digest_labels["missing_required_refs"])
        proof_digest_layout.addRow("Truncated Frontier", self.proof_digest_labels["truncated_frontier_count"])
        proof_digest_layout.addRow("Metrics", self.proof_digest_labels["metrics"])
        self.repro_meta_text = QPlainTextEdit()
        if hasattr(self.repro_meta_text, "setReadOnly"):
            self.repro_meta_text.setReadOnly(True)
        repro_layout.addWidget(QLabel("包目录"))
        repro_layout.addWidget(self.repro_package_input)
        repro_layout.addWidget(QLabel("角色"))
        repro_layout.addWidget(self.repro_role_combo)
        repro_layout.addWidget(self.repro_open_button)
        repro_layout.addWidget(self.repro_restore_button)
        repro_layout.addWidget(self.repro_load_button)
        repro_layout.addWidget(self.repro_status_label)
        repro_layout.addWidget(proof_digest_group)
        repro_layout.addWidget(self.repro_meta_text)
        self.export_splitter = self._create_splitter(
            Qt.Horizontal,
            [export_group, repro_group],
            sizes=[820, 700],
            stretch_factors=[6, 5],
        )
        root.addWidget(self.export_splitter, 1)
        return tab

    def _build_diagnostic_tab(self) -> QWidget:
        tab = QWidget()
        root = QVBoxLayout()
        if hasattr(root, "setContentsMargins"):
            root.setContentsMargins(0, 0, 0, 0)
        tab.setLayout(root)

        context_group = QGroupBox("当前上下文")
        context_layout = QVBoxLayout()
        context_group.setLayout(context_layout)
        self.context_dump = QPlainTextEdit()
        if hasattr(self.context_dump, "setReadOnly"):
            self.context_dump.setReadOnly(True)
        context_layout.addWidget(self.context_dump)

        query_group = QGroupBox("最近查询缓存")
        query_layout = QVBoxLayout()
        query_group.setLayout(query_layout)
        self.query_dump = QPlainTextEdit()
        if hasattr(self.query_dump, "setReadOnly"):
            self.query_dump.setReadOnly(True)
        query_layout.addWidget(self.query_dump)

        self.diagnostic_splitter = self._create_splitter(
            Qt.Horizontal,
            [context_group, query_group],
            sizes=[760, 760],
            stretch_factors=[1, 1],
        )
        root.addWidget(self.diagnostic_splitter, 1)
        return tab

    def _bind_events(self) -> None:
        self.load_button.clicked.connect(self._load_dataset_from_input)
        self.load_pair_button.clicked.connect(self._load_compare_from_toolbar)
        self.load_demo_button.clicked.connect(self.load_demo_dataset)
        self.refresh_button.clicked.connect(self.refresh_workspace)
        self.open_package_button.clicked.connect(self._open_package_from_input)
        self.bookmark_quick_button.clicked.connect(self.create_bookmark)

        self.apply_filter_button.clicked.connect(self.apply_filters)
        self.clear_filter_button.clicked.connect(self.clear_filters)
        self.bookmark_apply_button.clicked.connect(self.apply_selected_bookmark)
        self.bookmark_delete_button.clicked.connect(self.delete_selected_bookmark)

        self.replay_prev_button.clicked.connect(lambda: self.step_replay(-1))
        self.replay_next_button.clicked.connect(lambda: self.step_replay(1))
        self.replay_play_button.clicked.connect(self.play_replay)
        self.replay_pause_button.clicked.connect(self.pause_replay)
        self.replay_timer.timeout.connect(self._advance_replay)
        self.load_timer.timeout.connect(self._poll_load_job)
        self.export_timer.timeout.connect(self._poll_export_job)

        self.timeline_lod_combo.currentTextChanged.connect(self._on_timeline_mode_changed)
        self.timeline_grouping_combo.currentTextChanged.connect(self._on_timeline_grouping_changed)
        self.metric_popout_button.clicked.connect(lambda: self.open_plot_preview("metric"))
        self.timeline_popout_button.clicked.connect(lambda: self.open_plot_preview("timeline"))
        self.compare_popout_button.clicked.connect(lambda: self.open_plot_preview("compare"))

        self.event_table.cellClicked.connect(self._on_event_row_selected)
        self.task_state_table.cellClicked.connect(self._on_task_state_selected)
        self.resource_table.cellClicked.connect(self._on_resource_selected)
        self.alert_table.cellClicked.connect(self._on_alert_selected)
        self.compare_table.cellClicked.connect(self._on_compare_row_selected)
        if hasattr(self.event_table, "cellEntered"):
            self.event_table.cellEntered.connect(self._on_event_row_hover)
        if hasattr(self.task_state_table, "cellEntered"):
            self.task_state_table.cellEntered.connect(self._on_task_state_row_hover)
        if hasattr(self.resource_table, "cellEntered"):
            self.resource_table.cellEntered.connect(self._on_resource_row_hover)
        if hasattr(self.alert_table, "cellEntered"):
            self.alert_table.cellEntered.connect(self._on_alert_row_hover)
        self.compare_dimension_combo.currentTextChanged.connect(self._on_compare_dimension_changed)
        self.compare_jump_button.clicked.connect(self.jump_selected_compare_detail)
        self.compare_peer_jump_button.clicked.connect(self.jump_selected_compare_peer_detail)

        self.compare_load_button.clicked.connect(self._load_compare_from_inputs)
        self.compare_demo_button.clicked.connect(self.load_compare_demo)
        self.export_full_button.clicked.connect(lambda: self.run_export("full"))
        self.export_clipped_button.clicked.connect(lambda: self.run_export("clipped"))
        self.export_evidence_button.clicked.connect(lambda: self.run_export("evidence"))
        self.repro_open_button.clicked.connect(self._open_package_from_repro_input)
        self.repro_restore_button.clicked.connect(self.restore_repro_context)
        self.repro_load_button.clicked.connect(self.load_repro_dataset)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        self.controller.context_store.changed.connect(self._on_context_changed)

    def _prepare_plot(self, widget: PlotWidget, left: str, bottom: str) -> None:
        if hasattr(widget, "setBackground"):
            widget.setBackground("#111827")
        if hasattr(widget, "setLabel"):
            widget.setLabel("left", "")
            widget.setLabel("bottom", bottom)
        if hasattr(widget, "showGrid"):
            widget.showGrid(x=True, y=True, alpha=0.22)

    def _build_plot_frame(self, plot_widget: PlotWidget, axis_label: str) -> tuple[QWidget, QLabel]:
        frame = QWidget()
        layout = QHBoxLayout()
        if hasattr(layout, "setContentsMargins"):
            layout.setContentsMargins(0, 0, 0, 0)
        if hasattr(layout, "setSpacing"):
            layout.setSpacing(6)
        frame.setLayout(layout)

        label = QLabel(_vertical_axis_text(axis_label))
        if hasattr(label, "setObjectName"):
            label.setObjectName("plot_axis_label")
        if hasattr(label, "setAlignment"):
            label.setAlignment(Qt.AlignCenter)
        if hasattr(label, "setMinimumWidth"):
            label.setMinimumWidth(38)
        layout.addWidget(label)
        layout.addWidget(plot_widget, 1)
        return frame, label

    def _build_legend_strip(self, items: list[dict[str, str]]) -> tuple[QWidget, list[QLabel]]:
        strip = QWidget()
        layout = QHBoxLayout()
        if hasattr(layout, "setContentsMargins"):
            layout.setContentsMargins(0, 0, 0, 0)
        if hasattr(layout, "setSpacing"):
            layout.setSpacing(8)
        strip.setLayout(layout)
        labels: list[QLabel] = []
        for item in items:
            label = QLabel(item["label"])
            if hasattr(label, "setStyleSheet"):
                label.setStyleSheet(
                    "background: {bg}; color: {fg}; border-radius: 8px; padding: 4px 10px; font-weight: 600;".format(
                        bg=item["bg"],
                        fg=item.get("fg", "#0b1220"),
                    )
                )
            labels.append(label)
            layout.addWidget(label)
        layout.addStretch(1)
        return strip, labels

    def _create_splitter(
        self,
        orientation: Any,
        widgets: list[QWidget],
        *,
        sizes: list[int] | None = None,
        stretch_factors: list[int] | None = None,
    ) -> QSplitter:
        splitter = QSplitter(orientation)
        if hasattr(splitter, "setChildrenCollapsible"):
            splitter.setChildrenCollapsible(False)
        if hasattr(splitter, "setOpaqueResize"):
            splitter.setOpaqueResize(True)
        if hasattr(splitter, "setHandleWidth"):
            splitter.setHandleWidth(10)
        for index, widget in enumerate(widgets):
            splitter.addWidget(widget)
            if hasattr(splitter, "setCollapsible"):
                splitter.setCollapsible(index, False)
        if sizes and hasattr(splitter, "setSizes"):
            splitter.setSizes(sizes)
        if stretch_factors and hasattr(splitter, "setStretchFactor"):
            for index, factor in enumerate(stretch_factors):
                splitter.setStretchFactor(index, factor)
        return splitter

    def _configure_table(self, widget: QTableWidget, headers: list[str]) -> None:
        if hasattr(widget, "setColumnCount"):
            widget.setColumnCount(len(headers))
            widget.setHorizontalHeaderLabels(headers)
        if hasattr(widget, "horizontalHeader"):
            header = widget.horizontalHeader()
            if hasattr(header, "setStretchLastSection"):
                header.setStretchLastSection(True)
        if hasattr(widget, "setAlternatingRowColors"):
            widget.setAlternatingRowColors(True)
        if hasattr(widget, "setMouseTracking"):
            widget.setMouseTracking(True)
        if hasattr(widget, "viewport"):
            viewport = widget.viewport()
            if hasattr(viewport, "setMouseTracking"):
                viewport.setMouseTracking(True)

    def _runtime_context_dict(self, context: AnalysisContext | None = None) -> dict[str, Any]:
        return dataclass_to_dict(context or self.controller.context_store.get())

    def _context_refresh_signature(self, context: AnalysisContext) -> dict[str, Any]:
        snapshot = self._runtime_context_dict(context)
        for field in ("context_rev", "pending_jobs", "hover_target", "transient_selection", "playback_runtime"):
            snapshot.pop(field, None)
        return snapshot

    def _context_requires_workspace_refresh(
        self,
        previous: AnalysisContext | None,
        current: AnalysisContext,
    ) -> bool:
        if previous is None:
            return True
        return self._context_refresh_signature(previous) != self._context_refresh_signature(current)

    def _context_requires_analysis_refresh(
        self,
        previous: AnalysisContext | None,
        current: AnalysisContext,
    ) -> bool:
        if previous is None:
            return True
        return (
            _compact_mapping(previous.transient_selection) != _compact_mapping(current.transient_selection)
            or dict(previous.hover_target or {}) != dict(current.hover_target or {})
        )

    def _context_footer_text(self, context: AnalysisContext) -> str:
        return (
            f"context_rev={context.context_rev}"
            f" | focused_view={context.focused_view or '-'}"
            f" | selection={_format_selection_summary(context.selection)}"
            f" | hover={_format_hover_target_summary(context.hover_target)}"
            f" | transient={_format_selection_summary(context.transient_selection)}"
        )

    def _update_runtime_context_views(self, context: AnalysisContext) -> None:
        self.context_dump.setPlainText(_json_text(self._runtime_context_dict(context)))

    def _render_empty_state(self) -> None:
        self.context_dump.setPlainText(_json_text(self._runtime_context_dict()))
        self.query_dump.setPlainText(_json_text({"analysis": {}, "compare": {}, "export": {}}))
        self.analysis_details.setPlainText(
            _json_text(
                {
                    "message": "未加载数据集",
                    "availability": self.availability.notes(),
                }
            )
        )

    def _set_active_tab(self, tab_name: str) -> None:
        index_map = {"analysis": 0, "compare": 1, "export": 2, "diagnostic": 3}
        self.state.active_tab = tab_name if tab_name in index_map else "analysis"
        self.tabs.setCurrentIndex(index_map.get(self.state.active_tab, 0))

    def _on_tab_changed(self, index: int) -> None:
        tab_names = ["analysis", "compare", "export", "diagnostic"]
        self.state.active_tab = tab_names[index] if 0 <= index < len(tab_names) else "analysis"

    def _load_dataset_from_input(self) -> None:
        self.load_single_dataset(self.quick_source_input.text().strip() or None)

    def _load_compare_from_toolbar(self) -> None:
        baseline = self.compare_baseline_input.text().strip() or None
        candidate = self.compare_candidate_input.text().strip() or None
        self.load_compare_pair(baseline, candidate)

    def _open_package_from_input(self) -> None:
        raw = self.quick_source_input.text().strip() or self.repro_package_input.text().strip()
        self.open_repro_package(raw or None)

    def _open_package_from_repro_input(self) -> None:
        self.open_repro_package(self.repro_package_input.text().strip() or None)

    def _load_compare_from_inputs(self) -> None:
        self.load_compare_pair(
            self.compare_baseline_input.text().strip() or None,
            self.compare_candidate_input.text().strip() or None,
        )

    def _create_demo_trace(self, name: str = "basic", candidate_variant: bool = False) -> str:
        suffix = "candidate" if candidate_variant else "baseline"
        path = self._temp_root / f"{name}-{suffix}.trace"
        return str(write_scenario(path, name=name, candidate_variant=candidate_variant))

    def load_demo_dataset(self) -> None:
        self.load_single_dataset(None)

    def _complete_loaded_dataset(self, dataset_id: str) -> None:
        self._active_load_preview = None
        self.state.active_dataset_id = dataset_id
        bundle = self.controller.repository.get(dataset_id).artifact.bundle
        self.replay_service.replay_Init(bundle.event_stream, bundle.exec_slices, self.controller.context_store.get())
        self.refresh_workspace()

    def _describe_load_preview(self, preview: dict[str, Any] | None) -> str:
        if not preview:
            return "数据集加载已提交"
        bucket_count = len(preview.get("lod0_buckets") or [])
        task_preview = dict(preview.get("task_state_preview") or {})
        readiness = preview.get("readiness") or {}
        lod_ready = readiness.get("lod_ready") or {}
        view_ready = readiness.get("view_ready") or {}
        window = preview.get("time_window") or (0.0, 0.0)
        stage = preview.get("stage") or readiness.get("stage") or "preview_ready"
        if task_preview:
            task_state_text = (
                f" TaskStatePreview={len(task_preview.get('task_ids') or [])} tasks"
                f"/{_safe_cell(task_preview.get('lane_count'))} lanes"
            )
        else:
            task_state_text = f" TaskState={'ready' if view_ready.get('task_states') else 'pending'}"
        return (
            "摘要已就绪"
            f" LOD0={bucket_count}桶"
            f" window=[{float(window[0]):.0f}, {float(window[1]):.0f}]"
            f" stage={stage}"
            f" 细节加载中"
            f" (LOD1={'ready' if lod_ready.get('lod1') else 'pending'},"
            f" LOD2={'ready' if lod_ready.get('lod2') else 'pending'},"
            f"{task_state_text},"
            f" EventTable={'ready' if view_ready.get('event_table') else 'pending'})"
        )

    def _show_task_state_preview(self, preview: dict[str, Any] | None) -> None:
        task_preview = dict((preview or {}).get("task_state_preview") or {})
        if not task_preview:
            self._task_state_rows = []
            self._task_state_lane_order = []
            self._set_table_rows(self.task_state_table, [])
            return
        state_totals = dict(task_preview.get("state_totals") or {})
        total_segments = sum(int(value) for value in state_totals.values())
        self._task_state_rows = []
        self._task_state_lane_order = []
        self._set_table_rows(
            self.task_state_table,
            [
                [
                    _task_preview_label(list(task_preview.get("task_ids") or [])),
                    f"{_safe_cell(task_preview.get('lane_count'))} lanes",
                    _format_window(task_preview.get("time_window")),
                    _format_state_counts(state_totals),
                    _safe_cell(total_segments),
                ]
            ],
        )

    def load_single_dataset(self, source: str | None = None) -> None:
        target = source or self._create_demo_trace(name="basic", candidate_variant=False)
        result = self.controller.viz_LoadDatasetAsync(target)
        if not result.ok:
            self._show_error(f"加载任务提交失败: {result.message}")
            return
        self.quick_source_input.setText(target)
        self._active_load_job_id = result.data["job_id"]
        self._active_load_source = target
        self._active_load_preview = result.data.get("preview")
        self._complete_load_on_next_poll = False
        self._show_task_state_preview(self._active_load_preview)
        self.footer_label.setText(f"{self._describe_load_preview(self._active_load_preview)}: {Path(target).name}")
        self.load_timer.start(50)

    def _poll_load_job(self) -> None:
        job_id = self._active_load_job_id
        if job_id is None:
            self.load_timer.stop()
            return
        snapshot = self.controller.jobs.status(job_id)
        if not snapshot.ok:
            self.load_timer.stop()
            self._active_load_job_id = None
            self._active_load_source = None
            self._active_load_preview = None
            self._complete_load_on_next_poll = False
            self._show_error(f"加载任务状态查询失败: {snapshot.message}")
            return
        status = snapshot.data["status"]
        source_name = Path(self._active_load_source or snapshot.data["payload"].get("source", "dataset")).name
        preview = snapshot.data["payload"].get("preview") or self._active_load_preview
        if status in {"queued", "running"}:
            self._complete_load_on_next_poll = False
            if preview:
                self._active_load_preview = preview
                self._show_task_state_preview(preview)
                self.footer_label.setText(f"{self._describe_load_preview(preview)}: {source_name}")
            else:
                self._show_task_state_preview(None)
                self.footer_label.setText(f"数据集加载中: {source_name}")
            return
        if status == "succeeded" and preview and not self._complete_load_on_next_poll:
            self._active_load_preview = preview
            self._complete_load_on_next_poll = True
            self._show_task_state_preview(preview)
            self.footer_label.setText(f"{self._describe_load_preview(preview)}: {source_name}")
            return
        self.load_timer.stop()
        self._active_load_job_id = None
        self._active_load_source = None
        self._active_load_preview = None
        self._complete_load_on_next_poll = False
        if status != "succeeded":
            loaded = self.controller.jobs.result(job_id)
            self._show_error(f"加载失败: {loaded.message}")
            return
        resolved = self.controller.viz_ResolveLoadDatasetJob(job_id)
        if not resolved.ok:
            self._show_error(f"加载失败: {resolved.message}")
            return
        self._complete_loaded_dataset(resolved.data)
        self.footer_label.setText(f"已加载数据集 {resolved.data}。")

    def load_compare_demo(self) -> None:
        baseline = self._create_demo_trace(name="basic", candidate_variant=False)
        candidate = self._create_demo_trace(name="basic", candidate_variant=True)
        self.compare_baseline_input.setText(baseline)
        self.compare_candidate_input.setText(candidate)
        self.load_compare_pair(baseline, candidate)

    def load_compare_pair(self, baseline: str | None = None, candidate: str | None = None) -> None:
        baseline_path = baseline or self._create_demo_trace(name="basic", candidate_variant=False)
        candidate_path = candidate or self._create_demo_trace(name="basic", candidate_variant=True)
        baseline_result = self.controller.viz_LoadDataset(baseline_path)
        candidate_result = self.controller.viz_LoadDataset(candidate_path)
        if not baseline_result.ok or not candidate_result.ok:
            self._show_error(
                "对比载入失败: "
                + "; ".join(
                    part
                    for part in [baseline_result.message if not baseline_result.ok else "", candidate_result.message if not candidate_result.ok else ""]
                    if part
                )
            )
            return
        self.compare_service = CompareService(self.controller.repository, self.controller.context_store)
        loaded_pair = self.compare_service.cmp_LoadPair(baseline_result.data, candidate_result.data)
        if not loaded_pair.ok:
            self._show_error(f"对比载入失败: {loaded_pair.message}")
            return
        scope = self.compare_service.cmp_SetScope(
            {
                "baseline_id": baseline_result.data,
                "candidate_id": candidate_result.data,
                "filter": self.controller.context_store.get().filter,
                "dimensions": ["metric", "alert", "hotspot", "interval", "task", "core", "resource", "irq"],
            }
        )
        if not scope.ok:
            self._show_error(f"对比范围初始化失败: {scope.message}")
            return
        self.state.compare_baseline_id = scope.data.baseline_id
        self.state.compare_candidate_id = scope.data.candidate_id
        self.state.active_dataset_id = scope.data.baseline_id
        self.compare_scope_label.setText(_json_text(dataclass_to_dict(scope.data)))
        self.footer_label.setText(f"已载入对比基线 {scope.data.baseline_id} vs {scope.data.candidate_id}。")
        self._set_active_tab("compare")
        self.refresh_workspace()

    def _format_digest_field(self, value: Any) -> str:
        if value is None:
            return "-"
        if isinstance(value, float):
            return f"{value:.3f}"
        if isinstance(value, (list, tuple, set)):
            return ", ".join(self._format_digest_field(item) for item in value) or "-"
        if isinstance(value, dict):
            compact = _compact_mapping(value)
            return _json_text(compact) if compact else "-"
        text = str(value).strip()
        return text or "-"

    def _set_export_contract_summary(
        self,
        *,
        closure_mode: Any = None,
        halt_reason: Any = None,
        patent_job_state: Any = None,
    ) -> None:
        closure_text = self._format_digest_field(closure_mode)
        if patent_job_state:
            closure_text = f"{closure_text} [{self._format_digest_field(patent_job_state)}]"
        self.export_closure_mode_label.setText(f"Closure Mode: {closure_text}")
        self.export_halt_reason_label.setText(f"Halt Reason: {self._format_digest_field(halt_reason)}")

    def _clear_proof_digest_panel(self) -> None:
        for label in self.proof_digest_labels.values():
            label.setText("-")

    def _update_proof_digest_panel(self, proof_digest: dict[str, Any] | None) -> None:
        digest = dict(proof_digest or {})
        metrics = {
            "scan_count": digest.get("scan_count"),
            "seek_count": digest.get("seek_count"),
            "window_hit_rate": digest.get("window_hit_rate"),
            "peak_rss_mb": digest.get("peak_rss_mb"),
        }
        self.proof_digest_labels["closure_mode"].setText(self._format_digest_field(digest.get("closure_mode")))
        self.proof_digest_labels["frontier_halt_reason"].setText(
            self._format_digest_field(digest.get("frontier_halt_reason"))
        )
        self.proof_digest_labels["proof_hash"].setText(self._format_digest_field(digest.get("proof_hash")))
        self.proof_digest_labels["snapshot_id"].setText(self._format_digest_field(digest.get("snapshot_id")))
        self.proof_digest_labels["rule_family"].setText(self._format_digest_field(digest.get("rule_family")))
        self.proof_digest_labels["missing_required_refs"].setText(
            self._format_digest_field(digest.get("missing_required_refs"))
        )
        self.proof_digest_labels["truncated_frontier_count"].setText(
            self._format_digest_field(digest.get("truncated_frontier_count"))
        )
        self.proof_digest_labels["metrics"].setText(
            ", ".join(f"{key}={self._format_digest_field(value)}" for key, value in metrics.items())
        )

    def _refresh_proof_digest_from_opened_package(self) -> Result[dict[str, Any]]:
        proof = self.repro_service.repro_QueryProof()
        if not proof.ok:
            self._clear_proof_digest_panel()
            self._set_export_contract_summary()
            return proof
        proof_digest = dict(proof.data.get("proof_digest") or {})
        self._update_proof_digest_panel(proof_digest)
        self._set_export_contract_summary(
            closure_mode=proof_digest.get("closure_mode"),
            halt_reason=proof_digest.get("frontier_halt_reason"),
        )
        return proof

    def _build_evidence_export_request(self, dataset_id: str) -> dict[str, Any] | None:
        seed_spec_text = self.evidence_seed_spec_input.toPlainText().strip()
        if not seed_spec_text:
            seed_spec_text = _json_text({"source_kind": "analysis_context", "source_payload": {}})
        try:
            seed_spec = json.loads(seed_spec_text)
        except json.JSONDecodeError as exc:
            self._show_error(f"Seed Spec JSON 无法解析: {exc}")
            return None
        if not isinstance(seed_spec, dict):
            self._show_error("Seed Spec JSON 必须是对象。")
            return None
        rule_family = [item.strip() for item in self.evidence_rule_family_input.text().split(",") if item.strip()]
        payload: dict[str, Any] = {
            "dataset_id": dataset_id,
            "seed_spec": seed_spec,
            "embodiment_mode": self.evidence_mode_combo.currentText() or "mode_a",
        }
        if rule_family:
            payload["rule_family"] = rule_family
        sidecar_source = self.evidence_sidecar_input.text().strip()
        sidecar_manifest_source = self.evidence_sidecar_manifest_input.text().strip()
        if sidecar_source:
            payload["sidecar_source"] = sidecar_source
        if sidecar_manifest_source:
            payload["sidecar_manifest_source"] = sidecar_manifest_source
        if payload["embodiment_mode"] == "mode_b" and (not sidecar_source or not sidecar_manifest_source):
            self._show_error("mode_b 需要同时提供 Sidecar Source 和 Sidecar Manifest。")
            return None
        return payload

    def open_repro_package(self, path: str | None = None) -> None:
        target = path or self._maybe_pick_directory()
        if not target:
            self._show_error("未提供复现包目录。")
            return
        opened = self.repro_service.repro_OpenPackage(target)
        if not opened.ok:
            self._show_error(f"打开复现包失败: {opened.message}")
            return
        self.state.current_package_path = target
        self.repro_package_input.setText(target)
        self.repro_status_label.setText(f"复现状态: 已打开 {target}")
        proof = self._refresh_proof_digest_from_opened_package()
        self.repro_meta_text.setPlainText(_json_text(proof.data if proof.ok else opened.data))
        self.footer_label.setText(f"已打开复现包 {target}。")
        self._set_active_tab("export")
        self.refresh_workspace()

    def restore_repro_context(self) -> None:
        restored = self.repro_service.repro_RestoreContext(None)
        if not restored.ok:
            self._show_error(f"恢复上下文失败: {restored.message}")
            return
        self.footer_label.setText("已恢复复现包上下文。")
        self.refresh_workspace()

    def load_repro_dataset(self) -> None:
        role = self.repro_role_combo.currentText() or "single"
        loaded = self.repro_service.repro_LoadAsDataset(role)
        if not loaded.ok:
            self._show_error(f"装载复现数据集失败: {loaded.message}")
            return
        self.state.active_dataset_id = loaded.data
        bundle = self.controller.repository.get(loaded.data).artifact.bundle
        self.replay_service.replay_Init(bundle.event_stream, bundle.exec_slices, self.controller.context_store.get())
        self.footer_label.setText(f"已按 {role} 角色装载复现数据集 {loaded.data}。")
        self._set_active_tab("analysis")
        self.refresh_workspace()

    def create_bookmark(self) -> None:
        label = self.bookmark_label_input.text().strip() or f"书签 {len(self._bookmark_rows) + 1}"
        result = self.controller.viz_CreateBookmark(label)
        if not result.ok:
            self._show_error(f"创建书签失败: {result.message}")
            return
        self.bookmark_label_input.setText("")
        self.footer_label.setText(f"已创建书签 {result.data.bookmark_id}。")
        self._refresh_bookmarks()

    def apply_selected_bookmark(self) -> None:
        row = self.bookmark_list.currentRow()
        if row < 0 or row >= len(self._bookmark_rows):
            self._show_error("请选择一个书签。")
            return
        bookmark_id = self._bookmark_rows[row]["bookmark_id"]
        result = self.controller.viz_ApplyBookmark(bookmark_id)
        if not result.ok:
            self._show_error(f"恢复书签失败: {result.message}")
            return
        self.footer_label.setText(f"已恢复书签 {bookmark_id}。")
        self.refresh_workspace()

    def delete_selected_bookmark(self) -> None:
        row = self.bookmark_list.currentRow()
        if row < 0 or row >= len(self._bookmark_rows):
            self._show_error("请选择一个书签。")
            return
        bookmark_id = self._bookmark_rows[row]["bookmark_id"]
        result = self.controller.viz_DeleteBookmark(bookmark_id)
        if not result.ok:
            self._show_error(f"删除书签失败: {result.message}")
            return
        self.footer_label.setText(f"已删除书签 {bookmark_id}。")
        self._refresh_bookmarks()

    def apply_filters(self) -> None:
        try:
            task_id = _coerce_int(self.filter_task_input.text())
            core_id = _coerce_int(self.filter_core_input.text())
        except ValueError:
            self._show_error("任务 ID / 核 ID 必须是整数。")
            return
        filter_spec: dict[str, Any] = {}
        if task_id is not None:
            filter_spec["task_id"] = task_id
        if core_id is not None:
            filter_spec["core_id"] = core_id
        if self.filter_event_input.text().strip():
            filter_spec["event_name"] = self.filter_event_input.text().strip()
        self.controller.viz_SetContext({"filter": filter_spec, "focused_view": "timeline"})
        self.footer_label.setText("筛选条件已更新。")

    def clear_filters(self) -> None:
        self.filter_task_input.setText("")
        self.filter_core_input.setText("")
        self.filter_event_input.setText("")
        self.controller.viz_SetContext({"filter": {}, "focused_view": "timeline"})
        self.footer_label.setText("筛选条件已清空。")

    def _plot_preview_spec(self, plot_kind: str) -> dict[str, Any] | None:
        specs = {
            "metric": {
                "title": "指标概览图",
                "axis_label": "数值",
                "note": self.metric_note_label.text(),
                "legend_items": [],
            },
            "timeline": {
                "title": "时间线 / 甘特图",
                "axis_label": "泳道",
                "note": self.timeline_note_label.text(),
                "legend_items": [
                    {"label": "ExecSlice", "bg": "#c239b3", "fg": "#ffffff"},
                    {"label": "IRQ", "bg": "#f97316", "fg": "#111827"},
                    {"label": "Untrusted 窗口", "bg": "#da7b01", "fg": "#111827"},
                ],
            },
            "compare": {
                "title": "对比差异图",
                "axis_label": "值",
                "note": self.compare_note_label.text(),
                "legend_items": [
                    {"label": "Baseline", "bg": "#94a3b8", "fg": "#111827"},
                    {"label": "Candidate", "bg": "#38bdf8", "fg": "#111827"},
                ],
            },
        }
        return specs.get(plot_kind)

    def open_plot_preview(self, plot_kind: str) -> None:
        spec = self._plot_preview_spec(plot_kind)
        if spec is None:
            self._show_error(f"未知图表类型: {plot_kind}")
            return
        window = self._plot_preview_windows.get(plot_kind)
        if window is None:
            window = PlotPreviewWindow(
                title=spec["title"],
                axis_label=spec["axis_label"],
                note=spec["note"],
                legend_items=list(spec.get("legend_items") or []),
            )
            if hasattr(self, "styleSheet") and hasattr(window, "setStyleSheet"):
                window.setStyleSheet(self.styleSheet())
            self._plot_preview_windows[plot_kind] = window
        else:
            window.setWindowTitle(spec["title"])
            window.note_label.setText(spec["note"])
            window.axis_label.setText(_vertical_axis_text(spec["axis_label"]))
        self._render_plot_preview(plot_kind, window)
        window.show()
        if hasattr(window, "raise_"):
            window.raise_()
        if hasattr(window, "activateWindow"):
            window.activateWindow()

    def _refresh_plot_previews(self) -> None:
        for plot_kind, window in list(self._plot_preview_windows.items()):
            self._render_plot_preview(plot_kind, window)

    def _render_plot_preview(self, plot_kind: str, window: PlotPreviewWindow) -> None:
        spec = self._plot_preview_spec(plot_kind)
        if spec is None:
            return
        self._prepare_plot(
            window.plot,
            left=spec["axis_label"],
            bottom="时间" if plot_kind == "timeline" else "指标",
        )
        window.note_label.setText(spec["note"])
        if plot_kind == "metric":
            metrics = self._analysis_cache.get("metrics_rows")
            if isinstance(metrics, list):
                self._render_metric_plot(metrics, target_plot=window.plot)
            else:
                window.plot.clear()
            return
        if plot_kind == "timeline":
            payload = self._analysis_cache.get("timeline_payload")
            time_window = self._analysis_cache.get("time_window")
            if payload is not None and isinstance(time_window, tuple):
                self._render_timeline_plot(
                    payload,
                    time_window,
                    target_plot=window.plot,
                    note_label=window.note_label,
                )
            else:
                window.plot.clear()
            return
        if plot_kind == "compare":
            self._render_compare_plot(self._compare_rows, target_plot=window.plot)

    def refresh_workspace(self) -> None:
        self._refresh_header()
        self._refresh_bookmarks()
        self._refresh_analysis_tab()
        self._refresh_compare_tab()
        self._refresh_export_tab()
        self._refresh_diagnostic_tab()
        self._refresh_plot_previews()

    def _refresh_header(self) -> None:
        context = self.controller.context_store.get()
        summary = self._current_dataset_summary()
        if summary is None:
            self.header_dataset_label.setText("数据集: 未加载")
            self.header_window_label.setText("时间窗: n/a")
            self.header_untrusted_label.setText("可信度: 未知")
            self.dataset_summary_label.setText("未加载数据集")
            self.dataset_source_label.setText("来源: n/a")
            self.dataset_counts_label.setText("事件/片段/任务/IRQ: -")
            self.dataset_window_label.setText("窗口: n/a")
            return
        self.header_dataset_label.setText(f"数据集: {summary.dataset_id}")
        self.header_window_label.setText(f"时间窗: {_format_window(context.time_window)}")
        trusted = "存在不可信窗口" if summary.untrusted_count else "可信"
        self.header_untrusted_label.setText(f"可信度: {trusted}")
        self.dataset_summary_label.setText(summary.dataset_id)
        self.dataset_source_label.setText(f"来源: {summary.source}")
        self.dataset_counts_label.setText(
            f"事件/片段/任务/IRQ: {summary.event_count}/{summary.slice_count}/{summary.task_count}/{summary.irq_count}"
        )
        self.dataset_window_label.setText(f"窗口: {_format_window(summary.window)}")
        self.export_context_label.setText(
            f"当前上下文: time_window={_format_window(context.time_window)} filter={context.filter}"
        )

    def _current_dataset_summary(self) -> DatasetSummary | None:
        dataset_id = self.state.active_dataset_id or self.controller.active_dataset_id
        if dataset_id is None or not self.controller.repository.has(dataset_id):
            return None
        record = self.controller.repository.get(dataset_id)
        bundle = record.artifact.bundle
        task_ids = {item.task_id for item in bundle.task_states}
        event_stream = bundle.event_stream
        time_window = (
            event_stream[0].timestamp_aligned if event_stream else 0.0,
            event_stream[-1].timestamp_aligned if event_stream else 0.0,
        )
        return DatasetSummary(
            dataset_id=dataset_id,
            source=record.artifact.source,
            event_count=len(bundle.event_stream),
            slice_count=len(bundle.exec_slices),
            task_count=len(task_ids),
            irq_count=len(bundle.irq_spans),
            untrusted_count=len(bundle.untrusted_windows),
            window=time_window,
        )

    def _refresh_bookmarks(self) -> None:
        listed = self.controller.viz_ListBookmarks()
        self._bookmark_rows = [dataclass_to_dict(item) for item in (listed.data or [])] if listed.ok else []
        self.bookmark_list.clear()
        for item in self._bookmark_rows:
            self.bookmark_list.addItem(f"{item['label']} [{item['bookmark_id']}]")

    def _analysis_request(self) -> PanelRefreshRequest | None:
        summary = self._current_dataset_summary()
        if summary is None:
            return None
        context = self.controller.context_store.get()
        time_window = context.time_window
        if time_window == (0.0, 0.0):
            time_window = summary.window
        selection = _compact_mapping(context.selection)
        transient_selection = _compact_mapping(context.transient_selection)
        return PanelRefreshRequest(
            dataset_id=summary.dataset_id,
            time_window=time_window,
            filter_spec=context.filter,
            selection=selection,
            transient_selection=transient_selection,
            effective_selection=transient_selection or selection,
            hover_target=dict(context.hover_target or {}) or None,
            lod=self.state.timeline_lod,
            focused_view=context.focused_view,
        )

    def _has_active_dataset_record(self) -> bool:
        dataset_id = self.state.active_dataset_id or self.controller.active_dataset_id
        return dataset_id is not None and self.controller.repository.has(dataset_id)

    def _has_unresolved_load_preview(self) -> bool:
        return (
            self._active_load_job_id is not None
            and self._active_load_preview is not None
            and not self._has_active_dataset_record()
        )

    def _refresh_preview_analysis_tab(self) -> None:
        self._analysis_cache = {}
        self._event_rows = []
        self._resource_rows = []
        self._alert_rows = []
        self._set_table_rows(self.event_table, [])
        self._show_task_state_preview(self._active_load_preview)
        self._set_table_rows(self.resource_table, [])
        self._set_table_rows(self.alert_table, [])
        self.metric_plot.clear()
        self.timeline_plot.clear()

    def _refresh_analysis_tab(self) -> None:
        request = self._analysis_request()
        if request is None:
            self._analysis_cache = {}
            if self._has_unresolved_load_preview():
                self._refresh_preview_analysis_tab()
                return
            self._set_table_rows(self.event_table, [])
            self._set_table_rows(self.task_state_table, [])
            self._set_table_rows(self.resource_table, [])
            self._set_table_rows(self.alert_table, [])
            self.metric_plot.clear()
            self.timeline_plot.clear()
            return

        scope = {
            "dataset_id": request.dataset_id,
            "time_window": request.time_window,
            "filter": request.filter_spec,
            "lod": request.lod,
        }
        timeline = self.controller.viz_QueryTimelineLOD(scope)
        self.controller.active_dataset_id = request.dataset_id
        task_query = TaskStateQuery(
            time_window=request.time_window,
            lane_group=self.state.timeline_grouping,
            task_filter=_task_query_task_filter(request.filter_spec, request.effective_selection),
            anchor_ref=self.controller.context_store.get().evidence_anchor,
        )
        runtime_filter = _task_query_runtime_filter(request.filter_spec, request.effective_selection)
        if runtime_filter:
            setattr(task_query, "_runtime_filter", runtime_filter)
        task_states = self.controller.viz_QueryTaskStates(task_query)
        metrics = self.controller.viz_QueryMetricSeries(scope)
        event_page = self.controller.viz_QueryEventTable(
            EventTableQuery(
                filter={
                    "dataset_id": request.dataset_id,
                    **request.filter_spec,
                    "t_begin": request.time_window[0],
                    "t_end": request.time_window[1],
                },
                limit=20,
            )
        )
        resource_scope = dict(scope)
        resource_scope["filter"] = {
            **request.filter_spec,
            **(
                {"task_id": request.effective_selection["task_id"]}
                if request.effective_selection.get("task_id") is not None
                else {}
            ),
            **(
                {"resource_id": request.effective_selection["resource_id"]}
                if request.effective_selection.get("resource_id") is not None
                else {}
            ),
        }
        resource = self.controller.viz_QueryResourceGraph(resource_scope)
        alerts = self.controller.viz_QueryAlerts(scope)

        self._analysis_cache = {
            "timeline": dataclass_to_dict(timeline.data) if timeline.ok else {"error": timeline.message},
            "timeline_payload": timeline.data if timeline.ok else None,
            "task_states": dataclass_to_dict(task_states.data) if task_states.ok else {"error": task_states.message},
            "metrics": metrics.data if metrics.ok else {"error": metrics.message},
            "metrics_rows": metrics.data if metrics.ok else None,
            "events": dataclass_to_dict(event_page.data) if event_page.ok else {"error": event_page.message},
            "resource": resource.data if resource.ok else {"error": resource.message},
            "alerts": dataclass_to_dict(alerts.data) if alerts.ok else {"error": alerts.message},
            "time_window": request.time_window,
        }

        if metrics.ok:
            self._render_metric_cards(metrics.data)
            self._render_metric_plot(metrics.data)
        if timeline.ok:
            self._render_timeline_plot(timeline.data, request.time_window)
        if event_page.ok:
            self._event_rows = [dataclass_to_dict(item) for item in event_page.data.items]
            self._set_table_rows(
                self.event_table,
                [
                    [
                        _safe_cell(event["timestamp_aligned"]),
                        _safe_cell(event["core_id"]),
                        _safe_cell(event["task_id"]),
                        _safe_cell(event["event_name"]),
                        _safe_cell(event["obj_id"]),
                        "yes" if not event["trust_tags"] else ",".join(event["trust_tags"]),
                    ]
                    for event in self._event_rows
                ],
            )
        else:
            self._event_rows = []
            self._set_table_rows(self.event_table, [])
        if task_states.ok:
            state_legend = dict(task_states.data.state_legend)
            self._task_state_rows = [dataclass_to_dict(item) for item in task_states.data.rows]
            self._task_state_lane_order = list(task_states.data.lane_order)
            self._set_table_rows(
                self.task_state_table,
                [
                    [
                        _safe_cell(item["task_id"]),
                        _safe_cell(item["lane_label"]),
                        _format_window(item.get("time_window")),
                        _format_state_counts(item.get("state_counts") or {}, state_legend),
                        _safe_cell(len(item.get("segments") or [])),
                    ]
                    for item in self._task_state_rows
                ],
            )
        else:
            self._task_state_rows = []
            self._task_state_lane_order = []
        if resource.ok:
            self._resource_rows = []
            for hotspot in resource.data.get("hotspots", []):
                resource_id = hotspot["node_id"].split(":", 1)[1] if hotspot.get("node_id", "").startswith("obj:") else None
                self._resource_rows.append(
                    {
                        "label": hotspot["node_id"],
                        "value": hotspot.get("count", 0),
                        "kind": "hotspot",
                        "evidence": "-",
                        "resource_id": int(resource_id, 0) if resource_id is not None else None,
                    }
                )
            for edge in resource.data.get("wait_edges", []):
                self._resource_rows.append(
                    {
                        "label": f"task:{edge['from_task']} -> obj:{edge['to_obj']}",
                        "value": 1,
                        "kind": "wait_edge",
                        "evidence": edge.get("evidence_ref", "-"),
                        "resource_id": edge.get("to_obj"),
                        "task_id": edge.get("from_task"),
                        "owner_task_id": edge.get("owner_task_id"),
                    }
                )
            for edge in resource.data.get("hold_edges", []):
                self._resource_rows.append(
                    {
                        "label": f"task:{edge['from_task']} holds obj:{edge['to_obj']}",
                        "value": edge.get("owner_task", edge.get("task_id", "-")),
                        "kind": "hold_edge",
                        "evidence": edge.get("evidence_ref", "-"),
                        "resource_id": edge.get("to_obj"),
                        "task_id": edge.get("from_task"),
                        "owner_task_id": edge.get("owner_task", edge.get("task_id")),
                    }
                )
            for chain in resource.data.get("wait_chains", []):
                self._resource_rows.append(
                    {
                        "label": " -> ".join(chain["path"]),
                        "value": chain.get("owner_task_id", "-"),
                        "kind": "wait_chain",
                        "evidence": chain.get("wait_evidence") or chain.get("hold_evidence") or "-",
                        "resource_id": chain.get("obj_id"),
                        "task_id": chain.get("task_id"),
                        "owner_task_id": chain.get("owner_task_id"),
                    }
                )
            self._set_table_rows(
                self.resource_table,
                [
                    [
                        item["label"],
                        _safe_cell(item["value"]),
                        item["kind"],
                        item["evidence"],
                    ]
                    for item in self._resource_rows
                ],
            )
        if alerts.ok:
            self._alert_rows = [dataclass_to_dict(item) for item in alerts.data]
            self._set_table_rows(
                self.alert_table,
                [
                    [
                        _safe_cell(item["type"]),
                        _safe_cell(item["severity"]),
                        _safe_cell(item["object_scope"]),
                        _format_window(item["time_window"]),
                        "yes" if item["trusted"] else "no",
                    ]
                    for item in self._alert_rows
                ],
            )

        self.analysis_details.setPlainText(
            _json_text(
                {
                    "request": dataclass_to_dict(request),
                    "context": self._runtime_context_dict(),
                    "selection": request.selection,
                    "transient_selection": request.transient_selection,
                    "effective_selection": request.effective_selection,
                    "hover_target": request.hover_target,
                    "availability": self.availability.notes(),
                }
            )
        )

    def _render_metric_cards(self, metrics: list[dict[str, Any]]) -> None:
        metric_map = {metric["metric_id"]: metric for metric in metrics}
        values = {
            "cpu_utilization": f"{100.0 * float(metric_map.get('cpu_utilization', {}).get('summary', {}).get('avg_utilization', 0.0)):.1f}%",
            "blocked_time": f"{float(metric_map.get('blocked_time', {}).get('summary', {}).get('total_blocked_time', 0.0)):.1f}",
            "context_switch_count": str(metric_map.get("context_switch_count", {}).get("summary", {}).get("count", 0)),
            "irq_busy_time": str(metric_map.get("irq_busy_time", {}).get("summary", {}).get("irq_count", 0)),
        }
        for metric_id, label in self.metric_value_labels.items():
            label.setText(values.get(metric_id, "--"))

    def _render_metric_plot(self, metrics: list[dict[str, Any]], *, target_plot: PlotWidget | None = None) -> None:
        plot = target_plot or self.metric_plot
        plot.clear()
        bucket_ready = any(metric.get("bucket_series") for metric in metrics)
        if bucket_ready:
            if hasattr(plot, "addLegend"):
                plot.addLegend()
            max_x = 1.0
            for metric in metrics:
                bucket_series = metric.get("bucket_series") or []
                if not bucket_series:
                    continue
                x_values = [float(point["midpoint"]) for point in bucket_series]
                y_values = [float(point["value"]) for point in bucket_series]
                if not x_values:
                    continue
                max_x = max(max_x, x_values[-1])
                if hasattr(plot, "plot"):
                    plot.plot(
                        x_values,
                        y_values,
                        pen=mkPen(_color_for_key(metric["metric_id"]), width=2),
                        name=metric["metric_id"],
                    )
            if hasattr(plot, "setXRange"):
                plot.setXRange(0, max_x)
            return
        points = []
        labels = []
        for metric in metrics:
            summary = metric["summary"]
            labels.append(metric["metric_id"])
            if "avg_utilization" in summary:
                points.append(float(summary["avg_utilization"]) * 100.0)
            elif "total_blocked_time" in summary:
                points.append(float(summary["total_blocked_time"]))
            elif "count" in summary:
                points.append(float(summary["count"]))
            elif "irq_count" in summary:
                points.append(float(summary["irq_count"]))
            else:
                points.append(0.0)
        for index, value in enumerate(points):
            plot.addItem(
                BarGraphItem(
                    x=[index],
                    height=[value],
                    width=0.65,
                    y0=0,
                    brush=mkBrush(_color_for_key(labels[index])),
                    pen=mkPen("#0b1220", width=1),
                )
            )
        if hasattr(plot, "setXRange"):
            plot.setXRange(-1, max(len(points), 1))
        axis = plot.getAxis("bottom") if hasattr(plot, "getAxis") else None
        if axis is not None and hasattr(axis, "setTicks"):
            axis.setTicks([[(index, labels[index]) for index in range(len(labels))]])

    def _render_timeline_plot(
        self,
        payload: Any,
        time_window: tuple[float, float],
        *,
        target_plot: PlotWidget | None = None,
        note_label: QLabel | None = None,
    ) -> None:
        plot = target_plot or self.timeline_plot
        note = note_label or self.timeline_note_label
        plot.clear()
        slices = payload.slices
        irqs = payload.irq_spans
        buckets = getattr(payload, "buckets", [])
        events = getattr(payload, "events", [])
        if payload.lod == 0:
            lane_keys = ["Summary"]
            for item in buckets:
                value = float(item.get("event_count", 0))
                plot.addItem(
                    BarGraphItem(
                        x=[(float(item["t_begin"]) + float(item["t_end"])) / 2.0],
                        height=[max(0.1, min(value / 5.0, 0.8))],
                        width=[max(float(item["t_end"]) - float(item["t_begin"]), 1.0)],
                        y0=0.6,
                        brush=mkBrush("#38bdf8"),
                        pen=mkPen("#0f6cbd", width=1),
                    )
                )
            if hasattr(plot, "setXRange"):
                plot.setXRange(time_window[0], time_window[1])
            if hasattr(plot, "setYRange"):
                plot.setYRange(0, 2)
            axis = plot.getAxis("left") if hasattr(plot, "getAxis") else None
            if axis is not None and hasattr(axis, "setTicks"):
                axis.setTicks([[(1, "Summary")]])
            note.setText("当前 LOD0 使用真实时间桶摘要展示事件/片段密度。")
            return
        if payload.lod == 2:
            if self.state.timeline_grouping == "按任务":
                lane_keys = sorted({f"Task {item['task_id']}" for item in events if item.get("task_id") is not None})
                lane_for_event = lambda event: f"Task {event['task_id']}" if event.get("task_id") is not None else "Task ?"
            else:
                lane_keys = sorted({f"Core {item['core_id']}" for item in events})
                lane_for_event = lambda event: f"Core {event['core_id']}"
            if not lane_keys:
                lane_keys = ["Core 0"]
            lane_map = {label: index + 1 for index, label in enumerate(lane_keys)}
            for event in events:
                lane = lane_map.get(lane_for_event(event), 1)
                plot.addItem(
                    BarGraphItem(
                        x=[float(event["timestamp"])],
                        height=[0.35],
                        width=[1.0],
                        y0=lane - 0.18,
                        brush=mkBrush(_color_for_key(f"event:{event['event_name']}")),
                        pen=mkPen("#0b1220", width=1),
                    )
                )
            if hasattr(plot, "setXRange"):
                plot.setXRange(time_window[0], time_window[1])
            if hasattr(plot, "setYRange"):
                plot.setYRange(0, len(lane_keys) + 1)
            axis = plot.getAxis("left") if hasattr(plot, "getAxis") else None
            if axis is not None and hasattr(axis, "setTicks"):
                axis.setTicks([[(lane_map[label], label) for label in lane_keys]])
            note.setText("当前 LOD2 使用原始事件点展示最细粒度落点。")
            return
        axis_ticks: list[tuple[int, str]] = []
        lane_map: dict[tuple[str, str], int] = {}
        if self.state.timeline_grouping == "按任务":
            task_lane_labels = list(self._task_state_lane_order) or sorted(
                {f"Task {item.task_id}" for item in slices}
                | {f"Task {item['task_id']}" for item in self._task_state_rows if item.get("task_id") is not None}
            )
            if not task_lane_labels:
                task_lane_labels = ["Task 0"]
            irq_lane_labels = sorted({f"Core {item.core_id}" for item in irqs})
            lane_index = 1
            for label in task_lane_labels:
                lane_map[(label, "exec")] = lane_index
                axis_ticks.append((lane_index, label))
                lane_index += 1
            for label in irq_lane_labels:
                lane_map[(label, "irq")] = lane_index
                axis_ticks.append((lane_index, f"IRQ {label}"))
                lane_index += 1
            max_lane_index = lane_index - 1
        else:
            core_lane_labels = list(self._task_state_lane_order) or sorted(
                {f"Core {item.core_id}" for item in slices} | {f"Core {item.core_id}" for item in irqs}
            )
            if not core_lane_labels:
                core_lane_labels = ["Core 0"]
            lane_index = 1
            for label in core_lane_labels:
                lane_map[(label, "exec")] = lane_index
                axis_ticks.append((lane_index, f"{label} / 执行"))
                lane_index += 1
                lane_map[(label, "irq")] = lane_index
                axis_ticks.append((lane_index, f"{label} / IRQ"))
                lane_index += 1
            max_lane_index = lane_index - 1

        summary = self._current_dataset_summary()
        if summary is not None:
            record = self.controller.repository.get(summary.dataset_id)
            for window in record.artifact.bundle.untrusted_windows:
                plot.addItem(
                    BarGraphItem(
                        x=[(window.t_begin + window.t_end) / 2.0],
                        height=[max_lane_index + 0.6],
                        width=[max(window.t_end - window.t_begin, 1.0)],
                        y0=0.4,
                        brush=mkBrush(214, 123, 1, 75),
                        pen=mkPen("#da7b01", width=1),
                    )
                )

        for item in slices:
            if self.state.timeline_grouping == "按任务":
                lane = lane_map.get((f"Task {item.task_id}", "exec"), 1)
            else:
                lane = lane_map.get((f"Core {item.core_id}", "exec"), 1)
            plot.addItem(
                BarGraphItem(
                    x=[(item.t_begin + item.t_end) / 2.0],
                    height=[0.55],
                    width=[max(item.t_end - item.t_begin, 1.0)],
                    y0=lane - 0.27,
                    brush=mkBrush(_color_for_key(f"task:{item.task_id}")),
                    pen=mkPen("#111827", width=1),
                )
            )
        for irq in irqs:
            lane = lane_map.get((f"Core {irq.core_id}", "irq"), max_lane_index if max_lane_index > 0 else 1)
            plot.addItem(
                BarGraphItem(
                    x=[(irq.t_begin + irq.t_end) / 2.0],
                    height=[0.22],
                    width=[max(irq.t_end - irq.t_begin, 1.0)],
                    y0=lane + 0.18,
                    brush=mkBrush("#f97316"),
                    pen=mkPen("#fb923c", width=1),
                )
            )

        playback = self.replay_service.replay_GetState()
        if playback.ok:
            cursor_ts = _playback_timestamp(playback.data, self.controller.context_store.get().playback_runtime)
            plot.addItem(
                InfiniteLine(
                    pos=cursor_ts,
                    angle=90,
                    pen=mkPen("#f8fafc", width=2),
                )
            )

        if hasattr(plot, "setXRange"):
            plot.setXRange(time_window[0], time_window[1])
        if hasattr(plot, "setYRange"):
            plot.setYRange(0, max_lane_index + 1)
        axis = plot.getAxis("left") if hasattr(plot, "getAxis") else None
        if axis is not None and hasattr(axis, "setTicks"):
            axis.setTicks([axis_ticks])
        if self.state.timeline_grouping == "按任务":
            note.setText("当前 LOD1 按任务显示 ExecSlice，并把 IRQ 拆到独立 Core IRQ 泳道；Untrusted 窗口为全局覆盖层。")
        else:
            note.setText("当前 LOD1 按核显示执行/IRQ 子泳道；Untrusted 窗口为全局覆盖层。")

    def _compare_dimension_specs(self) -> dict[str, dict[str, str]]:
        return {
            "metric": {"label": "指标", "summary_key": "metric_changes", "id_key": "metric_id", "header": "指标"},
            "alert": {"label": "告警", "summary_key": "alert_changes", "id_key": "alert_type", "header": "告警"},
            "hotspot": {"label": "热点", "summary_key": "hotspot_changes", "id_key": "node_id", "header": "热点对象"},
            "interval": {"label": "区间", "summary_key": "interval_changes", "id_key": "interval_type", "header": "区间"},
            "task": {"label": "任务", "summary_key": "task_changes", "id_key": "task_id", "header": "任务"},
            "core": {"label": "核", "summary_key": "core_changes", "id_key": "core_id", "header": "核"},
            "resource": {"label": "资源", "summary_key": "resource_changes", "id_key": "resource_id", "header": "资源"},
            "irq": {"label": "IRQ", "summary_key": "irq_changes", "id_key": "irq_id", "header": "IRQ"},
        }

    def _compare_dimension_from_label(self, label: str) -> str:
        for dimension, spec in self._compare_dimension_specs().items():
            if spec["label"] == label:
                return dimension
        return label or "metric"

    def _compare_dimension_label(self, dimension: str) -> str:
        return self._compare_dimension_specs().get(dimension, {}).get("label", dimension)

    def _set_compare_jump_buttons(self, detail: dict[str, Any] | None) -> None:
        main_target = _compare_main_jump_target(detail)
        peer_target = _compare_peer_jump_target(detail)
        self.compare_jump_button.setText(
            _compare_jump_button_text("跳转到", main_target, "跳转证据")
        )
        self.compare_jump_button.setEnabled(bool(main_target and main_target.get("time_window")))
        self.compare_peer_jump_button.setText(
            _compare_jump_button_text("切换到", peer_target, "切换到对侧证据")
        )
        self.compare_peer_jump_button.setEnabled(bool(peer_target and peer_target.get("time_window")))

    def _set_compare_table_row_selected(self, row: int) -> None:
        if hasattr(self.compare_table, "setCurrentCell"):
            self.compare_table.setCurrentCell(row, 0)
            return
        if hasattr(self.compare_table, "setCurrentRow"):
            self.compare_table.setCurrentRow(row)

    def _show_compare_detail(
        self,
        compare_row: dict[str, Any],
        detail_row: dict[str, Any] | None,
        *,
        error: str | None = None,
        row_index: int | None = None,
    ) -> None:
        self._compare_selected_detail = detail_row
        self._set_compare_jump_buttons(detail_row)
        self.compare_details.setPlainText(
            _format_compare_detail_text(
                compare_row.get("label", compare_row.get("metric_id", "-")),
                detail_row,
                error=error,
            )
        )
        if row_index is not None:
            self._set_compare_table_row_selected(row_index)

    def _queue_compare_detail_restore(self) -> None:
        if not self._compare_selected_detail:
            self._compare_restore_request = None
            return
        diff_id = self._compare_selected_detail.get("diff_id")
        if diff_id is None:
            self._compare_restore_request = None
            return
        self._compare_restore_request = {
            "diff_id": str(diff_id),
            "dimension": self.state.compare_dimension,
        }

    def _restore_compare_detail_after_refresh(self) -> None:
        request = self._compare_restore_request
        self._compare_restore_request = None
        if not request or request.get("dimension") != self.state.compare_dimension:
            return
        diff_id = request.get("diff_id")
        if not diff_id:
            return
        for row_index, compare_row in enumerate(self._compare_rows):
            if compare_row.get("detail_target") != diff_id:
                continue
            detail = self.compare_service.cmp_QueryDiffDetail(diff_id)
            self._show_compare_detail(
                compare_row,
                detail.data if detail.ok else None,
                error=None if detail.ok else detail.message,
                row_index=row_index,
            )
            return

    def _compare_summary_dimensions(self, summary: dict[str, Any]) -> list[str]:
        scope = summary.get("scope") or {}
        return list(scope.get("dimensions") or summary.get("dimensions") or [])

    def _compare_summary_rows(self, summary: dict[str, Any], dimension: str) -> list[dict[str, Any]]:
        specs = self._compare_dimension_specs()
        spec = specs.get(dimension, specs["metric"])
        if dimension == "metric":
            return list(summary.get("metric_changes") or summary.get("metrics") or [])
        return list(summary.get(spec["summary_key"]) or [])

    def _compare_summary_trusted(self, summary: dict[str, Any]) -> bool:
        trust_summary = summary.get("trust_summary") or {}
        if "trusted" in trust_summary:
            return bool(trust_summary.get("trusted"))
        return not bool(summary.get("untrusted", False))

    def _compare_target_label(self, dimension: str, row: dict[str, Any]) -> str:
        if dimension == "metric":
            return str(row.get("metric_id", "-"))
        if dimension == "alert":
            return str(row.get("alert_type", "-"))
        if dimension == "hotspot":
            return str(row.get("node_id", "-"))
        if dimension == "interval":
            return str(row.get("interval_type", "-"))
        if dimension == "task":
            return f"Task {row.get('task_id', '-')}"
        if dimension == "core":
            return f"Core {row.get('core_id', '-')}"
        if dimension == "resource":
            return f"obj:{row.get('resource_id', '-')}"
        if dimension == "irq":
            return f"IRQ {row.get('irq_id', '-')}"
        return str(row)

    def _available_compare_dimensions(self, summary: dict[str, Any]) -> list[str]:
        available: list[str] = []
        specs = self._compare_dimension_specs()
        requested = self._compare_summary_dimensions(summary)
        for dimension in requested:
            spec = specs.get(dimension)
            if spec is None:
                continue
            if self._compare_summary_rows(summary, dimension):
                available.append(dimension)
        if self._compare_summary_rows(summary, "metric") and "metric" not in available:
            available.insert(0, "metric")
        return available

    def _render_compare_plot(self, rows: list[dict[str, Any]], *, target_plot: PlotWidget | None = None) -> None:
        plot = target_plot or self.compare_plot
        plot.clear()
        for index, row in enumerate(rows):
            plot.addItem(
                BarGraphItem(
                    x=[index - 0.16],
                    height=[float(row.get("baseline", 0.0))],
                    width=0.28,
                    y0=0,
                    brush=mkBrush("#94a3b8"),
                    pen=mkPen("#475569", width=1),
                )
            )
            plot.addItem(
                BarGraphItem(
                    x=[index + 0.16],
                    height=[float(row.get("candidate", 0.0))],
                    width=0.28,
                    y0=0,
                    brush=mkBrush("#38bdf8"),
                    pen=mkPen("#0f6cbd", width=1),
                )
            )
        if hasattr(plot, "setXRange"):
            plot.setXRange(-1, max(len(rows), 1))
        axis = plot.getAxis("bottom") if hasattr(plot, "getAxis") else None
        if axis is not None and hasattr(axis, "setTicks"):
            axis.setTicks([[(index, row["label"]) for index, row in enumerate(rows)]])

    def _render_compare_dimension(self, dimension: str) -> None:
        summary = self._compare_cache.get("summary") or {}
        details = self._compare_cache.get("details") or []
        specs = self._compare_dimension_specs()
        spec = specs.get(dimension, specs["metric"])
        summary_rows = self._compare_summary_rows(summary, dimension)
        detail_lookup: dict[str, dict[str, Any]] = {}
        for item in details:
            if _compare_detail_dimension(item) != dimension:
                continue
            key = _compare_detail_target_value(item, spec["id_key"])
            detail_lookup[key] = item

        self.state.compare_dimension = dimension
        self._compare_rows = []
        self._compare_selected_detail = None
        self._set_compare_jump_buttons(None)
        self._configure_table(self.compare_table, [spec["header"], "Baseline", "Candidate", "Delta", "可信"])

        trusted = self._compare_summary_trusted(summary)
        for row in summary_rows:
            target_id = row.get(spec["id_key"])
            detail = detail_lookup.get(str(target_id))
            self._compare_rows.append(
                {
                    **dict(row),
                    "dimension": dimension,
                    "label": self._compare_target_label(dimension, row),
                    "detail_target": detail.get("diff_id")
                    if detail is not None
                    else {"dimension": dimension, spec["id_key"]: target_id},
                }
            )

        self._set_table_rows(
            self.compare_table,
            [
                [
                    row["label"],
                    _safe_cell(row.get("baseline")),
                    _safe_cell(row.get("candidate")),
                    _safe_cell(row.get("delta")),
                    "yes" if trusted else "no",
                ]
                for row in self._compare_rows
            ],
        )

        self._render_compare_plot(self._compare_rows)

        self.compare_note_label.setText(
            f"当前维度: {self._compare_dimension_label(dimension)}；可查看 diff detail 并跳转到证据链。"
        )
        self.compare_details.setPlainText(
            _json_text(
                {
                    "active_dimension": dimension,
                    "summary": summary,
                    "detail_count": len([item for item in details if _compare_detail_dimension(item) == dimension]),
                }
            )
        )

    def _refresh_compare_tab(self) -> None:
        specs = self._compare_dimension_specs()
        if not self.state.compare_baseline_id or not self.state.compare_candidate_id:
            self._compare_cache = {}
            self._compare_rows = []
            self._compare_restore_request = None
            self.compare_plot.clear()
            self.compare_dimension_combo.clear()
            self.compare_dimension_combo.addItems([specs["metric"]["label"]])
            self._set_compare_jump_buttons(None)
            self.compare_details.setPlainText(
                _json_text(
                    {
                        "message": "未加载双基线对比",
                        "availability": self.availability.notes(),
                    }
                )
            )
            self._set_table_rows(self.compare_table, [])
            return
        summary = self.compare_service.cmp_QueryDiffSummary()
        details = self.compare_service.cmp_ListDiffDetails()
        if not summary.ok or not details.ok:
            self._compare_cache = {}
            self._compare_rows = []
            self._compare_restore_request = None
            self._set_compare_jump_buttons(None)
            self.compare_details.setPlainText(
                _json_text(
                    {
                        "summary_error": summary.message if not summary.ok else None,
                        "detail_error": details.message if not details.ok else None,
                    }
                )
            )
            return
        self._compare_cache = {"summary": summary.data, "details": details.data}
        available_dimensions = self._available_compare_dimensions(summary.data)
        current_dimension = self.state.compare_dimension if self.state.compare_dimension in available_dimensions else ""
        if not current_dimension:
            current_dimension = available_dimensions[0] if available_dimensions else "metric"
        self.compare_dimension_combo.clear()
        self.compare_dimension_combo.addItems([self._compare_dimension_label(item) for item in available_dimensions] or [specs["metric"]["label"]])
        self.compare_dimension_combo.setCurrentText(self._compare_dimension_label(current_dimension))
        self._render_compare_dimension(current_dimension)
        self._restore_compare_detail_after_refresh()

    def _refresh_export_tab(self) -> None:
        context = self.controller.context_store.get()
        self.export_context_label.setText(
            f"当前上下文: time_window={_format_window(context.time_window)} filter={context.filter}"
        )
        if self.state.current_package_path:
            self.repro_status_label.setText(f"复现状态: 已打开 {self.state.current_package_path}")
        if self._active_export_job_id is not None:
            snapshot = self.controller.jobs.status(self._active_export_job_id)
            if snapshot.ok and snapshot.data["status"] in {"queued", "running"}:
                mode = self._active_export_mode or "full"
                payload = snapshot.data.get("payload") or {}
                self._set_export_contract_summary(
                    closure_mode="pending" if mode == "evidence" else None,
                    halt_reason=payload.get("halt_reason"),
                    patent_job_state=payload.get("patent_job_state"),
                )
                self.export_status_label.setText(f"导出状态: {mode} 导出中")
                return
        if self._export_cache.get("status") == "succeeded":
            mode = self._export_cache.get("mode", "full")
            if mode == "evidence":
                result = self._export_cache.get("result") or {}
                self._set_export_contract_summary(
                    closure_mode=result.get("closure_mode"),
                    halt_reason=(result.get("proof_digest") or {}).get("frontier_halt_reason"),
                )
            self.export_status_label.setText(f"导出状态: {mode} 导出完成")
            return
        if self.state.last_export_path:
            self.export_status_label.setText(f"导出状态: 最近输出 {self.state.last_export_path}")

    def _refresh_diagnostic_tab(self) -> None:
        self.context_dump.setPlainText(_json_text(self._runtime_context_dict()))
        self.query_dump.setPlainText(
            _json_text(
                {
                    "analysis": self._analysis_cache,
                    "compare": self._compare_cache,
                    "export": self._export_cache,
                }
            )
        )

    def _set_table_rows(self, widget: QTableWidget, rows: list[list[str]]) -> None:
        if not hasattr(widget, "setRowCount"):
            return
        widget.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column_index, value in enumerate(row):
                widget.setItem(row_index, column_index, QTableWidgetItem(value))

    def _on_context_changed(self, context: AnalysisContext) -> None:
        previous = self._last_context_snapshot
        self._last_context_snapshot = context
        if self._context_requires_workspace_refresh(previous, context):
            self.refresh_workspace()
        elif self._context_requires_analysis_refresh(previous, context):
            self._refresh_analysis_tab()
            self._refresh_diagnostic_tab()
        else:
            self._update_runtime_context_views(context)
        if self._active_load_job_id is not None:
            source_name = Path(self._active_load_source or "dataset").name
            if self._active_load_preview:
                self.footer_label.setText(f"{self._describe_load_preview(self._active_load_preview)}: {source_name}")
            else:
                self.footer_label.setText(f"数据集加载中: {source_name}")
            return
        if self._active_export_job_id is not None:
            snapshot = self.controller.jobs.status(self._active_export_job_id)
            if snapshot.ok and snapshot.data["status"] in {"queued", "running"}:
                mode = self._active_export_mode or "full"
                self.footer_label.setText(f"{mode} 导出中")
                return
        self.footer_label.setText(self._context_footer_text(context))

    def _on_timeline_mode_changed(self, text: str) -> None:
        self.state.timeline_lod = {"LOD 0": 0, "LOD 1": 1, "LOD 2": 2}.get(text, 1)
        self.refresh_workspace()

    def _on_timeline_grouping_changed(self, text: str) -> None:
        self.state.timeline_grouping = text or "按核"
        self.refresh_workspace()

    def _on_compare_dimension_changed(self, text: str) -> None:
        if not self._compare_cache:
            return
        dimension = self._compare_dimension_from_label(text)
        self._render_compare_dimension(dimension)
        self._refresh_plot_previews()

    def _jump_to(self, request: EvidenceJumpRequest) -> None:
        self.controller.viz_SetContext(
            {
                "time_window": request.time_window,
                "selection": request.selection,
                "evidence_anchor": request.evidence_anchor,
                "focused_view": request.focused_view,
            }
        )

    def commit_transient_selection(self) -> None:
        self.controller.viz_CommitTransientSelection()

    def cancel_transient_selection(self, *, clear_hover_target: bool = True) -> None:
        self.controller.viz_CancelTransientSelection(clear_hover_target=clear_hover_target)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        for window in list(self._plot_preview_windows.values()):
            window.close()
        self._plot_preview_windows.clear()
        super().closeEvent(event)

    def _preview_hover_target(
        self,
        hover_target: dict[str, Any] | None,
        *,
        transient_selection: dict[str, Any] | None = None,
    ) -> None:
        normalized_selection = None if transient_selection is None else _compact_mapping(transient_selection)
        current = self.controller.context_store.get()
        if dict(current.hover_target or {}) == dict(hover_target or {}) and _compact_mapping(
            current.transient_selection
        ) == (normalized_selection or {}):
            return
        self.controller.viz_SetHoverTarget(
            hover_target,
            transient_selection=normalized_selection,
        )

    def _on_event_row_hover(self, row: int, column: int) -> None:
        if row < 0 or row >= len(self._event_rows):
            self.cancel_transient_selection()
            return
        event = self._event_rows[row]
        self._preview_hover_target(
            {
                "view": "event_table",
                "kind": "event",
                "event_uid": event.get("event_uid"),
                "ref_key": event.get("ref_key"),
                "timestamp": event.get("timestamp_aligned"),
                "task_id": event.get("task_id"),
                "core_id": event.get("core_id"),
            },
            transient_selection={
                "task_id": event.get("task_id"),
                "core_id": event.get("core_id"),
                "event_uid": event.get("event_uid"),
            },
        )

    def _on_task_state_row_hover(self, row: int, column: int) -> None:
        if row < 0 or row >= len(self._task_state_rows):
            self.cancel_transient_selection()
            return
        task_row = self._task_state_rows[row]
        segment = (task_row.get("segments") or [{}])[0]
        transient_selection = {
            "task_id": task_row.get("task_id"),
            "resource_id": segment.get("related_obj"),
        }
        self._preview_hover_target(
            {
                "view": "task_states",
                "kind": "task_state",
                "task_id": task_row.get("task_id"),
                "lane_label": task_row.get("lane_label"),
                "time_window": task_row.get("time_window"),
            },
            transient_selection=transient_selection,
        )

    def _on_resource_row_hover(self, row: int, column: int) -> None:
        if row < 0 or row >= len(self._resource_rows):
            self.cancel_transient_selection()
            return
        item = self._resource_rows[row]
        self._preview_hover_target(
            {
                "view": "resource_table",
                "kind": item.get("kind"),
                "label": item.get("label"),
                "resource_id": item.get("resource_id"),
                "task_id": item.get("task_id"),
                "evidence": item.get("evidence"),
            },
            transient_selection={
                "task_id": item.get("task_id"),
                "resource_id": item.get("resource_id"),
                "resource_label": item.get("label"),
            },
        )

    def _on_alert_row_hover(self, row: int, column: int) -> None:
        if row < 0 or row >= len(self._alert_rows):
            self.cancel_transient_selection()
            return
        alert = self._alert_rows[row]
        object_scope = dict(alert.get("object_scope") or {})
        self._preview_hover_target(
            {
                "view": "alerts",
                "kind": "alert",
                "alert_id": alert.get("alert_id"),
                "alert_type": alert.get("type"),
                "severity": alert.get("severity"),
                "time_window": alert.get("time_window"),
            },
            transient_selection=object_scope,
        )

    def _on_event_row_selected(self, row: int, column: int) -> None:
        if row < 0 or row >= len(self._event_rows):
            return
        event = self._event_rows[row]
        ts = float(event["timestamp_aligned"])
        self._jump_to(
            EvidenceJumpRequest(
                time_window=(max(ts - 120.0, 0.0), ts + 120.0),
                selection={
                    "task_id": event.get("task_id"),
                    "core_id": event.get("core_id"),
                    "event_uid": event.get("event_uid"),
                },
                evidence_anchor={"ref_key": event.get("ref_key"), "event_uid": event.get("event_uid")},
                focused_view="event_table",
            )
        )

    def _on_task_state_selected(self, row: int, column: int) -> None:
        if row < 0 or row >= len(self._task_state_rows):
            return
        task_row = self._task_state_rows[row]
        segment = (task_row.get("segments") or [{}])[0]
        selection = {"task_id": task_row.get("task_id")}
        if segment.get("related_obj") is not None:
            selection["resource_id"] = segment["related_obj"]
        evidence_ref = segment.get("evidence_ref") or {}
        self._jump_to(
            EvidenceJumpRequest(
                time_window=(
                    float(task_row.get("time_window", [0.0, 0.0])[0]),
                    float(task_row.get("time_window", [0.0, 0.0])[1]),
                ),
                selection=selection,
                evidence_anchor={"ref_key": evidence_ref.get("ref_key")} if evidence_ref.get("ref_key") else None,
                focused_view="task_states",
            )
        )

    def _on_resource_selected(self, row: int, column: int) -> None:
        if row < 0 or row >= len(self._resource_rows):
            return
        item = self._resource_rows[row]
        selection = {"resource_label": item["label"]}
        if item.get("resource_id") is not None:
            selection["resource_id"] = item["resource_id"]
        if item.get("task_id") is not None:
            selection["task_id"] = item["task_id"]
        self.controller.viz_ApplySelection(selection)
        drilldown = None
        if selection.get("resource_id") is not None:
            drilldown = self.controller.viz_QueryResourceDrilldown(
                {
                    "dataset_id": self.state.active_dataset_id or self.controller.active_dataset_id,
                    "filter": {"resource_id": selection["resource_id"]},
                }
            )
        self.analysis_details.setPlainText(
            _format_resource_drilldown_text(
                item,
                selection,
                drilldown.data if drilldown and drilldown.ok else {"error": drilldown.message} if drilldown else None,
            )
        )

    def _on_alert_selected(self, row: int, column: int) -> None:
        if row < 0 or row >= len(self._alert_rows):
            return
        alert = self._alert_rows[row]
        evidence_refs = alert.get("evidence_refs") or []
        self._jump_to(
            EvidenceJumpRequest(
                time_window=tuple(alert["time_window"]),
                selection=dict(alert.get("object_scope", {})),
                evidence_anchor=evidence_refs[0] if evidence_refs else None,
                focused_view="alerts",
            )
        )

    def _on_compare_row_selected(self, row: int, column: int) -> None:
        if row < 0 or row >= len(self._compare_rows):
            return
        compare_row = self._compare_rows[row]
        detail_target = compare_row.get("detail_target")
        detail = self.compare_service.cmp_QueryDiffDetail(detail_target)
        self._show_compare_detail(
            compare_row,
            detail.data if detail.ok else None,
            error=None if detail.ok else detail.message,
            row_index=row,
        )

    def _jump_to_compare_target(self, jump_target: dict[str, Any] | None, *, missing_message: str) -> None:
        if not jump_target:
            self._show_error(missing_message)
            return
        time_window = jump_target.get("time_window")
        if not time_window:
            self._show_error(missing_message)
            return
        self._queue_compare_detail_restore()
        dataset_id = jump_target.get("dataset_id")
        if dataset_id and self.controller.repository.has(dataset_id):
            self.state.active_dataset_id = dataset_id
            self.controller.active_dataset_id = dataset_id
        context_delta = {
            "time_window": (float(time_window[0]), float(time_window[1])),
            "selection": dict(jump_target.get("selection") or {}),
            "evidence_anchor": jump_target.get("evidence_anchor"),
            "focused_view": jump_target.get("focused_view"),
        }
        dataset_role = jump_target.get("dataset_role")
        if dataset_role:
            context_delta["dataset_role"] = dataset_role
        self.controller.viz_SetContext(context_delta)
        self._set_active_tab("analysis")

    def jump_selected_compare_detail(self) -> None:
        if not self._compare_selected_detail:
            self._show_error("请先选择一个差异项。")
            return
        self._jump_to_compare_target(
            _compare_main_jump_target(self._compare_selected_detail),
            missing_message="当前差异项缺少可跳转证据。",
        )

    def jump_selected_compare_peer_detail(self) -> None:
        if not self._compare_selected_detail:
            self._show_error("请先选择一个差异项。")
            return
        self._jump_to_compare_target(
            _compare_peer_jump_target(self._compare_selected_detail),
            missing_message="当前差异项缺少对侧可跳转证据。",
        )

    def step_replay(self, step: int) -> None:
        if step < 0:
            state = self.replay_service.replay_StepBackward(abs(step))
        else:
            state = self.replay_service.replay_StepForward(step)
        if not state.ok:
            self._show_error(f"回放步进失败: {state.message}")
            return
        runtime_payload = self.controller.context_store.get().playback_runtime
        self.footer_label.setText(
            "回放位置 "
            f"index={_playback_step_index(state.data, runtime_payload)} "
            f"ts={_playback_timestamp(state.data, runtime_payload):.1f} "
            f"status={state.data.status}"
        )
        self.refresh_workspace()

    def play_replay(self) -> None:
        try:
            rate = max(float(self.replay_rate_input.text().strip() or "1.0"), 0.25)
        except ValueError:
            self._show_error("回放倍率必须是数字。")
            return
        state = self.replay_service.replay_Play(rate)
        if not state.ok:
            self._show_error(f"启动回放失败: {state.message}")
            return
        interval_ms = max(80, int(480 / rate))
        self.replay_timer.start(interval_ms)
        self.footer_label.setText(f"回放开始，倍率 {rate:.2f}x。")

    def pause_replay(self) -> None:
        self.replay_timer.stop()
        state = self.replay_service.replay_Pause()
        if state.ok:
            self.footer_label.setText(
                f"回放暂停在 index={_playback_step_index(state.data, self.controller.context_store.get().playback_runtime)}。"
            )
        self.refresh_workspace()

    def _advance_replay(self) -> None:
        before = self.replay_service.replay_GetState()
        self.replay_service.replay_StepForward(1)
        after = self.replay_service.replay_GetState()
        if not before.ok or not after.ok:
            self.replay_timer.stop()
            return
        before_index = _playback_step_index(before.data, self.controller.context_store.get().playback_runtime)
        after_index = _playback_step_index(after.data, self.controller.context_store.get().playback_runtime)
        if after.data.status == "ended" or after_index == before_index:
            self.pause_replay()
            return
        self.refresh_workspace()

    def _poll_export_job(self) -> None:
        job_id = self._active_export_job_id
        if job_id is None:
            self.export_timer.stop()
            return
        snapshot = self.controller.jobs.status(job_id)
        if not snapshot.ok:
            self.export_timer.stop()
            self._active_export_job_id = None
            self._show_error(f"导出任务状态查询失败: {snapshot.message}")
            return
        mode = self._active_export_mode or "full"
        status = snapshot.data["status"]
        if status in {"queued", "running"}:
            payload = snapshot.data.get("payload") or {}
            self._set_export_contract_summary(
                closure_mode="pending" if mode == "evidence" else None,
                halt_reason=payload.get("halt_reason"),
                patent_job_state=payload.get("patent_job_state"),
            )
            self.export_status_label.setText(f"导出状态: {mode} 导出中")
            return
        self.export_timer.stop()
        written = self.controller.jobs.result(job_id)
        output_dir = self._active_export_output_dir or ""
        self._active_export_job_id = None
        self._active_export_mode = None
        self._active_export_output_dir = None
        if not written.ok:
            payload = snapshot.data.get("payload") or {}
            self._show_error(f"导出失败: {written.message}")
            self._export_cache = {
                "mode": mode,
                "job": {"job_id": job_id},
                "status": status,
                "error": written.message,
                "patent_job_state": payload.get("patent_job_state"),
                "error_code": payload.get("error_code"),
            }
            self._set_export_contract_summary(
                closure_mode="failed" if mode == "evidence" else None,
                halt_reason=payload.get("halt_reason") or payload.get("error_code"),
                patent_job_state=payload.get("patent_job_state"),
            )
            self.export_result_text.setPlainText(_json_text(self._export_cache))
            return
        self.state.last_export_path = output_dir
        self.repro_package_input.setText(output_dir)
        result_payload: dict[str, Any]
        if mode == "evidence":
            proof = self.repro_service.repro_QueryProof(output_dir)
            proof_digest = dict(proof.data.get("proof_digest") or {}) if proof.ok else {}
            result_payload = {
                "job_id": job_id,
                "package_path": written.data.get("package_path") or output_dir,
                "closure_mode": written.data.get("closure_mode"),
                "proof_digest": proof_digest,
            }
            self._export_cache = {
                "mode": mode,
                "job": {"job_id": job_id},
                "written": written.data,
                "status": status,
                "context": self.controller.context_store.get().persisted_dict(),
                "result": result_payload,
            }
            self._update_proof_digest_panel(proof_digest)
            self._set_export_contract_summary(
                closure_mode=result_payload.get("closure_mode"),
                halt_reason=proof_digest.get("frontier_halt_reason"),
                patent_job_state=(snapshot.data.get("payload") or {}).get("patent_job_state"),
            )
            self.open_repro_package(output_dir)
            self.export_result_text.setPlainText(_json_text(result_payload))
        else:
            self._export_cache = {
                "mode": mode,
                "job": {"job_id": job_id},
                "written": written.data,
                "status": status,
                "context": self.controller.context_store.get().persisted_dict(),
            }
            self.export_result_text.setPlainText(_json_text(self._export_cache))
        self.footer_label.setText(f"{mode} 导出完成: {output_dir}")
        self.refresh_workspace()
        self.export_status_label.setText(f"导出状态: {mode} 导出完成")

    def run_export(self, mode: str) -> None:
        dataset_id = self.state.active_dataset_id or self.controller.active_dataset_id
        if dataset_id is None:
            self._show_error("导出前请先加载数据集。")
            return
        requested = self.export_output_input.text().strip()
        default_full = str(self._temp_root / "exports" / "full")
        if not requested:
            requested = str(self._temp_root / "exports" / mode)
        elif mode in {"clipped", "evidence"} and requested == default_full:
            requested = str(self._temp_root / "exports" / mode)
            self.export_output_input.setText(requested)
        output_dir = Path(requested)
        output_dir.mkdir(parents=True, exist_ok=True)
        if mode == "clipped":
            job = self.export_service.export_Clipped(
                {
                    "dataset_id": dataset_id,
                    **self.controller.context_store.get().persisted_dict(),
                }
            )
            submit_task = self.export_service.export_WritePackage
        elif mode == "evidence":
            payload = self._build_evidence_export_request(dataset_id)
            if payload is None:
                return
            job = self.export_service.export_Evidence(payload)
            submit_task = self.export_service.export_WriteEvidencePackage
        else:
            job = self.export_service.export_Full({"dataset_id": dataset_id})
            submit_task = self.export_service.export_WritePackage
        if not job.ok:
            self._show_error(f"导出任务创建失败: {job.message}")
            return
        submitted = self.controller.jobs.submit(
            job.data["job_id"],
            submit_task,
            job.data["job_id"],
            str(output_dir),
        )
        if not submitted.ok:
            self._show_error(f"导出提交失败: {submitted.message}")
            return
        self._active_export_job_id = job.data["job_id"]
        self._active_export_mode = mode
        self._active_export_output_dir = str(output_dir)
        self._export_cache = {
            "mode": mode,
            "job": job.data,
            "status": "queued",
            "output_dir": str(output_dir),
            "context": self.controller.context_store.get().persisted_dict(),
        }
        self.export_result_text.setPlainText(_json_text(self._export_cache))
        self._set_export_contract_summary(
            closure_mode="pending" if mode == "evidence" else None,
            patent_job_state="JOB-queued" if mode == "evidence" else None,
        )
        self.export_status_label.setText(f"导出状态: {mode} 导出中")
        self.footer_label.setText(f"{mode} 导出已提交: {output_dir}")
        self.export_timer.start(50)
        self.refresh_workspace()

    def _maybe_pick_directory(self) -> str | None:
        if hasattr(QFileDialog, "getExistingDirectory"):
            return QFileDialog.getExistingDirectory(self, "选择复现包目录") or None
        return None

    def _show_error(self, message: str) -> None:
        self.footer_label.setText(message)
        if hasattr(QMessageBox, "critical"):
            try:
                QMessageBox.critical(self, "RTTrace", message)
            except Exception:
                pass


def launch_gui(
    source: str | None = None,
    baseline: str | None = None,
    candidate: str | None = None,
    package: str | None = None,
    tab: str = "analysis",
) -> RuntimeProbeWindow:
    if not PYSIDE_AVAILABLE:
        raise RuntimeError("PySide6 is not installed; run tool/bootstrap_desktop_env.sh first")
    if not PG_AVAILABLE:
        raise RuntimeError("pyqtgraph is not installed; run tool/bootstrap_desktop_env.sh first")
    window = RuntimeProbeWindow(
        source=source,
        baseline=baseline,
        candidate=candidate,
        package=package,
        default_tab=tab,
    )
    window.show()
    return window


def main(argv: list[str] | None = None) -> int:
    argp = argparse.ArgumentParser(prog="rttrace-gui")
    argp.add_argument("--input")
    argp.add_argument("--baseline")
    argp.add_argument("--candidate")
    argp.add_argument("--package")
    argp.add_argument("--tab", default="analysis", choices=["analysis", "compare", "export", "diagnostic"])
    argp.add_argument("--offscreen", action="store_true")
    args = argp.parse_args(argv)

    if args.offscreen:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    try:
        app = ensure_qapplication(argv or [])
        window = launch_gui(
            source=args.input,
            baseline=args.baseline,
            candidate=args.candidate,
            package=args.package,
            tab=args.tab,
        )
    except RuntimeError as exc:
        print(str(exc))
        return 1
    if args.offscreen:
        QTimer.singleShot(0, app.quit)  # type: ignore[attr-defined]
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
