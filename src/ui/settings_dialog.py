from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QCheckBox,
    QPushButton, QLineEdit, QFileDialog, QGroupBox, QDialogButtonBox,
    QDoubleSpinBox, QTabWidget, QWidget,
)
from PyQt6.QtCore import Qt
from src.ui.widgets import ClickSlider

from src.core.presets import ASPECT_PRESETS
from src.core.auto_presets import get_quality_presets as _get_auto_presets
from src.ui.themes import THEMES, DEFAULT_THEME
from src.ui.keybind_editor import KeybindEditor

QUALITY_PRESETS = {
    "Lossless (CRF 0)": 0,
    "High (CRF 17)": 17,
    "Medium (CRF 23)": 23,
    "Low (CRF 28)": 28,
}

# Auto-optimized presets from FFmpeg AutoResearch (empty if optimizer hasn't run)
AUTO_PRESETS: dict[str, dict] = _get_auto_presets()

AUDIO_MODES = {
    "Copy Original": "copy",
    "Re-encode AAC 320k": "reencode",
    "Mute (no audio)": "mute",
}


class SettingsDialog(QDialog):
    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(580)
        self.setMinimumHeight(620)
        self.settings = dict(settings)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        tabs = QTabWidget()
        tabs.addTab(self._build_general_tab(), "General")

        self.keybind_editor = KeybindEditor(self.settings.get("keybinds"))
        tabs.addTab(self.keybind_editor, "Shortcuts")

        layout.addWidget(tabs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _build_general_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # ── Video ─────────────────────────────────────────────
        video_group = QGroupBox("Video")
        vg = QVBoxLayout(video_group)

        row = QHBoxLayout()
        row.addWidget(QLabel("Codec:"))
        self.cmb_codec = QComboBox()
        self.cmb_codec.addItems(["H.264", "H.265"])
        self.cmb_codec.setCurrentText(
            "H.264" if self.settings.get("codec") == "h264" else "H.265"
        )
        row.addWidget(self.cmb_codec, stretch=1)
        vg.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Quality:"))
        self.cmb_quality = QComboBox()
        # Built-in presets
        self.cmb_quality.addItems(QUALITY_PRESETS.keys())
        # Auto-optimized presets (from FFmpeg AutoResearch, if available)
        if AUTO_PRESETS:
            self.cmb_quality.insertSeparator(self.cmb_quality.count())
            self.cmb_quality.addItems(AUTO_PRESETS.keys())
        current_crf = self.settings.get("crf", 17)
        current_auto = self.settings.get("auto_preset_name", "")
        if current_auto and current_auto in AUTO_PRESETS:
            self.cmb_quality.setCurrentText(current_auto)
        else:
            for name, crf in QUALITY_PRESETS.items():
                if crf == current_crf:
                    self.cmb_quality.setCurrentText(name)
                    break
        self.cmb_quality.currentTextChanged.connect(self._on_quality_changed)
        row.addWidget(self.cmb_quality, stretch=1)
        vg.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Audio:"))
        self.cmb_audio = QComboBox()
        self.cmb_audio.addItems(AUDIO_MODES.keys())
        current_audio = self.settings.get("audio_mode", "copy")
        for name, val in AUDIO_MODES.items():
            if val == current_audio:
                self.cmb_audio.setCurrentText(name)
                break
        row.addWidget(self.cmb_audio, stretch=1)
        vg.addLayout(row)

        layout.addWidget(video_group)

        # ── Defaults ──────────────────────────────────────────
        defaults_group = QGroupBox("Defaults (applied to new videos)")
        dg = QVBoxLayout(defaults_group)

        row = QHBoxLayout()
        row.addWidget(QLabel("Aspect Ratio:"))
        self.cmb_aspect = QComboBox()
        self.cmb_aspect.addItems(ASPECT_PRESETS.keys())
        self.cmb_aspect.setCurrentText(self.settings.get("aspect_ratio", "Free"))
        row.addWidget(self.cmb_aspect, stretch=1)
        dg.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Crop Mode:"))
        self.cmb_crop_mode = QComboBox()
        self.cmb_crop_mode.addItems(["Crop", "Stretch"])
        self.cmb_crop_mode.setCurrentText(
            "Stretch" if self.settings.get("crop_mode") == "stretch" else "Crop"
        )
        row.addWidget(self.cmb_crop_mode, stretch=1)
        dg.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Speed:"))
        self.spn_speed = QDoubleSpinBox()
        self.spn_speed.setRange(0.1, 10.0)
        self.spn_speed.setSingleStep(0.25)
        self.spn_speed.setDecimals(2)
        self.spn_speed.setSuffix("x")
        self.spn_speed.setValue(self.settings.get("default_speed", 1.0))
        row.addWidget(self.spn_speed, stretch=1)
        dg.addLayout(row)

        layout.addWidget(defaults_group)

        # ── Output ────────────────────────────────────────────
        output_group = QGroupBox("Output")
        og = QVBoxLayout(output_group)

        row = QHBoxLayout()
        row.addWidget(QLabel("Save to:"))
        self.txt_output = QLineEdit(self.settings.get("output_dir", ""))
        self.txt_output.setPlaceholderText("Same folder as source video")
        row.addWidget(self.txt_output)
        self.btn_browse = QPushButton("Browse...")
        self.btn_browse.clicked.connect(self._browse)
        row.addWidget(self.btn_browse)
        og.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("File suffix:"))
        self.txt_suffix = QLineEdit(self.settings.get("output_suffix", "_edited"))
        self.txt_suffix.setFixedWidth(120)
        self.txt_suffix.setPlaceholderText("_edited")
        row.addWidget(self.txt_suffix)
        row.addStretch()
        og.addLayout(row)

        layout.addWidget(output_group)

        # ── Archive Originals ─────────────────────────────────
        archive_group = QGroupBox("Archive Originals")
        ag = QVBoxLayout(archive_group)

        self.chk_archive = QCheckBox("Move original clip to archive folder after export")
        self.chk_archive.setChecked(self.settings.get("move_originals_to_archive", False))
        ag.addWidget(self.chk_archive)

        row = QHBoxLayout()
        row.addWidget(QLabel("Archive to:"))
        self.txt_archive_dir = QLineEdit(self.settings.get("untrimmed_archive_dir", ""))
        self.txt_archive_dir.setPlaceholderText("e.g. D:\\Clips\\Untrimmed Clips")
        row.addWidget(self.txt_archive_dir)
        self.btn_browse_archive = QPushButton("Browse...")
        self.btn_browse_archive.clicked.connect(self._browse_archive)
        row.addWidget(self.btn_browse_archive)
        ag.addLayout(row)

        hint = QLabel(
            "Year/month folder structure (e.g. 2026\\04 - April) is detected "
            "automatically and preserved."
        )
        hint.setStyleSheet("color: gray; font-size: 9pt;")
        hint.setWordWrap(True)
        ag.addWidget(hint)

        layout.addWidget(archive_group)

        # ── Workflow ──────────────────────────────────────────
        workflow_group = QGroupBox("Workflow")
        wg = QVBoxLayout(workflow_group)

        self.chk_advance = QCheckBox("Auto-advance to next video after export")
        self.chk_advance.setChecked(self.settings.get("auto_advance", False))
        wg.addWidget(self.chk_advance)

        self.chk_on_top = QCheckBox("Keep window always on top")
        self.chk_on_top.setChecked(self.settings.get("stay_on_top", False))
        wg.addWidget(self.chk_on_top)

        norm_row = QHBoxLayout()
        self.chk_normalize = QCheckBox("Normalize audio on export")
        self.chk_normalize.setChecked(self.settings.get("normalize_audio", False))
        self.chk_normalize.setToolTip(
            "Applies loudness normalization to all exports globally. "
            "Can also be toggled per-clip in the Adjustments panel."
        )
        norm_row.addWidget(self.chk_normalize)
        self.spn_norm_lufs = QDoubleSpinBox()
        self.spn_norm_lufs.setRange(-50.0, 0.0)
        self.spn_norm_lufs.setValue(self.settings.get("normalize_lufs", -14.0))
        self.spn_norm_lufs.setSingleStep(1.0)
        self.spn_norm_lufs.setDecimals(1)
        self.spn_norm_lufs.setSuffix(" LUFS")
        self.spn_norm_lufs.setFixedWidth(100)
        norm_row.addWidget(self.spn_norm_lufs)
        wg.addLayout(norm_row)

        layout.addWidget(workflow_group)

        # ── Appearance ────────────────────────────────────────
        appearance_group = QGroupBox("Appearance")
        apg = QHBoxLayout(appearance_group)
        apg.addWidget(QLabel("Theme:"))
        self.cmb_theme = QComboBox()
        self.cmb_theme.addItems(THEMES.keys())
        current_theme = self.settings.get("theme", DEFAULT_THEME)
        if current_theme in THEMES:
            self.cmb_theme.setCurrentText(current_theme)
        apg.addWidget(self.cmb_theme, stretch=1)
        layout.addWidget(appearance_group)

        # ── UI Scale ─────────────────────────────────────────
        scale_group = QGroupBox("UI Scale")
        sg = QHBoxLayout(scale_group)
        sg.addWidget(QLabel("Compact"))
        self.sld_scale = ClickSlider(Qt.Orientation.Horizontal)
        self.sld_scale.setRange(70, 120)  # 70% to 120%
        self.sld_scale.setValue(int(self.settings.get("ui_scale", 90)))
        self.sld_scale.setTickInterval(10)
        from PyQt6.QtWidgets import QSlider
        self.sld_scale.setTickPosition(QSlider.TickPosition.TicksBelow)
        sg.addWidget(self.sld_scale, stretch=1)
        sg.addWidget(QLabel("Large"))
        self.lbl_scale = QLabel(f"{self.sld_scale.value()}%")
        self.lbl_scale.setFixedWidth(36)
        self.sld_scale.valueChanged.connect(lambda v: self.lbl_scale.setText(f"{v}%"))
        sg.addWidget(self.lbl_scale)
        layout.addWidget(scale_group)

        layout.addStretch()
        return tab

    def _on_quality_changed(self, text: str):
        """When an auto-preset is selected, override the codec dropdown to match."""
        if text in AUTO_PRESETS:
            preset = AUTO_PRESETS[text]
            codec = preset.get("codec", "h264")
            self.cmb_codec.setCurrentText("H.264" if codec == "h264" else "H.265")
            self.cmb_codec.setEnabled(False)
            self.cmb_codec.setToolTip("Codec set by auto-optimized preset")
        else:
            self.cmb_codec.setEnabled(True)
            self.cmb_codec.setToolTip("")

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose Output Folder")
        if folder:
            self.txt_output.setText(folder)

    def _browse_archive(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose Archive Folder")
        if folder:
            self.txt_archive_dir.setText(folder)

    def get_settings(self) -> dict:
        codec = "h264" if self.cmb_codec.currentText() == "H.264" else "h265"
        quality_name = self.cmb_quality.currentText()
        auto_preset = AUTO_PRESETS.get(quality_name)
        if auto_preset:
            crf = auto_preset.get("crf", 23)
            codec = auto_preset.get("codec", codec)
        else:
            crf = QUALITY_PRESETS.get(quality_name, 17)
        audio_mode = AUDIO_MODES[self.cmb_audio.currentText()]
        output_dir = self.txt_output.text().strip()
        suffix = self.txt_suffix.text().strip() or "_edited"
        archive_dir = self.txt_archive_dir.text().strip()
        return {
            "codec": codec,
            "crf": crf,
            "audio_mode": audio_mode,
            "output_dir": output_dir,
            "output_suffix": suffix,
            "aspect_ratio": self.cmb_aspect.currentText(),
            "crop_mode": "stretch" if self.cmb_crop_mode.currentText() == "Stretch" else "crop",
            "default_speed": self.spn_speed.value(),
            "auto_advance": self.chk_advance.isChecked(),
            "move_originals_to_archive": self.chk_archive.isChecked(),
            "untrimmed_archive_dir": archive_dir,
            "theme": self.cmb_theme.currentText(),
            "stay_on_top": self.chk_on_top.isChecked(),
            "normalize_audio": self.chk_normalize.isChecked(),
            "normalize_lufs": self.spn_norm_lufs.value(),
            "ui_scale": self.sld_scale.value(),
            "keybinds": self.keybind_editor.get_bindings(),
            "auto_preset_name": quality_name if auto_preset else "",
            "auto_preset": auto_preset,
        }
