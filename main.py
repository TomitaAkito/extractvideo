import sys
import os
import threading
from collections import deque
import cv2
import time
from pathlib import Path
import concurrent.futures
import subprocess
import shutil
import numpy as np

from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QPushButton, QLabel, QFileDialog, 
                               QSlider, QScrollArea, QGridLayout, QMessageBox,
                               QProgressDialog, QListWidget, QListWidgetItem,
                               QFrame, QSizePolicy, QDialog, QProgressBar)
from PySide6.QtCore import Qt, QThread, Signal, Slot, QUrl
from PySide6.QtGui import QImage, QPixmap, QDesktopServices

APP_STYLE = """
QWidget { background-color: #1e1f26; color: #e6e8ee; font-family: 'Segoe UI', 'Yu Gothic UI', 'Meiryo UI', sans-serif; font-size: 13px; }
QMainWindow, QScrollArea, QScrollArea > QWidget > QWidget { background-color: #1e1f26; }
QFrame#card { background-color: #272935; border: 1px solid #343748; border-radius: 10px; }
QFrame#card QLabel, QFrame#card QWidget { background-color: transparent; }
QLabel#sectionTitle { font-size: 13px; font-weight: bold; color: #8fa3ff; }
QLabel#pathLabel { color: #9aa0b4; }
QLabel#frameInfo { font-size: 15px; font-weight: bold; color: #ffffff; }
QLabel#videoTitle { font-size: 12px; color: #c9cde0; }
QLabel#videoView { background-color: #000000; border-radius: 6px; }
QListWidget { background-color: #20222c; border: 1px solid #343748; border-radius: 8px; padding: 4px; }
QListWidget::item { padding: 5px 8px; border-radius: 5px; }
QListWidget::item:selected { background-color: #4a5bd8; color: #ffffff; }
QListWidget::item:hover:!selected { background-color: #2f3242; }
QPushButton { background-color: #343850; border: 1px solid #454a66; border-radius: 8px; padding: 7px 14px; }
QPushButton:hover { background-color: #424765; }
QPushButton:pressed { background-color: #2a2e44; }
QPushButton:disabled { background-color: #262836; color: #6b7088; border-color: #30334a; }
QPushButton#primary { background-color: #4a5bd8; border-color: #5d6df0; color: #ffffff; font-weight: bold; }
QPushButton#primary:hover { background-color: #5b6cf0; }
QPushButton#accent { background-color: #b45309; border-color: #f59e0b; color: #ffffff; font-weight: bold; }
QPushButton#accent:hover { background-color: #d97706; }
QPushButton#success { background-color: #2f9e6a; border-color: #43c088; color: #ffffff; font-weight: bold; }
QPushButton#success:hover { background-color: #3bb57c; }
QSlider::groove:horizontal { height: 6px; background: #343850; border-radius: 3px; }
QSlider::sub-page:horizontal { background: #6d7dff; border-radius: 3px; }
QSlider::handle:horizontal { background: #ffffff; width: 16px; height: 16px; margin: -5px 0; border-radius: 8px; }
QScrollBar:vertical { background: #1e1f26; width: 12px; }
QScrollBar::handle:vertical { background: #454a66; border-radius: 5px; min-height: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar:horizontal { background: #1e1f26; height: 12px; }
QScrollBar::handle:horizontal { background: #454a66; border-radius: 5px; min-width: 30px; }
QProgressBar { background-color: #343850; border: none; border-radius: 6px; text-align: center; color: #ffffff; font-weight: bold; }
QProgressBar::chunk { background-color: #6d7dff; border-radius: 6px; }
QToolTip { background-color: #272935; color: #e6e8ee; border: 1px solid #454a66; }
"""

