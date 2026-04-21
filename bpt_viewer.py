from __future__ import annotations
import os, sys, struct, threading, queue
import tkinter as tk
from tkinter import filedialog, messagebox

try:
    import numpy as np
    import imagecodecs
    from PIL import Image, ImageTk, ImageDraw
    from scipy.ndimage import map_coordinates
    from scipy.interpolate import splprep, splev
except ImportError:
    print("Dependências faltando. Execute:")
    print("  pip install numpy imagecodecs Pillow scipy")
    sys.exit(1)

class BPTParser:
    HEADER_SIZE = 64

    @classmethod
    def _u32_as_f32(cls, v: int) -> float:
        return struct.unpack('<f', struct.pack('<I', v))[0]

    @classmethod
    def parse(cls, data: bytes) -> tuple[dict, list[bytes]]:
        if len(data) < cls.HEADER_SIZE:
            raise ValueError(f"Arquivo muito pequeno ({len(data)} bytes).")

        h = struct.unpack('<16I', data[:cls.HEADER_SIZE])
        meta = {
            'width':           h[6],
            'height':          h[7],
            'num_slices':      h[8],
            'spacing_x':       cls._u32_as_f32(h[9]),
            'spacing_y':       cls._u32_as_f32(h[10]),
            'spacing_z':       cls._u32_as_f32(h[11]),
            'first_slice_len': h[15],
        }

        W, H, N = meta['width'], meta['height'], meta['num_slices']
        if not (0 < W <= 4096 and 0 < H <= 4096 and 0 < N <= 10000):
            raise ValueError(f"Header suspeito: {meta}")

        first_len = meta['first_slice_len']
        offset    = cls.HEADER_SIZE
        payloads  = []

        payload = data[offset: offset + first_len]
        if len(payload) != first_len:
            raise ValueError("Primeira fatia truncada.")
        payloads.append(payload)
        offset += first_len

        for i in range(1, N):
            if offset + 4 > len(data):
                raise ValueError(f"Arquivo truncado antes da fatia {i}.")
            sz = int.from_bytes(data[offset:offset + 4], 'little')
            offset += 4
            if sz <= 0 or offset + sz > len(data):
                raise ValueError(f"Tamanho inválido na fatia {i}: {sz}.")
            payloads.append(data[offset: offset + sz])
            offset += sz

        meta['leftover_bytes'] = len(data) - offset
        return meta, payloads

TOOLBAR_H  = 38
PANEL_PAD  = 3
BG_DARK    = "#1a1a1a"
BG_PANEL   = "#111111"
BG_TOOLBAR = "#242424"
CROSS_COL  = "#00ff88"
CURVE_COL  = "#ff9900"
LABEL_FG   = "#aaaaaa"
RESAMPLE   = Image.Resampling.BILINEAR

