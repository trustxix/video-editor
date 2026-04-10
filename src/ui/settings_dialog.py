from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QCheckBox,
    QPushButton, QLineEdit, QFileDialog, QGroupBox, QDialogButtonBox,
    QDoubleSpinBox
)

from src.core.presets import ASPECT_PRESETS
from src.ui.themes import THEMES, DEFAULT_THEME

QUALITY_PRESETS = {
    "Lossless (CRF 0)": 0,
    "High (CRF 17)": 17,
    "Medium (CRF 23)": 23,
    "Low (CRF 28)": 28,
}

AUDIO_MODES = {
    "Copy Original": "copy",
    "Re-encode AAC 320k": "reencode",
    "Mute (no audio)": "mute",
}


class SettingsDialog(QDialog):
    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(420)
        self.settings = dict(settings)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

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
        self.cmb_quality.addItems(QUALITY_PRESETS.keys())
        current_crf = self.settings.get("crf", 17)
        for name, crf in QUALITY_PRESETS.items():
            if crf == current_crf:
                self.cmb_quality.setCurrentText(name)
                break
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

        # ── Buttons ───────────────────────────────────────────
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

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
        crf = QUALITY_PRESETS[self.cmb_quality.currentText()]
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
        }