class ProxyProgressDialog(QDialog):
    """動画ごとの変換状況を一覧表示するダイアログ"""
    canceled = Signal()

    def __init__(self, video_paths, parent=None):
        super().__init__(parent)
        self.setWindowTitle("軽量キャッシュ作成")
        self.setModal(True)
        self.setMinimumWidth(560)
        self._finished = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        self.lbl_total = QLabel("準備中...")
        self.lbl_total.setObjectName("sectionTitle")
        lay.addWidget(self.lbl_total)
        self.bar_total = QProgressBar()
        self.bar_total.setRange(0, 100)
        lay.addWidget(self.bar_total)

        # 動画ごとの行 (多くても画面に収まるようスクロール可能に)
        rows_widget = QWidget()
        rows_lay = QVBoxLayout(rows_widget)
        rows_lay.setContentsMargins(0, 0, 0, 0)
        rows_lay.setSpacing(8)
        self.rows = []
        for i, p in enumerate(video_paths):
            row = QFrame()
            row.setObjectName("card")
            r = QVBoxLayout(row)
            r.setContentsMargins(10, 6, 10, 8)
            r.setSpacing(4)
            head = QHBoxLayout()
            name = QLabel(f"{i + 1}. {p.name}")
            name.setToolTip(str(p))
            name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            status = QLabel("⏳ 待機中")
            status.setObjectName("pathLabel")
            head.addWidget(name, 1)
            head.addWidget(status)
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setFixedHeight(16)
            r.addLayout(head)
            r.addWidget(bar)
            rows_lay.addWidget(row)
            self.rows.append((status, bar))
        rows_lay.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(rows_widget)
        scroll.setMinimumHeight(min(60 + 62 * len(video_paths), 420))
        lay.addWidget(scroll, 1)

        self.btn_cancel = QPushButton("キャンセル")
        self.btn_cancel.clicked.connect(self._on_cancel)
        lay.addWidget(self.btn_cancel, 0, Qt.AlignRight)

    def setValue(self, val):
        self.bar_total.setValue(val)

    def setLabelText(self, text):
        self.lbl_total.setText(text)

    def update_video(self, idx, percent, status):
        if 0 <= idx < len(self.rows):
            lbl, bar = self.rows[idx]
            lbl.setText(status)
            bar.setValue(percent)

    def _on_cancel(self):
        self.btn_cancel.setEnabled(False)
        self.canceled.emit()

    def closeEvent(self, event):
        # 完了前に×で閉じられた場合はキャンセル扱い
        if self.btn_cancel.isEnabled():
            self.canceled.emit()
        event.accept()


class AdaptiveController:
    """全体の変換スループット(フレーム/秒)を監視し、同時変換数を山登り法で自動調整する"""

    def __init__(self, start, cap, window=3.0):
        self.limit = max(1, min(start, cap))
        self.cap = max(1, cap)
        self.window = window
        self.running = 0
        self._direction = 1
        self._lock = threading.Lock()
        self._frames = 0
        self._win_frames = 0
        self._win_t = time.time()
        self._last_rate = None

    def add(self, n):
        with self._lock:
            self._frames += n

    def note_gpu_failure(self):
        """GPUの同時セッション上限等で失敗したとき、上限を現在の並列数未満に抑える"""
        self.cap = max(1, min(self.cap, self.running - 1))
        self.limit = min(self.limit, self.cap)

    def tune(self, running, pending):
        """一定間隔で呼び出し。同時変換数が変化したらTrue"""
        self.running = running
        now = time.time()
        if now - self._win_t < self.window:
            return False
        with self._lock:
            frames = self._frames
        rate = (frames - self._win_frames) / (now - self._win_t)
        self._win_t, self._win_frames = now, frames

        # 全枠埋まっていない/最後の残りは計測が不正確なので判定しない
        if pending == 0 or running < self.limit:
            self._last_rate = None
            return False

        old = self.limit
        if self._last_rate is None:
            self._direction = 1
            step = 1  # まず増やしてみる
        elif rate > self._last_rate * 1.08:
            step = self._direction  # 改善したので同じ方向へ
        elif rate < self._last_rate * 0.92:
            self._direction = -self._direction  # 悪化したので逆方向へ
            step = self._direction
        else:
            # 変化なし: 増やして効果が無いなら元に戻す、減らしても同じなら現状維持
            step = -1 if self._direction > 0 else 0
            self._direction = -1
        self.limit = max(1, min(self.cap, self.limit + step))
        self._last_rate = rate
        return self.limit != old


