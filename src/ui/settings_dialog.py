from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QCheckBox,
    QPushButton, QLineEdit, QFileDialog, QGroupBox, QDialogButtonBox,
    QDoubleSpinBox, QSpinBox, QTabWidget, QWidget,
)
from PyQt6.QtCore import Qt
from src.ui.widgets import ClickSlider

from src.core.presets import ASPECT_PRESETS
from src.core.auto_presets import get_quality_presets as _get_auto_presets
from src.core.keybinds import ACTION_DEFS, PLAYER_ACTION_DEFS
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
        tabs.addTab(self._build_player_tab(), "Player")

        self.editor_keybind_editor = KeybindEditor(
            ACTION_DEFS, self.settings.get("editor_keybinds"))
        tabs.addTab(self.editor_keybind_editor, "Editor Shortcuts")

        self.player_keybind_editor = KeybindEditor(
            PLAYER_ACTION_DEFS, self.settings.get("player_keybinds"))
        tabs.addTab(self.player_keybind_editor, "Player Shortcuts")

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
        self.cmb_quality.currentTextChanged.connect(self._on_quality_changed)
        row.addWidget(self.cmb_quality, stretch=1)
        vg.addLayout(row)

        # Experimental presets toggle (only visible when auto-presets exist)
        if AUTO_PRESETS:
            self.chk_experimental = QCheckBox(
                "Show experimental presets (AutoResearch)"
            )
            self.chk_experimental.setToolTip(
                "Reveal auto-optimized presets from FFmpeg parameter sweep.\n"
                "These are still being refined."
            )
            self.chk_experimental.setChecked(
                self.settings.get("experimental_presets", False)
            )
            self.chk_experimental.toggled.connect(self._rebuild_quality_combo)
            vg.addWidget(self.chk_experimental)
        else:
            self.chk_experimental = None

        self._rebuild_quality_combo()

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

    def _build_player_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # ── Click / Mouse Behavior ────────────────────────────
        mouse_group = QGroupBox("Mouse Behavior")
        mg = QVBoxLayout(mouse_group)

        row = QHBoxLayout()
        row.addWidget(QLabel("Single click on video:"))
        self.cmb_player_click = QComboBox()
        self.cmb_player_click.addItems(["Play/Pause", "Disabled"])
        current = self.settings.get("player_click", "play_pause")
        self.cmb_player_click.setCurrentText(
            "Play/Pause" if current == "play_pause" else "Disabled")
        row.addWidget(self.cmb_player_click, stretch=1)
        mg.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Double click on video:"))
        self.cmb_player_dblclick = QComboBox()
        self.cmb_player_dblclick.addItems(["Fullscreen", "Play/Pause", "Disabled"])
        current = self.settings.get("player_double_click", "fullscreen")
        idx = {"fullscreen": 0, "play_pause": 1, "disabled": 2}.get(current, 0)
        self.cmb_player_dblclick.setCurrentIndex(idx)
        row.addWidget(self.cmb_player_dblclick, stretch=1)
        mg.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Mouse wheel on video:"))
        self.cmb_player_wheel = QComboBox()
        self.cmb_player_wheel.addItems(["Seek", "Volume", "Disabled"])
        current = self.settings.get("player_wheel", "seek")
        idx = {"seek": 0, "volume": 1, "disabled": 2}.get(current, 0)
        self.cmb_player_wheel.setCurrentIndex(idx)
        row.addWidget(self.cmb_player_wheel, stretch=1)
        mg.addLayout(row)

        layout.addWidget(mouse_group)

        # ── Seeking ───────────────────────────────────────────
        seek_group = QGroupBox("Seeking")
        sg = QVBoxLayout(seek_group)

        row = QHBoxLayout()
        row.addWidget(QLabel("Small seek step:"))
        self.spn_seek_step = QSpinBox()
        self.spn_seek_step.setRange(1, 60)
        self.spn_seek_step.setValue(self.settings.get("player_seek_step", 5))
        self.spn_seek_step.setSuffix(" sec")
        row.addWidget(self.spn_seek_step)
        row.addSpacing(20)
        row.addWidget(QLabel("Large seek step:"))
        self.spn_seek_step_large = QSpinBox()
        self.spn_seek_step_large.setRange(1, 120)
        self.spn_seek_step_large.setValue(
            self.settings.get("player_seek_step_large", 30))
        self.spn_seek_step_large.setSuffix(" sec")
        row.addWidget(self.spn_seek_step_large)
        row.addStretch()
        sg.addLayout(row)

        layout.addWidget(seek_group)

        # ── Playback ─────────────────────────────────────────
        play_group = QGroupBox("Playback")
        pg = QVBoxLayout(play_group)

        row = QHBoxLayout()
        row.addWidget(QLabel("Volume:"))
        self.spn_player_volume = QSpinBox()
        self.spn_player_volume.setRange(0, 100)
        self.spn_player_volume.setValue(
            self.settings.get("player_volume", 100))
        self.spn_player_volume.setSuffix("%")
        self.spn_player_volume.setToolTip("Player volume (also saved when adjusted during playback)")
        row.addWidget(self.spn_player_volume)
        row.addStretch()
        pg.addLayout(row)

        self.chk_player_advance = QCheckBox("Auto-advance to next file")
        self.chk_player_advance.setChecked(
            self.settings.get("player_auto_advance", True))
        pg.addWidget(self.chk_player_advance)

        self.chk_remember_pos = QCheckBox("Remember playback position per file")
        self.chk_remember_pos.setChecked(
            self.settings.get("player_remember_positions", False))
        pg.addWidget(self.chk_remember_pos)

        layout.addWidget(play_group)

        # ── Fullscreen ────────────────────────────────────────
        fs_group = QGroupBox("Fullscreen")
        fg = QVBoxLayout(fs_group)

        row = QHBoxLayout()
        self.chk_cursor_hide = QCheckBox("Auto-hide cursor after")
        self.chk_cursor_hide.setChecked(
            self.settings.get("player_cursor_hide", True))
        row.addWidget(self.chk_cursor_hide)
        self.spn_cursor_delay = QSpinBox()
        self.spn_cursor_delay.setRange(500, 10000)
        self.spn_cursor_delay.setSingleStep(500)
        self.spn_cursor_delay.setValue(
            self.settings.get("player_cursor_hide_delay", 3000))
        self.spn_cursor_delay.setSuffix(" ms")
        row.addWidget(self.spn_cursor_delay)
        row.addStretch()
        fg.addLayout(row)

        row = QHBoxLayout()
        self.chk_controls_autohide = QCheckBox("Auto-hide controls after")
        self.chk_controls_autohide.setChecked(
            self.settings.get("player_controls_autohide", True))
        row.addWidget(self.chk_controls_autohide)
        self.spn_controls_delay = QSpinBox()
        self.spn_controls_delay.setRange(500, 10000)
        self.spn_controls_delay.setSingleStep(500)
        self.spn_controls_delay.setValue(
            self.settings.get("player_controls_autohide_delay", 3000))
        self.spn_controls_delay.setSuffix(" ms")
        row.addWidget(self.spn_controls_delay)
        row.addStretch()
        fg.addLayout(row)

        layout.addWidget(fs_group)

        # ── OSD ───────────────────────────────────────────────
        osd_group = QGroupBox("On-Screen Display")
        og = QHBoxLayout(osd_group)
        self.chk_osd = QCheckBox("Show OSD notifications")
        self.chk_osd.setChecked(self.settings.get("player_osd", True))
        og.addWidget(self.chk_osd)
        og.addWidget(QLabel("Duration:"))
        self.spn_osd_dur = QSpinBox()
        self.spn_osd_dur.setRange(500, 5000)
        self.spn_osd_dur.setSingleStep(250)
        self.spn_osd_dur.setValue(self.settings.get("player_osd_duration", 1500))
        self.spn_osd_dur.setSuffix(" ms")
        og.addWidget(self.spn_osd_dur)
        og.addStretch()
        layout.addWidget(osd_group)

        # ── Start Directory ───────────────────────────────────
        dir_group = QGroupBox("Start Directory")
        dg = QVBoxLayout(dir_group)

        row = QHBoxLayout()
        row.addWidget(QLabel("On startup:"))
        self.cmb_start_dir = QComboBox()
        self.cmb_start_dir.addItems(["Last opened", "Specific path", "Home"])
        mode = self.settings.get("player_start_dir_mode", "last")
        idx = {"last": 0, "specific": 1, "home": 2}.get(mode, 0)
        self.cmb_start_dir.setCurrentIndex(idx)
        row.addWidget(self.cmb_start_dir, stretch=1)
        dg.addLayout(row)

        row = QHBoxLayout()
        self.txt_start_dir = QLineEdit(
            self.settings.get("player_start_dir_path", ""))
        self.txt_start_dir.setPlaceholderText("Path for 'Specific path' mode")
        row.addWidget(self.txt_start_dir)
        btn = QPushButton("Browse...")
        btn.clicked.connect(self._browse_start_dir)
        row.addWidget(btn)
        dg.addLayout(row)

        layout.addWidget(dir_group)

        # ── File Extensions ───────────────────────────────────
        ext_group = QGroupBox("Video File Extensions")
        eg = QHBoxLayout(ext_group)
        self.txt_extensions = QLineEdit(
            self.settings.get("player_extensions",
                              ".mp4,.mkv,.avi,.mov,.webm,.flv,.wmv"))
        self.txt_extensions.setToolTip(
            "Comma-separated list of extensions (include the dot)")
        eg.addWidget(self.txt_extensions)
        layout.addWidget(ext_group)

        layout.addStretch()
        return tab

    def _browse_start_dir(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose Start Directory")
        if folder:
            self.txt_start_dir.setText(folder)
            self.cmb_start_dir.setCurrentIndex(1)  # Switch to "Specific path"

    def _rebuild_quality_combo(self):
        """Populate quality dropdown, optionally including experimental presets."""
        prev = self.cmb_quality.currentText()
        self.cmb_quality.blockSignals(True)
        self.cmb_quality.clear()

        self.cmb_quality.addItems(QUALITY_PRESETS.keys())

        show_exp = (
            self.chk_experimental is not None
            and self.chk_experimental.isChecked()
        )
        if show_exp and AUTO_PRESETS:
            self.cmb_quality.insertSeparator(self.cmb_quality.count())
            self.cmb_quality.addItems(AUTO_PRESETS.keys())

        # Restore selection
        current_auto = self.settings.get("auto_preset_name", "")
        if show_exp and current_auto and current_auto in AUTO_PRESETS:
            self.cmb_quality.setCurrentText(current_auto)
        elif prev in QUALITY_PRESETS:
            self.cmb_quality.setCurrentText(prev)
        else:
            # Pick the exact-CRF preset, else the nearest one — never fall
            # through and let the combo silently keep index 0, which would
            # change the effective export quality on reopen.
            current_crf = self.settings.get("crf", 17)
            best = next((n for n, c in QUALITY_PRESETS.items() if c == current_crf), None)
            if best is None:
                best = min(QUALITY_PRESETS,
                           key=lambda n: abs(QUALITY_PRESETS[n] - current_crf))
            self.cmb_quality.setCurrentText(best)

        self.cmb_quality.blockSignals(False)
        self._on_quality_changed(self.cmb_quality.currentText())

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

        # Player click/wheel behavior
        click_map = {"Play/Pause": "play_pause", "Disabled": "disabled"}
        dblclick_map = {"Fullscreen": "fullscreen", "Play/Pause": "play_pause",
                        "Disabled": "disabled"}
        wheel_map = {"Seek": "seek", "Volume": "volume", "Disabled": "disabled"}
        start_map = {0: "last", 1: "specific", 2: "home"}

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
            "editor_keybinds": self.editor_keybind_editor.get_bindings(),
            "player_keybinds": self.player_keybind_editor.get_bindings(),
            "auto_preset_name": quality_name if auto_preset else "",
            # Shallow-copy so the persisted settings never share identity with
            # the module-global AUTO_PRESETS table (a later in-place mutation
            # of the saved dict would otherwise corrupt the preset table).
            "auto_preset": dict(auto_preset) if auto_preset else None,
            "experimental_presets": (
                self.chk_experimental.isChecked()
                if self.chk_experimental is not None
                else False
            ),
            # Player settings
            "player_click": click_map.get(
                self.cmb_player_click.currentText(), "play_pause"),
            "player_double_click": dblclick_map.get(
                self.cmb_player_dblclick.currentText(), "fullscreen"),
            "player_wheel": wheel_map.get(
                self.cmb_player_wheel.currentText(), "seek"),
            "player_seek_step": self.spn_seek_step.value(),
            "player_seek_step_large": self.spn_seek_step_large.value(),
            "player_volume": self.spn_player_volume.value(),
            "player_auto_advance": self.chk_player_advance.isChecked(),
            "player_remember_positions": self.chk_remember_pos.isChecked(),
            "player_cursor_hide": self.chk_cursor_hide.isChecked(),
            "player_cursor_hide_delay": self.spn_cursor_delay.value(),
            "player_controls_autohide": self.chk_controls_autohide.isChecked(),
            "player_controls_autohide_delay": self.spn_controls_delay.value(),
            "player_osd": self.chk_osd.isChecked(),
            "player_osd_duration": self.spn_osd_dur.value(),
            "player_start_dir_mode": start_map.get(
                self.cmb_start_dir.currentIndex(), "last"),
            "player_start_dir_path": self.txt_start_dir.text().strip(),
            "player_extensions": self.txt_extensions.text().strip(),
        }