class TomoViewer(tk.Tk):

    def __init__(self, filepath: str | None = None):
        super().__init__()
        self.title("Visualizador MPR  ·  v5")
        self.geometry("1280x900")
        self.configure(bg=BG_DARK)
        self.minsize(800, 600)

        self.volume: np.ndarray | None = None
        self.vol_norm: np.ndarray | None = None
        self.meta: dict = {}

        self.wl_center = 0
        self.wl_width  = 1
        self.focal_z   = 0
        self.focal_y   = 0
        self.focal_x   = 0
        self.z_aspect    = 1.0
        self._spacing_x  = 1.0
        self._spacing_y  = 1.0
        self._spacing_z  = 1.0
        self._physical_z_aspect = 1.0

        self.interaction_mode   = 'navigate'
        self.curve_points_axial: list[tuple[float, float]] = []
        self.show_guides = True

        self._wl_job   = None
        self._load_q:  queue.Queue = queue.Queue()
        self._load_id: int = 0
        self._load_cancel: threading.Event | None = None

        self._mip_raw: np.ndarray | None = None
        self._mip_display: np.ndarray | None = None
        self._pano_win: tk.Toplevel | None = None
        self._resize_job = None

        self._build_ui()
        self._poll_load_queue()

        if filepath and os.path.exists(filepath):
            self.after(200, lambda: self._start_load(filepath))

    def _build_ui(self):
        tb = tk.Frame(self, bg=BG_TOOLBAR, height=TOOLBAR_H)
        tb.pack(fill=tk.X, side=tk.TOP)
        tb.pack_propagate(False)

        def _sep():
            tk.Frame(tb, bg="#444", width=1).pack(side=tk.LEFT, fill=tk.Y, pady=4)

        tk.Button(tb, text="Abrir .BPT", command=self._browse,
                  bg="#2d5a3d", fg="white", relief=tk.FLAT,
                  font=("Consolas", 9, "bold"), padx=8).pack(side=tk.LEFT, padx=6, pady=5)
        _sep()

        tk.Label(tb, text="Brilho:", bg=BG_TOOLBAR, fg=LABEL_FG,
                 font=("Consolas", 8)).pack(side=tk.LEFT, padx=(8, 2))
        self._sc_wc = tk.Scale(tb, from_=0, to=4095, orient=tk.HORIZONTAL,
                               bg=BG_TOOLBAR, fg="white", highlightthickness=0,
                               troughcolor="#333", length=100, width=8, sliderlength=12,
                               showvalue=False, command=self._wl_delayed)
        self._sc_wc.pack(side=tk.LEFT)

        tk.Label(tb, text="Contraste:", bg=BG_TOOLBAR, fg=LABEL_FG,
                 font=("Consolas", 8)).pack(side=tk.LEFT, padx=(6, 2))
        self._sc_ww = tk.Scale(tb, from_=1, to=4096, orient=tk.HORIZONTAL,
                               bg=BG_TOOLBAR, fg="white", highlightthickness=0,
                               troughcolor="#333", length=100, width=8, sliderlength=12,
                               showvalue=False, command=self._wl_delayed)
        self._sc_ww.pack(side=tk.LEFT)

        _sep()

        tk.Label(tb, text="Asp.Z:", bg=BG_TOOLBAR, fg=LABEL_FG,
                 font=("Consolas", 8)).pack(side=tk.LEFT, padx=(6, 2))
        self._sc_az = tk.Scale(tb, from_=5, to=40, orient=tk.HORIZONTAL,
                               bg=BG_TOOLBAR, fg="white", highlightthickness=0,
                               troughcolor="#333", length=70, width=8, sliderlength=12,
                               showvalue=False, command=self._az_changed)
        self._sc_az.set(10)
        self._sc_az.pack(side=tk.LEFT)

        self._lbl_az = tk.Label(tb, text="1.0×", bg=BG_TOOLBAR, fg=LABEL_FG,
                                font=("Consolas", 8), width=4)
        self._lbl_az.pack(side=tk.LEFT)

        _sep()

        for axis_label, view_key in [("Z/MIP", "axial"), ("Y", "coronal"),
                                      ("X", "sagittal")]:
            tk.Label(tb, text=f"{axis_label}:", bg=BG_TOOLBAR, fg=LABEL_FG,
                     font=("Consolas", 8)).pack(side=tk.LEFT, padx=(4, 0))
            tk.Button(tb, text="◂",
                      command=lambda k=view_key: self._step_view(k, -1),
                      bg=BG_TOOLBAR, fg="white", relief=tk.FLAT,
                      font=("Consolas", 8), padx=2, pady=0).pack(side=tk.LEFT)
            tk.Button(tb, text="▸",
                      command=lambda k=view_key: self._step_view(k, +1),
                      bg=BG_TOOLBAR, fg="white", relief=tk.FLAT,
                      font=("Consolas", 8), padx=2, pady=0).pack(side=tk.LEFT)

        _sep()

        self._btn_curve = tk.Button(tb, text="✏  Panorâmica",
                                    command=self._toggle_curve_mode,
                                    bg="#3a3a00", fg="#ffcc00", relief=tk.FLAT,
                                    font=("Consolas", 8, "bold"), padx=6)
        self._btn_curve.pack(side=tk.LEFT, padx=6, pady=5)

        tk.Button(tb, text="✕", command=self._clear_curve,
                  bg=BG_TOOLBAR, fg="#ff6666", relief=tk.FLAT,
                  font=("Consolas", 8), padx=3).pack(side=tk.LEFT, pady=5)

        _sep()

        self._btn_guides = tk.Button(
            tb, text="✚", command=self._toggle_guides,
            bg="#2a4a3a", fg="#00ff88", relief=tk.FLAT,
            font=("Consolas", 9, "bold"), padx=4)
        self._btn_guides.pack(side=tk.LEFT, padx=2, pady=5)

        tk.Button(tb, text="💾", command=self._export_individual_slices,
                  bg="#2d5a3d", fg="white", relief=tk.FLAT,
                  font=("Consolas", 9), padx=4).pack(side=tk.LEFT, padx=2, pady=5)

        self._lbl_status = tk.Label(tb, text="Aguardando arquivo…",
                                    bg=BG_TOOLBAR, fg="#555",
                                    font=("Consolas", 8), anchor="e")
        self._lbl_status.pack(side=tk.RIGHT, padx=10)

        self._vpaned = tk.PanedWindow(self, orient=tk.VERTICAL,
                                      bg=BG_DARK, sashwidth=4,
                                      sashrelief=tk.RAISED, opaqueresize=True)
        self._vpaned.pack(fill=tk.BOTH, expand=True)

        self._hpaned_top = tk.PanedWindow(self._vpaned, orient=tk.HORIZONTAL,
                                          bg=BG_DARK, sashwidth=4,
                                          sashrelief=tk.RAISED, opaqueresize=True)
        self._hpaned_bot = tk.PanedWindow(self._vpaned, orient=tk.HORIZONTAL,
                                          bg=BG_DARK, sashwidth=4,
                                          sashrelief=tk.RAISED, opaqueresize=True)
        self._vpaned.add(self._hpaned_top, stretch='always')
        self._vpaned.add(self._hpaned_bot, stretch='always')

        self._panels = {}
        for key, title, paned in [
            ('axial',    'AXIAL',                   self._hpaned_top),
            ('coronal',  'CORONAL',                 self._hpaned_top),
            ('sagittal', 'SAGITAL',                 self._hpaned_bot),
            ('mip',      'MIP  ·  Raio-X Frontal', self._hpaned_bot),
        ]:
            self._panels[key] = self._make_panel(paned, title, key)

        self.after_idle(self._init_panes)

    def _make_panel(self, paned, title, key):
        outer = tk.Frame(paned, bg="#2a2a2a", bd=1, relief=tk.SUNKEN)
        outer.pack_propagate(False)
        paned.add(outer, stretch='always')

        hdr = tk.Frame(outer, bg="#1e1e1e", height=18)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)
        tk.Label(hdr, text=title, bg="#1e1e1e", fg="#3a9e6a",
                 font=("Consolas", 8, "bold"), anchor="w", padx=6).pack(side=tk.LEFT)
        lbl_pos = tk.Label(hdr, text="—", bg="#1e1e1e", fg="#555",
                           font=("Consolas", 8), anchor="e", padx=6)
        lbl_pos.pack(side=tk.RIGHT)

        canvas = tk.Label(outer, bg="black", cursor="crosshair")
        canvas.pack(fill=tk.BOTH, expand=True)

        canvas.bind("<Button-1>",   lambda e, k=key: self._on_click(e, k))
        canvas.bind("<B1-Motion>",  lambda e, k=key: self._on_click(e, k))
        canvas.bind("<MouseWheel>", lambda e, k=key: self._on_scroll(e, k))
        canvas.bind("<Button-4>",   lambda e, k=key: self._on_scroll(e, k, +1))
        canvas.bind("<Button-5>",   lambda e, k=key: self._on_scroll(e, k, -1))
        outer.bind("<Configure>",   lambda e, k=key: self._on_panel_resize(e, k))

        return {'canvas': canvas, 'photo': None, 'pil': None, 'lbl_pos': lbl_pos}

    def _init_panes(self):
        self.update_idletasks()
        w = self._vpaned.winfo_width()
        h = self._vpaned.winfo_height()
        if w > 10:
            self._hpaned_top.sash_place(0, w // 2, 0)
            self._hpaned_bot.sash_place(0, w // 2, 0)
        if h > 10:
            self._vpaned.sash_place(0, 0, h // 2)

    def _browse(self):
        fp = filedialog.askopenfilename(
            filetypes=[("BPT Tomography", "*.bpt"), ("Todos", "*.*")])
        if fp:
            self._start_load(fp)

    def _start_load(self, filepath: str):
        if self._load_cancel is not None:
            self._load_cancel.set()
        self._load_cancel = threading.Event()
        self._load_id += 1
        token = self._load_id
        self._reset_view_state()
        self._status(f"Lendo {os.path.basename(filepath)}…", "#ffcc00")
        threading.Thread(
            target=self._load_worker,
            args=(filepath, token, self._load_cancel),
            daemon=True,
        ).start()

    def _reset_view_state(self):
        """[C] Limpa todo estado de sessão: lógico, dados e visual."""
        if self._wl_job is not None:
            self.after_cancel(self._wl_job)
            self._wl_job = None

        self.interaction_mode = 'navigate'
        self.curve_points_axial.clear()
        self._btn_curve.config(text="✏  Panorâmica", bg="#3a3a00", fg="#ffcc00")

        self.volume   = None
        self.vol_norm = None
        self.meta     = {}
        self.focal_z = self.focal_y = self.focal_x = 0
        self._spacing_x = self._spacing_y = self._spacing_z = 1.0
        self._physical_z_aspect = 1.0
        self.z_aspect = 1.0
        self._sc_az.set(10)
        self._lbl_az.config(text="1.0×")

        self._mip_raw     = None
        self._mip_display = None

        if self._resize_job is not None:
            try:
                self.after_cancel(self._resize_job)
            except tk.TclError:
                pass
            self._resize_job = None

        if self._pano_win is not None:
            try:
                self._pano_win.destroy()
            except tk.TclError:
                pass
            self._pano_win = None

        self.show_guides = True
        self._btn_guides.config(bg="#2a4a3a", fg="#00ff88")

        for p in self._panels.values():
            p['lbl_pos'].config(text="—")
            p['canvas'].config(image='')
            p['photo'] = None
            p['pil']   = None

    def _load_worker(self, filepath: str, token: int, cancel: threading.Event):
        try:
            with open(filepath, 'rb') as f:
                raw = f.read()
            if cancel.is_set():
                return

            meta, payloads = BPTParser.parse(raw)

            for sk in ('spacing_x', 'spacing_y', 'spacing_z'):
                sv = meta.get(sk, 0)
                if not (0 < sv < 100):
                    raise ValueError(f"{sk} inválido: {sv}")

            exp_shape = (meta['height'], meta['width'])
            arrays = []
            for i, payload in enumerate(payloads):
                if cancel.is_set():
                    return
                try:
                    arr = imagecodecs.jpeg_decode(payload)
                except Exception as e:
                    raise ValueError(f"Falha ao decodificar fatia {i}: {e}") from e
                if arr.ndim != 2:
                    raise ValueError(f"Fatia {i} não é grayscale (shape={arr.shape}).")
                if arr.shape != exp_shape:
                    raise ValueError(
                        f"Fatia {i}: shape {arr.shape} diverge do header {exp_shape}.")
                arrays.append(arr)

            if cancel.is_set():
                return
            volume = np.stack(arrays, axis=0).astype(np.float32)
            self._load_q.put(('ok', token, volume, meta))

        except Exception as ex:
            self._load_q.put(('error', token, str(ex)))

    def _poll_load_queue(self):
        try:
            while True:
                try:
                    msg = self._load_q.get_nowait()
                except queue.Empty:
                    break
                token = msg[1]
                if token != self._load_id:
                    continue
                if msg[0] == 'ok':
                    _, _, volume, meta = msg
                    self._on_load_success(volume, meta)
                else:
                    messagebox.showerror("Erro ao carregar", msg[2])
                    self._status("Erro.", "#ff4444")
        except tk.TclError:
            return
        try:
            self.after(50, self._poll_load_queue)
        except tk.TclError:
            pass

    def _on_load_success(self, volume: np.ndarray, meta: dict):
        self.volume = volume
        self.meta   = meta
        Z, Y, X     = volume.shape

        self._spacing_x = float(meta.get('spacing_x', 1.0) or 1.0)
        self._spacing_y = float(meta.get('spacing_y', 1.0) or 1.0)
        self._spacing_z = float(meta.get('spacing_z', 1.0) or 1.0)
        self._physical_z_aspect = self._spacing_z / self._spacing_x

        sample  = volume[::4, ::4, ::4]
        p01     = float(np.percentile(sample, 0.5))
        p99     = float(np.percentile(sample, 99.5))
        self.wl_center = int((p01 + p99) / 2)
        self.wl_width  = int(p99 - p01)

        maxv = max(int(p99 * 1.5), self.wl_center + self.wl_width, 100)
        self._sc_wc.config(to=maxv); self._sc_wc.set(self.wl_center)
        self._sc_ww.config(to=maxv); self._sc_ww.set(self.wl_width)

        self.z_aspect = 1.5
        self._sc_az.set(15)
        self._lbl_az.config(text="1.5×")

        self.focal_z = Z // 2
        self.focal_y = Y // 2
        self.focal_x = X // 2

        self._mip_raw = volume.max(axis=1).astype(np.float32, copy=False)

        self._apply_wl()
        self._rebuild_mip()
        self._render_all()

        anis_note = ""
        if abs(self._spacing_x - self._spacing_y) > 1e-4:
            anis_note = f"  ⚠ Δx={self._spacing_x:.4f} ≠ Δy={self._spacing_y:.4f}"

        leftover = meta.get('leftover_bytes', 0)
        tail_note = (f"  [+{leftover//1024} KB restantes]" if leftover > 0 else "")
        self._status(
            f"✓  {Z} fatias · {Y}×{X} px · "
            f"Δx={meta['spacing_x']:.3f} Δz={meta['spacing_z']:.3f} mm"
            f"  físico={self._physical_z_aspect:.2f}×"
            f"{anis_note}{tail_note}",
            "#ff9900" if anis_note else "#3a9e6a"
        )

    def _wl_delayed(self, *_):
        if self._wl_job: self.after_cancel(self._wl_job)
        self._wl_job = self.after(80, self._wl_commit)

    def _wl_commit(self):
        if self.volume is None: return
        self.wl_center = self._sc_wc.get()
        self.wl_width  = self._sc_ww.get()
        self._apply_wl()
        self._rebuild_mip()
        self._render_all()

    def _apply_wl(self):
        vmin = self.wl_center - self.wl_width / 2.0
        vmax = self.wl_center + self.wl_width / 2.0
        span = max(vmax - vmin, 1.0)
        self.vol_norm = np.clip(
            (self.volume - vmin) / span * 255.0, 0, 255
        ).astype(np.uint8)

    def _az_changed(self, val):
        self.z_aspect = int(val) / 10.0
        self._lbl_az.config(text=f"{self.z_aspect:.1f}×")
        self._render_all()

    def _rebuild_mip(self):
        if self._mip_raw is None:
            return
        vmin = self.wl_center - self.wl_width / 2.0
        vmax = self.wl_center + self.wl_width / 2.0
        span = max(vmax - vmin, 1.0)
        mip_8 = np.clip(
            (self._mip_raw - vmin) / span * 255.0, 0, 255
        ).astype(np.uint8)
        self._mip_display = mip_8[::-1]

    def _render_all(self):
        if self.vol_norm is None: return
        self._render_axial()
        self._render_coronal()
        self._render_sagittal()
        self._render_mip()

    def _panel_size(self, key):
        c = self._panels[key]['canvas']
        w, h = c.winfo_width(), c.winfo_height()
        return (w if w > 10 else 400), (h if h > 10 else 320)

    def _panel_geom(self, key):
        """[B] Geometria unificada para render e click."""
        Z, Y, X = self.vol_norm.shape
        cw, ch  = self._panel_size(key)
        xy_ratio = self._spacing_y / self._spacing_x

        if key == 'axial':
            rows, cols = Y, X
            row_ratio, col_ratio = xy_ratio, 1.0
        elif key in ('coronal', 'mip'):
            rows, cols = Z, X
            row_ratio, col_ratio = self.z_aspect, 1.0
        elif key == 'sagittal':
            rows, cols = Z, Y
            row_ratio, col_ratio = self.z_aspect, xy_ratio
        else:
            raise ValueError(key)

        phys_w = cols * col_ratio
        phys_h = rows * row_ratio
        s = min(cw / phys_w, ch / phys_h) if (phys_w > 0 and phys_h > 0) else 1.0
        px_col = col_ratio * s
        px_row = row_ratio * s
        nw = max(1, int(round(cols * px_col)))
        nh = max(1, int(round(rows * px_row)))
        ox = (cw - nw) // 2
        oy = (ch - nh) // 2
        return rows, cols, nw, nh, px_col, px_row, ox, oy

    def _cross(self, img: Image.Image, cx: int, cy: int, color=CROSS_COL):
        draw = ImageDraw.Draw(img)
        rgb  = tuple(int(color.lstrip('#')[i:i+2], 16) for i in (0, 2, 4))
        draw.line([(0, cy), (img.width, cy)], fill=rgb, width=1)
        draw.line([(cx, 0), (cx, img.height)], fill=rgb, width=1)

    def _show(self, key, pil_img: Image.Image):
        photo = ImageTk.PhotoImage(pil_img)
        p = self._panels[key]
        p['canvas'].config(image=photo, width=1, height=1)
        p['photo'] = photo
        p['pil']   = pil_img.copy()

    def _render_axial(self):
        Z, Y, X = self.vol_norm.shape
        arr = self.vol_norm[self.focal_z]
        _, _, nw, nh, px_col, px_row, *_ = self._panel_geom('axial')
        img = Image.fromarray(arr, 'L').resize((nw, nh), RESAMPLE).convert('RGB')
        if self.show_guides:
            self._cross(img, int(self.focal_x * px_col), int(self.focal_y * px_row))
        if len(self.curve_points_axial) >= 2:
            self._draw_curve_overlay(img, px_col, px_row)
        self._panels['axial']['lbl_pos'].config(text=f"z={self.focal_z}")
        self._show('axial', img)

    def _render_coronal(self):
        Z, Y, X = self.vol_norm.shape
        arr = self.vol_norm[::-1, self.focal_y, :]
        _, _, nw, nh, px_col, px_row, *_ = self._panel_geom('coronal')
        img = Image.fromarray(arr, 'L').resize((nw, nh), RESAMPLE).convert('RGB')
        if self.show_guides:
            self._cross(img, int(self.focal_x * px_col),
                        int((Z - 1 - self.focal_z) * px_row))
        self._panels['coronal']['lbl_pos'].config(text=f"y={self.focal_y}")
        self._show('coronal', img)

    def _render_sagittal(self):
        Z, Y, X = self.vol_norm.shape
        arr = self.vol_norm[::-1, :, self.focal_x]
        _, _, nw, nh, px_col, px_row, *_ = self._panel_geom('sagittal')
        img = Image.fromarray(arr, 'L').resize((nw, nh), RESAMPLE).convert('RGB')
        if self.show_guides:
            self._cross(img, int(self.focal_y * px_col),
                        int((Z - 1 - self.focal_z) * px_row))
        self._panels['sagittal']['lbl_pos'].config(text=f"x={self.focal_x}")
        self._show('sagittal', img)

    def _render_mip(self):
        if self._mip_display is None: return
        Z = self.vol_norm.shape[0]
        mip = self._mip_display
        _, _, nw, nh, px_col, px_row, *_ = self._panel_geom('mip')
        img = Image.fromarray(mip, 'L').resize((nw, nh), RESAMPLE).convert('RGB')
        if self.show_guides:
            cx = int(self.focal_x * px_col)
            cy = int((Z - 1 - self.focal_z) * px_row)
            draw = ImageDraw.Draw(img)
            draw.line([(0, cy), (nw, cy)], fill=(220, 50, 50), width=1)
            draw.line([(cx, 0), (cx, nh)], fill=(220, 50, 50), width=1)
        self._show('mip', img)

    def _draw_curve_overlay(self, img: Image.Image, px_col: float, px_row: float):
        pts  = self.curve_points_axial
        draw = ImageDraw.Draw(img)
        col  = tuple(int(CURVE_COL.lstrip('#')[i:i+2], 16) for i in (0, 2, 4))
        screen = [(p[0] * px_col, p[1] * px_row) for p in pts]
        if len(pts) >= 4:
            try:
                xs = np.array([p[0] for p in pts], float)
                ys = np.array([p[1] for p in pts], float)
                tck, _ = splprep([xs * px_col, ys * px_row], s=0,
                                 k=min(3, len(pts)-1))
                xi, yi = splev(np.linspace(0, 1, 400), tck)
                draw.line(list(zip(xi.tolist(), yi.tolist())), fill=col, width=2)
            except Exception:
                draw.line(screen, fill=col, width=2)
        else:
            draw.line(screen, fill=col, width=2)
        for sx2, sy2 in screen:
            draw.ellipse([sx2-3, sy2-3, sx2+3, sy2+3], fill=col)

    def _toggle_curve_mode(self):
        if self.interaction_mode == 'navigate':
            self.interaction_mode = 'draw_curve'
            self._btn_curve.config(text="✓ Calcular", bg="#665500", fg="#ffee44")
            self._status("Clique na visão AXIAL para traçar a curva. "
                         "Clique ✓ para calcular.", "#ffcc00")
        else:
            self.interaction_mode = 'navigate'
            self._btn_curve.config(text="✏  Panorâmica", bg="#3a3a00", fg="#ffcc00")
            self._compute_curved_mpr()

    def _clear_curve(self):
        self.curve_points_axial = []
        self.interaction_mode   = 'navigate'
        self._btn_curve.config(text="✏  Panorâmica", bg="#3a3a00", fg="#ffcc00")
        if self.vol_norm is not None:
            self._render_axial()
        self._status("Curva apagada.", LABEL_FG)

    def _compute_curved_mpr(self):
        if self.volume is None or len(self.curve_points_axial) < 4:
            messagebox.showinfo("Panorâmica",
                                "Desenhe pelo menos 4 pontos na visão Axial.")
            return

        Z, Y, X = self.volume.shape
        sx, sy  = self._spacing_x, self._spacing_y

        xs = np.array([p[0] for p in self.curve_points_axial], float)
        ys = np.array([p[1] for p in self.curve_points_axial], float)

        try:
            tck, _ = splprep([xs * sx, ys * sy], s=0, k=min(3, len(xs)-1))
        except Exception as e:
            messagebox.showerror("Erro", f"Spline falhou: {e}")
            return

        u_dense   = np.linspace(0.0, 1.0, 4000)
        xd_mm, yd_mm = splev(u_dense, tck)
        seg_mm    = np.hypot(np.diff(xd_mm), np.diff(yd_mm))
        arc_mm    = float(seg_mm.sum())

        sample_pitch = min(sx, sy)
        n_samp = int(np.clip(arc_mm / sample_pitch, 200, 1200))

        cum_mm = np.concatenate([[0.0], np.cumsum(seg_mm)])
        u_arc  = np.interp(np.linspace(0.0, arc_mm, n_samp), cum_mm, u_dense)
        xi_mm, yi_mm = splev(u_arc, tck)

        tx_mm, ty_mm = splev(u_arc, tck, der=1)
        norm_len = np.hypot(tx_mm, ty_mm) + 1e-9
        nx_mm = -ty_mm / norm_len
        ny_mm =  tx_mm / norm_len

        SLAB_HALF_MM   = 2.0
        n_slab         = max(3, 2 * int(round(SLAB_HALF_MM / sample_pitch)) + 1)
        offsets_mm     = np.linspace(-SLAB_HALF_MM, SLAB_HALF_MM, n_slab)
        z_idx          = np.arange(Z, dtype=np.float64)[::-1]
        vmin           = self.wl_center - self.wl_width / 2.0
        vmax           = self.wl_center + self.wl_width / 2.0

        slab_imgs = []
        for off in offsets_mm:
            xi_off = np.clip((xi_mm + nx_mm * off) / sx, 0, X - 1)
            yi_off = np.clip((yi_mm + ny_mm * off) / sy, 0, Y - 1)
            cz = np.tile(z_idx[:, None],  (1, n_samp))
            cy = np.tile(yi_off[None, :], (Z, 1))
            cx = np.tile(xi_off[None, :], (Z, 1))
            slab_imgs.append(
                map_coordinates(self.volume, [cz, cy, cx], order=1, mode='nearest'))

        pano_float = np.stack(slab_imgs, axis=-1).max(axis=-1)
        pano_8 = np.clip(
            (pano_float - vmin) / max(vmax - vmin, 1) * 255, 0, 255
        ).astype(np.uint8)

        self._show_panoramic(pano_8, arc_mm=arc_mm)

    def _show_panoramic(self, arr: np.ndarray, arc_mm: float = 0.0):
        if self._pano_win is not None:
            try:
                self._pano_win.destroy()
            except tk.TclError:
                pass
        win = tk.Toplevel(self)
        self._pano_win = win

        slab_mm = 4.0
        win.title(f"Panorâmica  ·  {arc_mm:.1f} mm  ·  slab {slab_mm:.0f} mm MIP")
        win.configure(bg=BG_DARK)

        Z, W    = arr.shape
        disp_w  = min(W * 2, 1200)
        disp_h  = min(int(Z * self.z_aspect * (disp_w / W)), 450)
        img     = Image.fromarray(arr, 'L').resize((disp_w, disp_h), RESAMPLE)
        photo   = ImageTk.PhotoImage(img)

        tk.Label(win, image=photo, bg="black").pack(padx=10, pady=10)
        win._photo = photo

        info = (f"Arco: {arc_mm:.1f} mm  ·  Slab MIP ±{slab_mm/2:.0f} mm  ·  "
                f"{W} amostras (comprimento de arco)")
        tk.Label(win, text=info, bg=BG_DARK, fg=LABEL_FG,
                 font=("Consolas", 9)).pack(pady=(0, 6))
        tk.Button(win, text="Salvar PNG",
                  command=lambda: self._save_png(img),
                  bg="#2d5a3d", fg="white", relief=tk.FLAT, padx=8
                  ).pack(pady=(0, 10))

    def _save_png(self, img: Image.Image):
        fp = filedialog.asksaveasfilename(defaultextension=".png",
                                          filetypes=[("PNG", "*.png")])
        if fp:
            img.save(fp)
            messagebox.showinfo("Salvo", fp)

    def _toggle_guides(self):
        self.show_guides = not self.show_guides
        if self.show_guides:
            self._btn_guides.config(bg="#2a4a3a", fg="#00ff88")
        else:
            self._btn_guides.config(bg="#3a2a2a", fg="#666666")
        if self.vol_norm is not None:
            self._render_all()

    def _export_individual_slices(self):
        """💾 Salva axial/coronal/sagital individualmente com timestamp.

        Destino: os.getcwd() (diretório de trabalho atual — onde o processo
        foi iniciado). Se quiser a pasta do script, use:
            os.path.dirname(os.path.abspath(sys.argv[0]))
        """
        from datetime import datetime

        if self.vol_norm is None:
            messagebox.showinfo("Exportar", "Nenhum volume carregado.")
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir   = os.getcwd()
        saved, errors = [], []

        for key, label in [('axial', 'axial'), ('coronal', 'coronal'),
                            ('sagittal', 'sagital')]:
            pil = self._panels[key].get('pil')
            if pil is None:
                errors.append(f"{label}: imagem não disponível"); continue
            fname = f"{label}_{timestamp}.png"
            fpath = os.path.join(out_dir, fname)
            if os.path.exists(fpath):
                errors.append(f"{fname}: já existe"); continue
            try:
                pil.save(fpath)
                saved.append(fname)
            except Exception as e:
                errors.append(f"{label}: {e}")

        if saved:
            msg = f"Salvo em {out_dir}:\n" + "\n".join(f"  • {s}" for s in saved)
            if errors:
                msg += "\n\nAvisos:\n" + "\n".join(f"  ⚠ {e}" for e in errors)
            messagebox.showinfo("Exportar", msg)
            self._status(f"✓ {len(saved)} imagens → {out_dir}", "#3a9e6a")
        else:
            messagebox.showwarning("Exportar",
                "Nenhuma imagem exportada.\n" + "\n".join(errors))

    def _canvas_to_voxel(self, mx, my, key):
        if self.vol_norm is None:
            return None
        Z, Y, X = self.vol_norm.shape
        rows, cols, nw, nh, px_col, px_row, ox, oy = self._panel_geom(key)

        if not (ox <= mx < ox + nw and oy <= my < oy + nh):
            return None

        col = int(np.clip((mx - ox) / px_col, 0, cols - 1))
        row = int(np.clip((my - oy) / px_row, 0, rows - 1))

        if key == 'axial':
            return {'focal_x': col, 'focal_y': row}
        if key == 'coronal':
            return {'focal_x': col, 'focal_z': Z - 1 - row}
        if key == 'sagittal':
            return {'focal_y': col, 'focal_z': Z - 1 - row}
        if key == 'mip':
            return {'focal_x': col, 'focal_z': Z - 1 - row}
        return None

    def _on_click(self, event, key):
        if self.vol_norm is None: return
        mx, my = event.x, event.y

        if self.interaction_mode == 'draw_curve':
            if key != 'axial': return
            vox = self._canvas_to_voxel(mx, my, 'axial')
            if vox is None: return
            vx, vy = float(vox['focal_x']), float(vox['focal_y'])
            if self.curve_points_axial:
                lx, ly = self.curve_points_axial[-1]
                if ((vx-lx)**2 + (vy-ly)**2) < 25:
                    return
            self.curve_points_axial.append((vx, vy))
            self._render_axial()
            return

        vox = self._canvas_to_voxel(mx, my, key)
        if vox is None: return
        for attr, val in vox.items():
            setattr(self, attr, val)
        self._render_all()

    def _step_view(self, key: str, delta: int):
        if self.vol_norm is None: return
        Z, Y, X = self.vol_norm.shape
        if key == 'axial':
            self.focal_z = max(0, min(Z - 1, self.focal_z + delta))
        elif key == 'coronal':
            self.focal_y = max(0, min(Y - 1, self.focal_y + delta))
        elif key == 'sagittal':
            self.focal_x = max(0, min(X - 1, self.focal_x + delta))
        elif key == 'mip':
            self.focal_z = max(0, min(Z - 1, self.focal_z + delta))
        self._render_all()

    def _on_scroll(self, event, key, delta=None):
        if delta is None:
            delta = -1 if event.delta > 0 else +1
        self._step_view(key, delta)

    def _on_panel_resize(self, event=None, key=None):
        self._schedule_render_all()

    def _schedule_render_all(self):
        """[patch-2] Debounce com self-clear via wrapper."""
        if self.vol_norm is None:
            return
        if self._resize_job is not None:
            try:
                self.after_cancel(self._resize_job)
            except tk.TclError:
                pass
        self._resize_job = self.after(80, self._run_scheduled_render_all)

    def _run_scheduled_render_all(self):
        """[patch-2] Zera o job antes de renderizar."""
        self._resize_job = None
        self._render_all()

    def _status(self, msg: str, color: str = "#888"):
        self._lbl_status.config(text=msg, fg=color)

if __name__ == "__main__":
    fp  = sys.argv[1] if len(sys.argv) > 1 else None
    app = TomoViewer(fp)
    app.mainloop()