class ProxyWorker(QThread):
    progress = Signal(int, int, str)
    video_progress = Signal(int, int, str)  # (動画の番号, 進捗%, 状態テキスト)
    finished = Signal()
    
    def __init__(self, video_paths, target_size=(320, 180)):
        super().__init__()
        self.video_paths = video_paths
        self.target_size = target_size
        self.is_cancelled = False
        
    def run(self):
        total_videos = len(self.video_paths)
        ffmpeg_path = shutil.which("ffmpeg")
        
        if ffmpeg_path:
            self.run_ffmpeg(ffmpeg_path, total_videos)
        else:
            self.run_opencv(total_videos)
            
        self.finished.emit()

    @staticmethod
    def _total_frames(path):
        cap = cv2.VideoCapture(str(path))
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()
        return max(n, 1)

    @staticmethod
    def _nvenc_available(ffmpeg_path, creationflags):
        """NVENC(GPUエンコード)が実際に使えるか短いテスト変換で確認する"""
        cmd = [ffmpeg_path, '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=320x180:d=0.2',
               '-c:v', 'h264_nvenc', '-f', 'null', '-']
        try:
            return subprocess.run(cmd, capture_output=True, creationflags=creationflags, timeout=20).returncode == 0
        except Exception:
            return False

    def _run_adaptive(self, task_fn, ctl, total_videos, mode_label):
        """同時実行数をctlに従って動的に増減させながら全動画を処理する"""
        pending = deque(enumerate(self.video_paths))
        running = {}
        completed = 0

        def report():
            self.progress.emit(int(completed / total_videos * 100), 100,
                               f"全体: {completed}/{total_videos} 本完了  |  {mode_label}  |  同時変換数: {ctl.limit} (自動調整, 最大{ctl.cap})")

        report()
        with concurrent.futures.ThreadPoolExecutor(max_workers=ctl.cap) as executor:
            while (pending or running) and not self.is_cancelled:
                while pending and len(running) < ctl.limit:
                    i, p = pending.popleft()
                    running[executor.submit(task_fn, i, p)] = i
                if not running:
                    break
                done, _ = concurrent.futures.wait(
                    list(running), timeout=0.5, return_when=concurrent.futures.FIRST_COMPLETED)
                for f in done:
                    del running[f]
                    completed += 1
                changed = ctl.tune(len(running), len(pending))
                if done or changed:
                    report()

    def run_ffmpeg(self, ffmpeg_path, total_videos):
        w, h = self.target_size
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0

        # GPU(NVENC)があるか事前判定。無ければ最初からCPUモード
        use_gpu = self._nvenc_available(ffmpeg_path, creationflags)
        cpu_cap = max(2, min(8, (os.cpu_count() or 4) // 2))
        ctl = AdaptiveController(2, 8 if use_gpu else cpu_cap)
        mode_label = "GPU(NVENC)" if use_gpu else "CPU(GPU未検出)"

        def run_cmd(cmd, idx, total_frames, label):
            """ffmpegを実行し、-progress出力から進捗%を通知する"""
            cmd = cmd[:1] + ['-progress', 'pipe:1', '-nostats'] + cmd[1:]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    creationflags=creationflags, text=True, errors='ignore')
            last_n = 0
            for line in proc.stdout:
                if self.is_cancelled:
                    proc.terminate()
                    break
                if line.startswith('frame='):
                    try:
                        n = int(line.split('=')[1].strip())
                    except ValueError:
                        continue
                    ctl.add(n - last_n)
                    last_n = n
                    pct = min(99, int(n / total_frames * 100))
                    self.video_progress.emit(idx, pct, f"🔄 {label} {pct}%")
            proc.wait()
            return proc.returncode

        def convert_video(idx, path):
            proxy_dir = path.parent / ".proxy"
            proxy_dir.mkdir(exist_ok=True)
            proxy_path = proxy_dir / path.name
            
            if proxy_path.exists():
                self.video_progress.emit(idx, 100, "✅ 作成済み(スキップ)")
                return True
                
            cmd_gpu = [
                ffmpeg_path, '-y', '-hwaccel', 'auto',
                '-i', str(path), '-vf', f'scale={w}:{h}',
                '-c:v', 'h264_nvenc', '-preset', 'p1', '-an', str(proxy_path)
            ]
            cmd_cpu = [
                ffmpeg_path, '-y',
                '-i', str(path), '-vf', f'scale={w}:{h}',
                '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '28', '-an', str(proxy_path)
            ]
            ok = False
            try:
                total_frames = self._total_frames(path)
                rc = 1
                if use_gpu:
                    self.video_progress.emit(idx, 0, "🔄 GPUで変換中 0%")
                    rc = run_cmd(cmd_gpu, idx, total_frames, "GPUで変換中")
                    if rc != 0 and not self.is_cancelled:
                        ctl.note_gpu_failure()  # 同時セッション上限等を超えた可能性
                if rc != 0 and not self.is_cancelled:
                    self.video_progress.emit(idx, 0, "🔄 CPUで変換中 0%")
                    rc = run_cmd(cmd_cpu, idx, total_frames, "CPUで変換中")
                ok = (rc == 0) and not self.is_cancelled
            except Exception:
                ok = False

            if ok:
                self.video_progress.emit(idx, 100, "✅ 完了")
            else:
                # 中途半端なファイルを残すと完成品扱いされるので削除
                try:
                    if proxy_path.exists(): proxy_path.unlink()
                except OSError:
                    pass
                self.video_progress.emit(idx, 0, "⚠ 中止/失敗")
            return ok

        self._run_adaptive(convert_video, ctl, total_videos, mode_label)

    def run_opencv(self, total_videos):
        ctl = AdaptiveController(2, max(2, min(8, os.cpu_count() or 4)))
        def convert_video_cv(idx, path):
            proxy_dir = path.parent / ".proxy"
            proxy_dir.mkdir(exist_ok=True)
            proxy_path = proxy_dir / path.name
            if proxy_path.exists():
                self.video_progress.emit(idx, 100, "✅ 作成済み(スキップ)")
                return
                
            cap = cv2.VideoCapture(str(path))
            fps = cap.get(cv2.CAP_PROP_FPS)
            if fps <= 0: fps = 30.0
            total_frames = max(int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), 1)
            
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            out = cv2.VideoWriter(str(proxy_path), fourcc, fps, self.target_size)
            
            n = 0
            while True:
                if self.is_cancelled: break
                ret, frame = cap.read()
                if not ret: break
                resized = cv2.resize(frame, self.target_size, interpolation=cv2.INTER_NEAREST)
                out.write(resized)
                n += 1
                if n % 15 == 0:
                    ctl.add(15)
                    pct = min(99, int(n / total_frames * 100))
                    self.video_progress.emit(idx, pct, f"🔄 CPUで変換中 {pct}%")
                
            cap.release()
            out.release()

            if self.is_cancelled:
                try:
                    if proxy_path.exists(): proxy_path.unlink()
                except OSError:
                    pass
                self.video_progress.emit(idx, 0, "⚠ 中止")
            else:
                self.video_progress.emit(idx, 100, "✅ 完了")
            
        self._run_adaptive(convert_video_cv, ctl, total_videos, "CPU(OpenCV)")


class DragDropListWidget(QListWidget):
    file_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            event.setDropAction(Qt.CopyAction)
            event.accept()
            files = []
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    files.append(url.toLocalFile())
            self.file_dropped.emit(files)
        else:
            event.ignore()


class VideoWorker(QThread):
    frames_ready = Signal(list, int, float)
    playback_stopped = Signal()

    def __init__(self, load_paths, preview_size):
        super().__init__()
        self.video_paths = load_paths
        self.preview_size = preview_size
        self.caps = []
        
        self.fps = 30.0
        self.max_frames = 0
        
        self.is_playing = False
        self.current_frame = 0
        self.force_seek = True
        
        self.running = True
        self.command_queue = []
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=8)

    def open_videos(self):
        for path in self.video_paths:
            cap = cv2.VideoCapture(str(path))
            self.caps.append(cap)
            frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            fps = cap.get(cv2.CAP_PROP_FPS)
            if frames > self.max_frames:
                self.max_frames = frames
            if fps > 0 and self.fps == 30.0:
                self.fps = fps
        return self.max_frames > 0

    def _process_cap(self, idx, cap, force_seek, current_frame):
        if force_seek:
            cap.set(cv2.CAP_PROP_POS_FRAMES, current_frame)
        ret, frame = cap.read()
        if ret:
            frame = cv2.resize(frame, self.preview_size, interpolation=cv2.INTER_NEAREST)
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = frame.shape
            return idx, True, (frame.tobytes(), w, h, ch)
        return idx, False, None

    def run(self):
        while self.running:
            if self.command_queue:
                cmd = self.command_queue.pop(0)
                if cmd['type'] == 'seek':
                    self.current_frame = cmd['frame']
                    self.force_seek = True
                    self.is_playing = False
                elif cmd['type'] == 'step':
                    self.current_frame = max(0, min(self.max_frames - 1, self.current_frame + cmd['delta']))
                    self.force_seek = True
                    self.is_playing = False
                elif cmd['type'] == 'play':
                    self.is_playing = True
                    self.force_seek = True
                elif cmd['type'] == 'pause':
                    self.is_playing = False
                elif cmd['type'] == 'stop':
                    self.running = False
                    break

            if self.is_playing or self.force_seek:
                start_time = time.time()
                
                futures = [
                    self.executor.submit(self._process_cap, idx, cap, self.force_seek, self.current_frame)
                    for idx, cap in enumerate(self.caps)
                ]
                
                results = []
                for future in concurrent.futures.as_completed(futures):
                    results.append(future.result())
                
                results.sort(key=lambda x: x[0])
                pixmaps = [res[2] if res[1] else None for res in results]

                self.force_seek = False
                current_time = self.current_frame / self.fps if self.fps > 0 else 0
                
                self.frames_ready.emit(pixmaps, self.current_frame, current_time)

                if self.is_playing:
                    if self.current_frame >= self.max_frames - 1:
                        self.is_playing = False
                        self.playback_stopped.emit()
                    else:
                        self.current_frame += 1
                        
                        elapsed = time.time() - start_time
                        target_delay = 1.0 / self.fps if self.fps > 0 else 1.0 / 30.0
                        sleep_time = target_delay - elapsed
                        if sleep_time > 0:
                            time.sleep(sleep_time)
            else:
                time.sleep(0.02)
                
        for cap in self.caps:
            cap.release()
        self.executor.shutdown(wait=False)

    def set_command(self, cmd):
        if cmd['type'] == 'seek':
            self.command_queue = [c for c in self.command_queue if c['type'] != 'seek']
        self.command_queue.append(cmd)


class MainWindow(QMainWindow):
    PREVIEW_CELL_WIDTH = 350  # プレビュー1枚あたりの横幅(余白込み)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("複数動画 同期抽出ツール")
        self.setStyleSheet(APP_STYLE)

        # 画面解像度に合わせてウィンドウサイズを決定 (小さい画面でもはみ出さない)
        screen = QApplication.primaryScreen().availableGeometry()
        w = min(1200, int(screen.width() * 0.9))
        h = min(850, int(screen.height() * 0.9))
        self.resize(w, h)
        self.setMinimumSize(min(760, screen.width()), min(560, screen.height()))

        self.last_extract_dir = None
        self.video_containers = []
        self.output_dir = None
        self.video_paths = [] # オリジナルの高画質パス
        self.worker = None
        self.proxy_worker = None
        
        self._build_ui()

    def _make_card(self, title):
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(8)
        lbl = QLabel(title)
        lbl.setObjectName("sectionTitle")
        lay.addWidget(lbl)
        return card, lay

    def _build_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(10)
        
        # --- Top Panel (動画リスト + 出力先) ---
        card_files, files_lay = self._make_card("🎞 入力動画  (ドラッグ＆ドロップでも追加できます)")
        
        file_list_layout = QHBoxLayout()
        file_list_layout.setSpacing(10)
        
        self.list_videos = DragDropListWidget()
        self.list_videos.setSelectionMode(QListWidget.ExtendedSelection)
        self.list_videos.setMinimumHeight(70)
        self.list_videos.setMaximumHeight(110)
        self.list_videos.file_dropped.connect(self._on_files_dropped)
        
        btn_layout = QVBoxLayout()
        btn_layout.setSpacing(6)
        btn_add = QPushButton("➕ 追加")
        btn_add.clicked.connect(self.add_videos)
        btn_remove = QPushButton("➖ 選択を削除")
        btn_remove.clicked.connect(self.remove_videos)
        btn_clear = QPushButton("🗑 全てクリア")
        btn_clear.clicked.connect(self.clear_videos)
        for b in (btn_add, btn_remove, btn_clear):
            btn_layout.addWidget(b)
        btn_layout.addStretch()
        
        file_list_layout.addWidget(self.list_videos, 1)
        file_list_layout.addLayout(btn_layout)
        files_lay.addLayout(file_list_layout)
        
        out_layout = QHBoxLayout()
        out_layout.setSpacing(10)
        btn_output = QPushButton("📁 出力先を選択")
        btn_output.clicked.connect(self.select_output_dir)
        self.lbl_output = QLabel("指定なし (一番上の動画と同じフォルダ内の extracted_frames)")
        self.lbl_output.setObjectName("pathLabel")
        self.lbl_output.setWordWrap(True)
        self.lbl_output.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.btn_open_folder = QPushButton("📂 抽出フォルダを開く")
        self.btn_open_folder.setToolTip("抽出したフレームの保存先をエクスプローラーで開きます")
        self.btn_open_folder.clicked.connect(self.open_output_folder)
        out_layout.addWidget(btn_output)
        out_layout.addWidget(self.lbl_output, 1)
        out_layout.addWidget(self.btn_open_folder)
        files_lay.addLayout(out_layout)
        
        button_row = QHBoxLayout()
        button_row.setSpacing(10)
        btn_proxy = QPushButton("⚙ ① 軽量キャッシュ(プロキシ)を作成")
        btn_proxy.setObjectName("accent")
        btn_proxy.setMinimumHeight(38)
        btn_proxy.clicked.connect(self.create_proxies)
        btn_load = QPushButton("▶ ② リストの動画を読み込む")
        btn_load.setObjectName("primary")
        btn_load.setMinimumHeight(38)
        btn_load.clicked.connect(self.load_videos)
        button_row.addWidget(btn_proxy, 1)
        button_row.addWidget(btn_load, 1)
        files_lay.addLayout(button_row)
        
        main_layout.addWidget(card_files, 0)
        
        # --- Central Panel (Grid) ---
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.grid_widget = QWidget()
        self.grid_layout = QGridLayout(self.grid_widget)
        self.grid_layout.setSpacing(10)
        self.grid_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.scroll_area.setWidget(self.grid_widget)
        
        main_layout.addWidget(self.scroll_area, 1)
        self.image_labels = []
        
        # --- Bottom Panel ---
        card_ctrl = QFrame()
        card_ctrl.setObjectName("card")
        bottom_layout = QVBoxLayout(card_ctrl)
        bottom_layout.setContentsMargins(14, 10, 14, 12)
        bottom_layout.setSpacing(8)
        
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimum(0)
        self.slider.setMaximum(100)
        self.slider.sliderPressed.connect(self.slider_pressed)
        self.slider.sliderReleased.connect(self.slider_released)
        self.slider.sliderMoved.connect(self.slider_moved)
        self.slider_is_pressed = False
        bottom_layout.addWidget(self.slider)
        
        self.lbl_frame_info = QLabel("Frame: 0 / 0  |  Time: 0.00s")
        self.lbl_frame_info.setObjectName("frameInfo")
        self.lbl_frame_info.setAlignment(Qt.AlignCenter)
        bottom_layout.addWidget(self.lbl_frame_info)
        
        control_layout = QHBoxLayout()
        control_layout.setSpacing(10)
        
        self.btn_play = QPushButton("▶ 再生")
        self.btn_play.setObjectName("primary")
        self.btn_play.clicked.connect(self.toggle_play)
        self.btn_play.setMinimumWidth(110)
        self.btn_play.setMinimumHeight(40)
        
        btn_prev = QPushButton("◀ -1 フレーム")
        btn_prev.clicked.connect(lambda: self.step_frame(-1))
        btn_prev.setMinimumHeight(40)
        
        btn_next = QPushButton("+1 フレーム ▶")
        btn_next.clicked.connect(lambda: self.step_frame(1))
        btn_next.setMinimumHeight(40)
        
        btn_extract = QPushButton("📸 元動画から現在のフレームを抽出")
        btn_extract.setObjectName("success")
        btn_extract.clicked.connect(self.extract_frames)
        btn_extract.setMinimumHeight(40)
        
        control_layout.addStretch(1)
        control_layout.addWidget(self.btn_play)
        control_layout.addWidget(btn_prev)
        control_layout.addWidget(btn_next)
        control_layout.addSpacing(24)
        control_layout.addWidget(btn_extract)
        control_layout.addStretch(1)
        
        bottom_layout.addLayout(control_layout)
        main_layout.addWidget(card_ctrl, 0)

    def _relayout_grid(self):
        """ウィンドウ幅に応じてプレビューの列数を自動調整"""
        if not self.video_containers:
            return
        width = self.scroll_area.viewport().width()
        columns = max(1, width // self.PREVIEW_CELL_WIDTH)
        for c in self.video_containers:
            self.grid_layout.removeWidget(c)
        for idx, c in enumerate(self.video_containers):
            row, col = divmod(idx, columns)
            self.grid_layout.addWidget(c, row, col)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout_grid()

    def open_output_folder(self):
        target = self.last_extract_dir or self.output_dir
        if target is None and self.list_videos.count() > 0:
            first = self.list_videos.item(0).data(Qt.UserRole)
            target = first.parent / 'extracted_frames'
        if target is None or not Path(target).exists():
            QMessageBox.information(self, "フォルダがありません", "まだフレームを抽出していないか、出力先フォルダが存在しません。")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(target))))

    @Slot(list)
    def _on_files_dropped(self, files):
        existing = [self.list_videos.item(i).data(Qt.UserRole) for i in range(self.list_videos.count())]
        allowed_exts = {'.mp4', '.avi', '.mov', '.mkv'}
        
        for f in files:
            path = Path(f)
            if path.suffix.lower() in allowed_exts and path not in existing:
                item = QListWidgetItem(f"{path.name}  ({path.parent})")
                item.setData(Qt.UserRole, path)
                self.list_videos.addItem(item)

    def add_videos(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "動画ファイルを追加", "", "動画ファイル (*.mp4 *.avi *.mov *.mkv);;すべてのファイル (*.*)"
        )
        existing = [self.list_videos.item(i).data(Qt.UserRole) for i in range(self.list_videos.count())]
        
        for f in files:
            path = Path(f)
            if path not in existing:
                item = QListWidgetItem(f"{path.name}  ({path.parent})")
                item.setData(Qt.UserRole, path)
                self.list_videos.addItem(item)

    def remove_videos(self):
        for item in self.list_videos.selectedItems():
            self.list_videos.takeItem(self.list_videos.row(item))

    def clear_videos(self):
        self.list_videos.clear()

    def select_output_dir(self):
        dir_path = QFileDialog.getExistingDirectory(self, "出力ディレクトリを選択")
        if dir_path:
            self.output_dir = Path(dir_path)
            self.lbl_output.setText(str(self.output_dir.absolute()))
            self.lbl_output.setStyleSheet("color: #8fa3ff;")

    def create_proxies(self):
        if self.list_videos.count() == 0:
            QMessageBox.critical(self, "エラー", "リストに動画がありません。")
            return
            
        mp4_files = [self.list_videos.item(i).data(Qt.UserRole) for i in range(self.list_videos.count())]
            
        if not shutil.which("ffmpeg"):
            msg = QMessageBox(self)
            msg.setWindowTitle("FFmpegが未インストールです")
            msg.setText("超高速変換ツール「FFmpeg」が見つかりませんでした。\n\n現在の状態(OpenCV)でも変換可能ですが、FFmpegを導入すると変換速度が飛躍的に向上(GPU対応)します。")
            msg.setInformativeText("【インストール方法】\nAnacondaをご利用の場合は、ターミナルで以下を実行してください：\nconda install -c conda-forge ffmpeg\n\nこのままOpenCVで変換を続行しますか？")
            btn_continue = msg.addButton("このまま続行 (遅い)", QMessageBox.AcceptRole)
            btn_cancel = msg.addButton("キャンセル", QMessageBox.RejectRole)
            msg.exec()
            
            if msg.clickedButton() == btn_cancel:
                return

        self.progress_dialog = ProxyProgressDialog(mp4_files, self)
        self.progress_dialog.setLabelText(f"全体: 0/{len(mp4_files)} 本完了")
        
        self.proxy_worker = ProxyWorker(mp4_files)
        
        self.proxy_worker.progress.connect(self.update_proxy_progress)
        self.proxy_worker.video_progress.connect(self.progress_dialog.update_video)
        self.proxy_worker.finished.connect(self.proxy_finished)
        self.progress_dialog.canceled.connect(self.cancel_proxy)
        
        self.proxy_worker.start()
        self.progress_dialog.show()
        
    @Slot(int, int, str)
    def update_proxy_progress(self, val, maximum, text):
        self.progress_dialog.setValue(val)
        self.progress_dialog.setLabelText(text)
        
    def proxy_finished(self):
        # 二重実行(finishedシグナルの重複発火)を防ぐ
        if self.proxy_worker is None:
            return
        self.proxy_worker = None
        try:
            self.progress_dialog.canceled.disconnect(self.cancel_proxy)
        except (RuntimeError, TypeError):
            pass
        self.progress_dialog.close()
        QMessageBox.information(self, "完了", "軽量キャッシュの作成が完了しました！\n「リストの動画を読み込む」ボタンを押すと軽量版で再生されます。")
        
    def cancel_proxy(self):
        if self.proxy_worker:
            self.proxy_worker.is_cancelled = True
            self.proxy_worker.wait()
            self.proxy_worker = None
        self.progress_dialog.close()

    def load_videos(self):
        if self.worker is not None:
            self.worker.set_command({'type': 'stop'})
            self.worker.wait()
            self.worker = None

        for i in reversed(range(self.grid_layout.count())): 
            widget = self.grid_layout.itemAt(i).widget()
            if widget is not None:
                widget.setParent(None)
        self.image_labels.clear()
        self.video_containers.clear()

        if self.list_videos.count() == 0:
            QMessageBox.critical(self, "エラー", "リストに動画がありません。")
            return

        self.video_paths = [self.list_videos.item(i).data(Qt.UserRole) for i in range(self.list_videos.count())]
        
        load_paths = []
        preview_size = (320, 180)

        for idx, orig_path in enumerate(self.video_paths):
            proxy_dir = orig_path.parent / ".proxy"
            proxy_path = proxy_dir / orig_path.name
            
            if proxy_path.exists():
                load_paths.append(proxy_path)
                title = orig_path.name + " [軽量]"
            else:
                load_paths.append(orig_path)
                title = orig_path.name
            
            container = QFrame()
            container.setObjectName("card")
            vbox = QVBoxLayout(container)
            vbox.setContentsMargins(10, 8, 10, 10)
            vbox.setSpacing(6)
            
            lbl_title = QLabel(f"{idx + 1}. {title}")
            lbl_title.setObjectName("videoTitle")
            lbl_title.setToolTip(str(orig_path))
            lbl_title.setMaximumWidth(preview_size[0])
            vbox.addWidget(lbl_title)
            
            lbl_img = QLabel()
            lbl_img.setObjectName("videoView")
            lbl_img.setFixedSize(preview_size[0], preview_size[1])
            vbox.addWidget(lbl_img)
            self.image_labels.append(lbl_img)
            self.video_containers.append(container)
        
        self._relayout_grid()

        self.worker = VideoWorker(load_paths, preview_size)
        if not self.worker.open_videos():
            QMessageBox.critical(self, "エラー", "動画を開けませんでした。")
            return
            
        self.slider.setMaximum(self.worker.max_frames - 1)
        self.worker.frames_ready.connect(self.update_ui)
        self.worker.playback_stopped.connect(lambda: self.btn_play.setText("▶ 再生"))
        
        self.worker.start()

    @Slot(list, int, float)
    def update_ui(self, frames_data, current_frame, current_time):
        if not self.slider_is_pressed:
            self.slider.blockSignals(True)
            self.slider.setValue(current_frame)
            self.slider.blockSignals(False)
            
        self.lbl_frame_info.setText(f"Frame: {current_frame} / {self.worker.max_frames - 1}  |  Time: {current_time:.2f}s")
        
        for idx, data in enumerate(frames_data):
            if data is not None:
                raw_bytes, w, h, ch = data
                qimg = QImage(raw_bytes, w, h, ch * w, QImage.Format_RGB888)
                self.image_labels[idx].setPixmap(QPixmap.fromImage(qimg))
            else:
                self.image_labels[idx].clear()

    def toggle_play(self):
        if not self.worker: return
        if self.worker.is_playing:
            self.worker.set_command({'type': 'pause'})
            self.btn_play.setText("▶ 再生")
        else:
            self.worker.set_command({'type': 'play'})
            self.btn_play.setText("⏸ 停止")

    def step_frame(self, step):
        if not self.worker: return
        self.btn_play.setText("▶ 再生")
        self.worker.set_command({'type': 'step', 'delta': step})

    def slider_pressed(self):
        self.slider_is_pressed = True
        if self.worker and self.worker.is_playing:
            self.toggle_play()

    def slider_released(self):
        self.slider_is_pressed = False
        if self.worker:
            self.worker.set_command({'type': 'seek', 'frame': self.slider.value()})

    def slider_moved(self, val):
        pass

    def extract_frames(self):
        if not self.worker: return
        
        if self.worker.is_playing:
            self.toggle_play()

        if self.output_dir:
            out_dir = self.output_dir
        else:
            if not self.video_paths: return
            out_dir = self.video_paths[0].parent / 'extracted_frames'
            
        current_frame = self.worker.current_frame
        current_time = current_frame / self.worker.fps if self.worker.fps > 0 else 0
        
        out_sub_dir = out_dir / f"frame_{current_frame}_time_{current_time:.2f}s"
        out_sub_dir.mkdir(parents=True, exist_ok=True)

        success_count = 0
        for path in self.video_paths:
            cap = cv2.VideoCapture(str(path))
            cap.set(cv2.CAP_PROP_POS_FRAMES, current_frame)
            ret, frame = cap.read()
            cap.release()

            if ret:
                out_file = out_sub_dir / f"{path.stem}.png"
                is_success, im_buf_arr = cv2.imencode(".png", frame)
                if is_success:
                    with open(out_file, "wb") as f:
                        f.write(im_buf_arr.tobytes())
                    success_count += 1

        self.last_extract_dir = out_sub_dir

        msg = QMessageBox(self)
        msg.setWindowTitle("抽出完了")
        msg.setText(f"{success_count}/{len(self.video_paths)} 件のオリジナル画像を保存しました。\n\n保存先:\n{out_sub_dir}")
        btn_open = msg.addButton("📂 フォルダを開く", QMessageBox.ActionRole)
        msg.addButton("閉じる", QMessageBox.RejectRole)
        msg.exec()
        if msg.clickedButton() == btn_open:
            self.open_output_folder()

    def closeEvent(self, event):
        if self.worker:
            self.worker.set_command({'type': 'stop'})
            self.worker.wait()
        if self.proxy_worker:
            self.proxy_worker.is_cancelled = True
            self.proxy_worker.wait()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())