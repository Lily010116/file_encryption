import io
import os
import tkinter as tk
import tkinter.colorchooser as colorchooser
import zipfile
from datetime import datetime
from tkinter import ttk, messagebox, filedialog

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from PIL import Image, ImageTk, ImageFile, ImageDraw, ImageFilter

import crypto_utils

# 环境性能优化
Image.MAX_IMAGE_PIXELS = None
ImageFile.LOAD_TRUNCATED_IMAGES = True

try:
    import fitz  # PyMuPDF

    HAS_PDF = True
except ImportError:
    HAS_PDF = False


class SecureViewerApp:
    """
    [安全阅览器主程序]
    提供加密档案的内存级无痕阅览、轨迹打码、缩放、PDF 多页翻阅等功能。
    所有解密数据仅保留在内存中，关闭即物理销毁。
    """

    def __init__(self, root, zip_obj, order_no):
        self.root = root
        self.zip_obj = zip_obj
        self.order_no = order_no
        self.root.title(f"安全阅览器 - 加工单号: {order_no}")
        self.root.geometry("1450x980")

        # 核心交互状态
        self.current_pdf_doc = None  # 当前持有的 PDF 句柄 (fitz.Document)
        self.current_raw_data = None  # 当前文件的二进制流
        self.current_page_idx = 0  # (PDF) 当前页码
        self.total_pages = 0  # (PDF) 总页数
        self.zoom_ratio = 1.0  # (图片) 自定义缩放倍率

        self.setup_styles()
        self.main_container = tk.Frame(root, bg="#f1f5f9")
        self.main_container.pack(fill=tk.BOTH, expand=True)
        self.paned = ttk.PanedWindow(self.main_container, orient=tk.HORIZONTAL)
        self.paned.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

        # 1. 目录树
        self.tree_frame = tk.Frame(self.paned, bg="white")
        self.tree = ttk.Treeview(self.tree_frame, columns=("size"), show="tree headings")
        self.tree.heading("#0", text=" 📂 文档资源目录")
        self.tree.heading("size", text="文件大小")
        self.tree.column("#0", width=90)
        self.tree.column("size", width=30, anchor="e")
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ttk.Scrollbar(self.tree_frame, orient=tk.VERTICAL, command=self.tree.yview).pack(side=tk.RIGHT, fill=tk.Y)
        self.paned.add(self.tree_frame, weight=1)

        # 2. 预览看板
        self.view_panel = tk.Frame(self.paned, bg="#ffffff")
        self.paned.add(self.view_panel, weight=5)

        # PDF 缩略图侧边栏 (默认隐藏)
        self.pdf_thumb_frame = tk.Frame(self.view_panel, width=200, bg="#2d3748")
        self.pdf_thumb_canvas = tk.Canvas(self.pdf_thumb_frame, bg="#2d3748", width=180, highlightthickness=0)
        self.pdf_thumb_scrollbar = ttk.Scrollbar(self.pdf_thumb_frame, orient=tk.VERTICAL,
                                                 command=self.pdf_thumb_canvas.yview)
        self.pdf_thumb_inner = tk.Frame(self.pdf_thumb_canvas, bg="#2d3748")
        self.pdf_thumb_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.pdf_thumb_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.thumb_window_id = self.pdf_thumb_canvas.create_window((90, 0), window=self.pdf_thumb_inner, anchor="n")
        self.pdf_thumb_inner.bind("<Configure>", lambda e: self.pdf_thumb_canvas.configure(
            scrollregion=self.pdf_thumb_canvas.bbox("all")))
        self.pdf_thumb_canvas.bind("<Configure>",
                                   lambda e: self.pdf_thumb_canvas.coords(self.thumb_window_id, (e.width // 2, 0)))

        self.canvas_frame = tk.Frame(self.view_panel, bg="#1e293b")

        # PDF 滚动条 (默认隐藏)
        self.pdf_scrollbar = ttk.Scrollbar(self.canvas_frame, orient=tk.VERTICAL, command=self.on_pdf_scroll)
        self.pdf_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.pdf_scrollbar.pack_forget()

        self.canvas = tk.Canvas(self.canvas_frame, bg="#1e293b", highlightthickness=0, bd=0)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 图片小地图 (默认隐藏)
        self.minimap = tk.Canvas(self.canvas_frame, bg="#111", highlightthickness=1, highlightbackground="#297aff")

        def on_minimap_drag(e):
            if not hasattr(self, 'original_image') or getattr(self, 'minimap_w', 0) == 0: return
            new_cx = max(0.0, min(1.0, e.x / self.minimap_w))
            new_cy = max(0.0, min(1.0, e.y / self.minimap_h))
            self.img_cx = new_cx;
            self.img_cy = new_cy
            self.render_image_scaled(fast=True)
            if getattr(self, 'zoom_timer', None): self.root.after_cancel(self.zoom_timer)
            self.zoom_timer = self.root.after(150, lambda: self.render_image_scaled(fast=False))

        self.minimap.bind("<B1-Motion>", on_minimap_drag)
        self.minimap.bind("<Button-1>", on_minimap_drag)

        # 图片工具栏 (默认隐藏)
        self.img_toolbar = tk.Frame(self.canvas_frame, bg="#2d3748", bd=1, relief=tk.RAISED, padx=10, pady=10)

        # 三行布局
        row1 = tk.Frame(self.img_toolbar, bg="#2d3748")
        row1.pack(fill=tk.X, pady=(0, 6))
        row2 = tk.Frame(self.img_toolbar, bg="#2d3748")
        row2.pack(fill=tk.X, pady=(0, 6))
        row3 = tk.Frame(self.img_toolbar, bg="#2d3748")
        row3.pack(fill=tk.X)

        # --- Row 1: 缩放区 ---
        tk.Label(row1, text="🔍 缩放:", bg="#2d3748", fg="white").pack(side=tk.LEFT, padx=(0, 5))
        self.zoom_slider_val = tk.DoubleVar(value=1.0)

        def on_zoom_slider(v):
            if hasattr(self, 'zoom_ratio'):
                self.zoom_ratio = float(v)
                self.zoom_lbl.config(text=f"{int(self.zoom_ratio * 100)}%")
                self.render_image_scaled(fast=True)
                if getattr(self, 'zoom_timer', None): self.root.after_cancel(self.zoom_timer)
                self.zoom_timer = self.root.after(150, lambda: self.render_image_scaled(fast=False))

        self.zoom_slider = ttk.Scale(row1, from_=0.1, to=15.0, variable=self.zoom_slider_val, command=on_zoom_slider)
        self.zoom_slider.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        self.zoom_lbl = tk.Label(row1, text="100%", bg="#2d3748", fg="white", width=4)
        self.zoom_lbl.pack(side=tk.LEFT, padx=5)
        tk.Button(row1, text="还原", command=lambda: self.reset_image_view(), highlightbackground="#2d3748",
                  width=4).pack(side=tk.RIGHT)

        # --- Row 2: 绘制区 ---
        self.draw_mode_var = tk.StringVar(value="拖拽")
        tools = ["拖拽", "直线", "矩形", "圆形", "画笔", "橡皮擦", "框选打码", "涂画打码"]
        self.tool_combo = tk.OptionMenu(row2, self.draw_mode_var, *tools)
        self.tool_combo.config(width=8, bg="#2d3748")
        self.tool_combo.pack(side=tk.LEFT, padx=(0, 5))

        self.color_var = tk.StringVar(value="#ff0000")

        def choose_color(e=None):
            c = colorchooser.askcolor(title="选择颜色", color=self.color_var.get())
            if c[1]: self.color_var.set(c[1]); self.color_box.config(bg=c[1])

        self.color_box = tk.Label(row2, bg=self.color_var.get(), width=3, relief=tk.SUNKEN, cursor="hand2")
        self.color_box.bind("<Button-1>", choose_color)
        self.color_box.pack(side=tk.LEFT, padx=5)

        tk.Label(row2, text="粗细:", bg="#2d3748", fg="white").pack(side=tk.LEFT, padx=(5, 0))
        self.thick_var = tk.IntVar(value=5)
        ttk.Scale(row2, from_=1, to=50, variable=self.thick_var).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)

        tk.Label(row2, text="马赛克:", bg="#2d3748", fg="white").pack(side=tk.LEFT, padx=(5, 0))
        self.mosaic_var = tk.IntVar(value=20)
        ttk.Scale(row2, from_=5, to=50, variable=self.mosaic_var).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)

        # --- Row 3: 历史操作区 ---
        tk.Button(row3, text="↶ 撤销", command=lambda: self.undo_draw(), highlightbackground="#2d3748").pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        tk.Button(row3, text="↷ 重做", command=lambda: self.redo_draw(), highlightbackground="#2d3748").pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        tk.Button(row3, text="🗑️ 清除", command=lambda: self.clear_draw(), highlightbackground="#2d3748").pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=2)

        self.undo_stack = []
        self.redo_stack = []

        def get_real_coords(ex, ey):
            cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
            w, h = self.original_image.size
            scale = self.base_scale * self.zoom_ratio
            vx, vy = w * scale * self.img_cx, h * scale * self.img_cy
            return (ex - cw / 2 + vx) / scale, (ey - ch / 2 + vy) / scale

        def get_mode():
            mm = {"拖拽": "PAN", "直线": "LINE", "矩形": "RECT", "圆形": "OVAL",
                  "画笔": "BRUSH", "橡皮擦": "ERASER", "框选打码": "MOSAIC_BOX", "涂画打码": "MOSAIC_BRUSH"}
            return mm.get(self.draw_mode_var.get(), "PAN")

        def on_b1(e):
            if not hasattr(self, 'original_image') or self.current_pdf_doc: return self.canvas.scan_mark(e.x, e.y)
            mode = get_mode()
            if mode == "PAN":
                self.drag_x, self.drag_y = e.x, e.y
            else:
                self.draw_start_cx, self.draw_start_cy = e.x, e.y
                self.last_cx, self.last_cy = e.x, e.y
                self.draw_points = [get_real_coords(e.x, e.y)]
                self.is_drawing_active = True
                if mode == "ERASER":
                    self.undo_stack.append(
                        ((0, 0, self.original_image.width, self.original_image.height), self.original_image.copy()))
                    self.redo_stack.clear()

        def on_b1_motion(e):
            if not hasattr(self, 'original_image') or self.current_pdf_doc: return self.canvas.scan_dragto(e.x, e.y,
                                                                                                           gain=1)
            mode = get_mode()
            if mode == "PAN":
                dx, dy = e.x - self.drag_x, e.y - self.drag_y
                self.drag_x, self.drag_y = e.x, e.y
                w, h = self.original_image.size
                scale = self.base_scale * self.zoom_ratio
                self.img_cx -= dx / (w * scale)
                self.img_cy -= dy / (h * scale)
                self.render_image_scaled(fast=True)
                if getattr(self, 'zoom_timer', None): self.root.after_cancel(self.zoom_timer)
                self.zoom_timer = self.root.after(200, lambda: self.render_image_scaled(fast=False))
            elif getattr(self, 'is_drawing_active', False):
                self.draw_points.append(get_real_coords(e.x, e.y))
                col, user_screen_thk = self.color_var.get(), self.thick_var.get()
                scale = self.base_scale * self.zoom_ratio
                orig_thk = max(1, int(round(user_screen_thk / scale)))
                thk = max(1, int(round(orig_thk * scale)))

                cx, cy = e.x, e.y
                if mode == "LINE":
                    self.canvas.delete("preview_shape")
                    self.canvas.create_line(self.draw_start_cx, self.draw_start_cy, cx, cy, fill=col, width=thk,
                                            tags="preview_shape")
                elif mode in ["RECT", "MOSAIC_BOX"]:
                    self.canvas.delete("preview_shape")
                    self.canvas.create_rectangle(self.draw_start_cx, self.draw_start_cy, cx, cy,
                                                 outline=col if mode == "RECT" else "gray",
                                                 width=thk if mode == "RECT" else 2,
                                                 dash=() if mode == "RECT" else (4, 2), tags="preview_shape")
                elif mode == "OVAL":
                    self.canvas.delete("preview_shape")
                    self.canvas.create_oval(self.draw_start_cx, self.draw_start_cy, cx, cy, outline=col, width=thk,
                                            tags="preview_shape")
                elif mode == "BRUSH":
                    self.canvas.create_line(self.last_cx, self.last_cy, cx, cy, fill=col, width=thk, capstyle=tk.ROUND,
                                            tags="preview_shape_perm")
                elif mode == "MOSAIC_BRUSH":
                    self.canvas.create_line(self.last_cx, self.last_cy, cx, cy, fill="gray", width=thk,
                                            capstyle=tk.ROUND, tags="preview_shape_perm")
                elif mode == "ERASER":
                    r_orig = orig_thk / 2.0
                    r_screen = thk / 2.0
                    p1, p2 = self.draw_points[-2] if len(self.draw_points) > 1 else self.draw_points[-1], \
                    self.draw_points[-1]
                    box = (int(min(p1[0], p2[0]) - r_orig), int(min(p1[1], p2[1]) - r_orig),
                           int(max(p1[0], p2[0]) + r_orig + 1), int(max(p1[1], p2[1]) + r_orig + 1))
                    if 0 <= box[0] < self.clean_original_image.width and 0 <= box[1] < self.clean_original_image.height:
                        patch_mask = Image.new("L", (box[2] - box[0], box[3] - box[1]), 0)
                        d_mask = ImageDraw.Draw(patch_mask)
                        d_mask.line([(p1[0] - box[0], p1[1] - box[1]), (p2[0] - box[0], p2[1] - box[1])], fill=255,
                                    width=max(1, int(orig_thk)))
                        d_mask.rectangle([p1[0] - box[0] - r_orig, p1[1] - box[1] - r_orig, p1[0] - box[0] + r_orig,
                                          p1[1] - box[1] + r_orig], fill=255)
                        d_mask.rectangle([p2[0] - box[0] - r_orig, p2[1] - box[1] - r_orig, p2[0] - box[0] + r_orig,
                                          p2[1] - box[1] + r_orig], fill=255)
                        patch_clean = self.clean_original_image.crop(box)
                        self.original_image.paste(patch_clean, box, patch_mask)
                        if hasattr(self, 'proxy_image'):
                            proxy_scale = self.proxy_image.width / self.original_image.width
                            p_box = (int(box[0] * proxy_scale), int(box[1] * proxy_scale), int(box[2] * proxy_scale),
                                     int(box[3] * proxy_scale))
                            p_patch = patch_clean.resize((max(1, p_box[2] - p_box[0]), max(1, p_box[3] - p_box[1])),
                                                         Image.NEAREST)
                            p_mask = patch_mask.resize(p_patch.size, Image.NEAREST)
                            self.proxy_image.paste(p_patch, p_box, p_mask)
                    self.render_image_scaled(fast=False)
                    self.canvas.delete("eraser_cursor")
                    self.canvas.create_rectangle(cx - r_screen, cy - r_screen, cx + r_screen, cy + r_screen,
                                                 outline="#ff3333", width=2, tags="eraser_cursor")
                self.last_cx, self.last_cy = cx, cy

        def on_b1_release(e):
            if not getattr(self, 'is_drawing_active', False): return
            self.is_drawing_active = False
            self.canvas.delete("preview_shape");
            self.canvas.delete("preview_shape_perm");
            self.canvas.delete("eraser_cursor")
            mode = get_mode()
            if mode == "ERASER":
                self.update_proxy_image();
                self.render_image_scaled(fast=False)
                return
            if len(self.draw_points) < 2: return
            scale = self.base_scale * self.zoom_ratio
            screen_thk = self.thick_var.get()
            thk = max(1, int(round(screen_thk / scale)))
            xs, ys = [p[0] for p in self.draw_points], [p[1] for p in self.draw_points]
            box = (int(max(0, min(xs) - thk)), int(max(0, min(ys) - thk)),
                   int(min(self.original_image.width, max(xs) + thk)),
                   int(min(self.original_image.height, max(ys) + thk)))
            if box[2] <= box[0] or box[3] <= box[1]: return
            self.undo_stack.append((box, self.original_image.crop(box)))
            self.redo_stack.clear()
            draw = ImageDraw.Draw(self.original_image)
            col = self.color_var.get()
            p1, p2 = self.draw_points[0], self.draw_points[-1]
            if mode == "LINE":
                draw.line([p1, p2], fill=col, width=thk)
            elif mode == "RECT":
                ro = thk / 2.0
                draw.rectangle(
                    [min(p1[0], p2[0]) - ro, min(p1[1], p2[1]) - ro, max(p1[0], p2[0]) + ro, max(p1[1], p2[1]) + ro],
                    outline=col, width=thk)
            elif mode == "OVAL":
                ro = thk / 2.0
                draw.ellipse(
                    [min(p1[0], p2[0]) - ro, min(p1[1], p2[1]) - ro, max(p1[0], p2[0]) + ro, max(p1[1], p2[1]) + ro],
                    outline=col, width=thk)
            elif mode == "BRUSH":
                draw.line(self.draw_points, fill=col, width=thk, joint="curve")
                r_orig = thk / 2.0
                for p in self.draw_points: draw.ellipse([p[0] - r_orig, p[1] - r_orig, p[0] + r_orig, p[1] + r_orig],
                                                        fill=col)
            elif mode == "MOSAIC_BOX":
                inty = self.mosaic_var.get()
                patch = self.original_image.crop(box)
                pw, ph = max(1, patch.width // inty), max(1, patch.height // inty)
                self.original_image.paste(patch.resize((pw, ph), Image.NEAREST).resize(patch.size, Image.NEAREST), box)
            elif mode == "MOSAIC_BRUSH":
                inty = self.mosaic_var.get()
                mask = Image.new("L", self.original_image.size, 0)
                d_mask = ImageDraw.Draw(mask)
                d_mask.line(self.draw_points, fill=255, width=thk, joint="curve")
                r_orig = thk / 2.0
                for p in self.draw_points: d_mask.ellipse([p[0] - r_orig, p[1] - r_orig, p[0] + r_orig, p[1] + r_orig],
                                                          fill=255)
                patch = self.original_image.crop(box)
                pw, ph = max(1, patch.width // inty), max(1, patch.height // inty)
                self.original_image.paste(patch.resize((pw, ph), Image.NEAREST).resize(patch.size, Image.NEAREST), box,
                                          mask.crop(box))
            self.update_proxy_image();
            self.render_image_scaled(fast=False)

        self.canvas.bind("<MouseWheel>", self.on_mousewheel)
        self.canvas.bind("<Button-1>", on_b1)
        self.canvas.bind("<B1-Motion>", on_b1_motion)
        self.canvas.bind("<ButtonRelease-1>", on_b1_release)

        self.text_frame = tk.Frame(self.view_panel, bg="white")
        self.text_area = tk.Text(self.text_frame, wrap=tk.WORD, font=("Consolas", 12), borderwidth=0, padx=30, pady=30,
                                 bg="white", fg="#1e293b")
        self.text_area.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.canvas_frame.pack(fill=tk.BOTH, expand=True)
        self.load_tree_data()
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        tk.Label(root, text=" 🛡 受控档案：阅览轨迹加密物理销毁 | 💡 图片滚轮缩放左键拖拽 | PDF滚轮翻页", bg="#0f172a",
                 fg="#475569", height=2).pack(side=tk.BOTTOM, fill=tk.X)

    def setup_styles(self):
        style = ttk.Style();
        style.theme_use("clam")
        style.configure("Treeview", background="#ffffff", foreground="#1e293b", rowheight=38, font=("微软雅黑", 10),
                        borderwidth=0)
        style.map("Treeview", background=[('selected', '#eff6ff')], foreground=[('selected', '#2563eb')])
        style.configure("Treeview.Heading", background="#f8fafc", foreground="#64748b", font=("微软雅黑", 10, "bold"))

    def load_tree_data(self):
        folders = {}
        for info in sorted(self.zip_obj.infolist(), key=lambda x: x.filename):
            parts = info.filename.rstrip('/').split('/')
            parent = ""
            for i, part in enumerate(parts):
                path = '/'.join(parts[:i + 1])
                if path not in folders:
                    if i == len(parts) - 1 and not info.is_dir():
                        sz = f"{info.file_size / 1024 / 1024:.2f}MB"
                        node = self.tree.insert(parent, "end", text=f"  📄 {part}", values=(sz,), tags=("file",))
                        folders[path] = node
                    else:
                        node = self.tree.insert(parent, "end", text=f"  📁 {part}", open=True, tags=("folder",))
                        folders[path] = node
                parent = folders[path]

    def on_select(self, event):
        sel = self.tree.selection()
        if not sel: return
        node_id = sel[0];
        if self.tree.tag_has("folder", node_id): return
        p_list = [];
        curr = node_id
        while curr:
            p_list.insert(0, self.tree.item(curr, "text").strip()[2:])
            curr = self.tree.parent(curr)
        self.zoom_ratio = 1.0;
        self.switch_to_file("/".join(p_list))

    def update_proxy_image(self):
        w, h = self.original_image.size
        proxy_scale = min(1200.0 / max(w, 1), 1200.0 / max(h, 1), 1.0)
        self.proxy_image = self.original_image.resize((int(w * proxy_scale), int(h * proxy_scale)), Image.NEAREST)

    def undo_draw(self):
        if self.undo_stack:
            box, patch = self.undo_stack.pop()
            self.redo_stack.append((box, self.original_image.crop(box)))
            self.original_image.paste(patch, box)
            self.update_proxy_image();
            self.render_image_scaled(fast=False)

    def redo_draw(self):
        if self.redo_stack:
            box, patch = self.redo_stack.pop()
            self.undo_stack.append((box, self.original_image.crop(box)))
            self.original_image.paste(patch, box)
            self.update_proxy_image();
            self.render_image_scaled(fast=False)

    def clear_draw(self):
        if hasattr(self, 'clean_original_image'):
            self.undo_stack.append(
                ((0, 0, self.original_image.width, self.original_image.height), self.original_image.copy()))
            self.redo_stack.clear()
            self.original_image = self.clean_original_image.copy()
            self.update_proxy_image();
            self.render_image_scaled(fast=False)

    def reset_image_view(self):
        self.zoom_ratio, self.img_cx, self.img_cy = 1.0, 0.5, 0.5
        if hasattr(self, 'zoom_slider_val'): self.zoom_slider_val.set(1.0)
        if hasattr(self, 'zoom_lbl'): self.zoom_lbl.config(text="100%")
        if hasattr(self, 'original_image'): self.render_image_scaled(fast=False)

    def switch_to_file(self, fname):
        try:
            with self.zip_obj.open(fname) as f:
                self.current_raw_data = f.read()
            self.text_frame.pack_forget();
            self.canvas_frame.pack(fill=tk.BOTH, expand=True)
            self.pdf_scrollbar.pack_forget()
            if hasattr(self, 'minimap'): self.minimap.place_forget()
            if hasattr(self, 'pdf_thumb_frame'): self.pdf_thumb_frame.pack_forget()
            if hasattr(self, 'img_toolbar'): self.img_toolbar.place_forget()
            self.canvas.xview_moveto(0);
            self.canvas.yview_moveto(0);
            self.canvas.delete("all")
            ext = os.path.splitext(fname)[1].lower()
            if ext in ['.jpg', '.jpeg', '.png', '.bmp']:
                self.current_pdf_doc = None;
                self.root.update_idletasks();
                self.load_image()
            elif ext == '.pdf' and HAS_PDF:
                if hasattr(self, 'original_image'): del self.original_image
                self.pdf_thumb_frame.pack(side=tk.LEFT, fill=tk.Y, before=self.canvas_frame)
                self.pdf_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
                self.root.update_idletasks();
                self.load_pdf()
            else:
                self.canvas_frame.pack_forget();
                self.text_frame.pack(fill=tk.BOTH, expand=True)
                self.text_area.delete("1.0", tk.END);
                self.text_area.insert(tk.END, self.current_raw_data.decode('utf-8', errors='ignore'))
        except:
            pass

    def load_image(self):
        self.original_image = Image.open(io.BytesIO(self.current_raw_data))
        self.clean_original_image = self.original_image.copy()
        self.undo_stack, self.redo_stack = [], []
        w, h = self.original_image.size
        self.base_scale = min(1100.0 / max(w, 1), 850.0 / max(h, 1))
        self.reset_image_view();
        self.update_proxy_image()
        self.img_toolbar.place(relx=0.0, rely=1.0, anchor=tk.SW, x=10, y=-10, relwidth=1.0, width=-270)
        mw_max, mh_max = 240.0, 160.0
        ratio = min(mw_max / max(w, 1), mh_max / max(h, 1))
        self.minimap_w, self.minimap_h = int(w * ratio), int(h * ratio)
        if self.minimap_w > 0:
            mm_img = self.proxy_image.resize((self.minimap_w, self.minimap_h), Image.NEAREST)
            self.tk_minimap_img = ImageTk.PhotoImage(mm_img)
            self.minimap.config(width=self.minimap_w, height=self.minimap_h)
            self.minimap.delete("all");
            self.minimap.create_image(0, 0, anchor=tk.NW, image=self.tk_minimap_img)
            self.minimap.place(relx=1.0, rely=1.0, anchor=tk.SE, x=-20, y=-20)
        self.render_image_scaled(fast=False)

    def render_image_scaled(self, fast=False):
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        if cw < 10: cw, ch = 1100, 850
        w, h = self.original_image.size
        scale = self.base_scale * self.zoom_ratio
        vx, vy = w * scale * self.img_cx, h * scale * self.img_cy
        v_left, v_top = vx - cw / 2, vy - ch / 2
        crop_left, crop_right = max(0, v_left / scale), min(w, (vx + cw / 2) / scale)
        crop_top, crop_bottom = max(0, v_top / scale), min(h, (vy + ch / 2) / scale)
        if crop_right <= crop_left or crop_bottom <= crop_top: return
        draw_w, draw_h = max(1, int((crop_right - crop_left) * scale)), max(1, int((crop_bottom - crop_top) * scale))
        draw_x, draw_y = cw / 2 - vx + crop_left * scale, ch / 2 - vy + crop_top * scale
        if fast and hasattr(self, 'proxy_image'):
            pw, ph = self.proxy_image.size
            cropped = self.proxy_image.crop(
                (int(crop_left * pw / w), int(crop_top * ph / h), int(crop_right * pw / w), int(crop_bottom * ph / h)))
            img = cropped.resize((draw_w, draw_h), Image.NEAREST)
        else:
            cropped = self.original_image.crop((int(crop_left), int(crop_top), int(crop_right), int(crop_bottom)))
            img = cropped.resize((draw_w, draw_h), Image.NEAREST if self.zoom_ratio > 1.0 else Image.BILINEAR)
        self.tk_img = ImageTk.PhotoImage(img)
        self.canvas.delete("all");
        self.canvas.create_image(draw_x, draw_y, anchor=tk.NW, image=self.tk_img, tags="img")
        if getattr(self, 'minimap', None) and self.minimap.winfo_ismapped():
            self.minimap.delete("vp_box")
            self.minimap.create_rectangle((crop_left / w) * self.minimap_w, (crop_top / h) * self.minimap_h,
                                          (crop_right / w) * self.minimap_w, (crop_bottom / h) * self.minimap_h,
                                          outline="#297aff", width=2, tags="vp_box")

    def load_pdf(self):
        if not HAS_PDF: return
        self.current_pdf_doc = fitz.open(stream=self.current_raw_data, filetype="pdf")
        self.total_pages = len(self.current_pdf_doc);
        self.current_page_idx = 0
        self.thumb_images, self.thumb_labels = [], []
        for widget in self.pdf_thumb_inner.winfo_children(): widget.destroy()

        def render_thumb(idx):
            if idx >= self.total_pages or not getattr(self, 'current_pdf_doc', None): return
            page = self.current_pdf_doc.load_page(idx);
            pix = page.get_pixmap(matrix=fitz.Matrix(0.24, 0.24))
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples);
            tk_img = ImageTk.PhotoImage(img)
            self.thumb_images.append(tk_img)
            lbl = tk.Label(self.pdf_thumb_inner, image=tk_img, bg="#2d3748", bd=3, relief=tk.FLAT)
            lbl.pack(pady=4, padx=5);
            lbl.bind("<Button-1>", lambda e, cur=idx: (setattr(self, 'current_page_idx', cur), self.render_pdf()))
            self.thumb_labels.append(lbl)
            if idx == self.current_page_idx: lbl.config(bg="#297aff")
            self.root.after(5, lambda: render_thumb(idx + 1))

        render_thumb(0);
        self.render_pdf()

    def render_pdf(self):
        page = self.current_pdf_doc.load_page(self.current_page_idx)
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        if cw < 10: cw, ch = 1100, 850
        scale = min(cw / page.rect.width, ch / page.rect.height) * 0.95
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples);
        self.tk_img = ImageTk.PhotoImage(img)
        self.canvas.delete("all");
        self.canvas.create_image(cw // 2, ch // 2, image=self.tk_img, tags="img")
        self.pdf_scrollbar.set(self.current_page_idx / self.total_pages, (self.current_page_idx + 1) / self.total_pages)
        if hasattr(self, 'thumb_labels') and self.current_page_idx < len(self.thumb_labels):
            for i, lbl in enumerate(self.thumb_labels): lbl.config(
                bg="#297aff" if i == self.current_page_idx else "#2d3748")

    def on_pdf_scroll(self, action, *args):
        if not self.current_pdf_doc: return
        if action == "moveto":
            self.current_page_idx = int(float(args[0]) * self.total_pages)
        elif action == "scroll":
            self.current_page_idx += int(args[0])
        self.current_page_idx = max(0, min(self.total_pages - 1, self.current_page_idx));
        self.render_pdf()

    def on_mousewheel(self, event):
        is_up = event.delta > 0
        if self.current_pdf_doc:
            self.current_page_idx = max(0, min(self.total_pages - 1, self.current_page_idx + (-1 if is_up else 1)));
            self.render_pdf()
        elif hasattr(self, 'original_image'):
            cw, ch = (self.canvas.winfo_width() or 1100, self.canvas.winfo_height() or 850)
            scale = self.base_scale * self.zoom_ratio
            vx, vy = self.original_image.width * scale * self.img_cx, self.original_image.height * scale * self.img_cy
            v_px, v_py = event.x - cw / 2 + vx, event.y - ch / 2 + vy
            self.zoom_ratio = max(0.1, min(self.zoom_ratio * (1.15 if is_up else 0.85), 50.0))
            if hasattr(self, 'zoom_slider_val'): self.zoom_slider_val.set(self.zoom_ratio)
            if hasattr(self, 'zoom_lbl'): self.zoom_lbl.config(text=f"{int(self.zoom_ratio * 100)}%")
            new_scale = self.base_scale * self.zoom_ratio
            new_vx, new_vy = cw / 2 - event.x + v_px * (new_scale / scale), ch / 2 - event.y + v_py * (
                        new_scale / scale)
            self.img_cx, self.img_cy = new_vx / (self.original_image.width * new_scale), new_vy / (
                        self.original_image.height * new_scale)
            self.render_image_scaled(fast=True)
            if getattr(self, 'zoom_timer', None): self.root.after_cancel(self.zoom_timer)
            self.zoom_timer = self.root.after(150, lambda: self.render_image_scaled(fast=False))


def decrypt_core(p, l=None):
    try:
        local_macs = crypto_utils.get_local_macs()
        ak = None
        if l and l.strip():
            raw_key = crypto_utils.rsa_decrypt(l)
            if raw_key: ak = crypto_utils.derive_aes_key(raw_key)

        with open(p, "rb") as f:
            iv = f.read(16);
            f.read(4)  # Skip IV & Protocol Head

            # --- 性能优化：试错机制 ---
            # 仅读取前 4KB 用于验证秘钥，避免对大文件进行多次全量解密尝试
            test_chunk = f.read(4096)
            candidates = [ak] if ak else []
            for m in local_macs: candidates.append(crypto_utils.derive_aes_key(crypto_utils.norm(m)))

            target_ak = None
            for cand_ak in list(dict.fromkeys(candidates)):
                try:
                    # 使用当前候选秘钥测试解密头部
                    test_cipher = AES.new(cand_ak, AES.MODE_CBC, iv)
                    test_rb = test_cipher.decrypt(test_chunk)
                    # 验证元数据：前4字节应为订单号长度，通常是一个合理的正整数
                    olen_check = int.from_bytes(test_rb[0:4], 'big')
                    if 0 < olen_check < 100:
                        target_ak = cand_ak;
                        break  # 找到正确秘钥
                except:
                    continue

            if not target_ak: return None, "安全校验失败 (秘钥不匹配或非本机授权)。"

            # --- 秘钥确认后：全量解密 ---
            f.seek(16 + 4)  # 回到密文起始点
            ca = AES.new(target_ak, AES.MODE_CBC, iv)
            decrypted_data = unpad(ca.decrypt(f.read()), 16)

            rb = decrypted_data;
            off = 0
            olen = int.from_bytes(rb[off:off + 4], 'big');
            off += 4;
            order_no = rb[off:off + olen].decode('utf-8', errors='ignore');
            off += olen
            mlen = int.from_bytes(rb[off:off + 4], 'big');
            off += 4;
            bound_mac = rb[off:off + mlen].decode('utf-8', errors='ignore');
            off += mlen
            elen = int.from_bytes(rb[off:off + 4], 'big');
            off += 4;
            ev = rb[off:off + elen].decode('utf-8', errors='ignore')

            if bound_mac:
                if not any(crypto_utils.norm(bound_mac) == crypto_utils.norm(m) for m in local_macs):
                    return None, f"安全审查失败：本档案已被硬件锁定！"
            if ev:
                try:
                    if datetime.now() > datetime.strptime(ev, "%Y-%m-%d %H:%M:%S"):
                        return None, "授权已截止。"
                except:
                    pass

            return (order_no, rb[rb.find(b'PK\x03\x04'):]), None
    except Exception as e:
        return None, f"系统错误: {str(e)}"


def run_login():
    win = tk.Tk();
    win.title("档案安控中心");
    win.geometry("600x500");
    win.configure(bg="#ffffff")
    C_PRIM, C_BG, C_TEXT, C_ENTRY = "#297aff", "#ffffff", "#000000", "#f9fafc"
    head = tk.Frame(win, bg=C_PRIM, height=100);
    head.pack(fill=tk.X)
    tk.Label(head, text="🔒 档案安全阅览系统", bg=C_PRIM, fg="white", font=("微软雅黑", 22, "bold")).pack(pady=30)
    body = tk.Frame(win, bg=C_BG, padx=55, pady=30);
    body.pack(fill=tk.BOTH, expand=True)

    def pick_enc():
        f = filedialog.askopenfilename(filetypes=[("加密档案包", "*.enc")]);
        if f: ent_path.delete(0, tk.END); ent_path.insert(0, f)

    def pick_license():
        f = filedialog.askopenfilename(filetypes=[("授权密钥", "*.txt")]);
        if f:
            try:
                with open(f, "r", encoding="utf-8", errors='ignore') as file:
                    ent_license.delete(0, tk.END);
                    ent_license.insert(0, file.read().strip())
            except:
                pass

    tk.Label(body, text="加密档案路径 (.enc)", bg=C_BG, fg="#666666", font=("微软雅黑", 12)).pack(anchor="w")
    r1 = tk.Frame(body, bg=C_BG);
    r1.pack(fill=tk.X, pady=(10, 20))
    ent_path = tk.Entry(r1, bg=C_ENTRY, fg=C_TEXT, relief=tk.FLAT, highlightthickness=1, highlightbackground="#e2e8f0",
                        bd=0)
    ent_path.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=10)
    b1 = tk.Label(r1, text="浏览", bg="#e2e8f0", fg=C_TEXT, font=("微软雅黑", 12), padx=15, cursor="hand2")
    b1.pack(side=tk.LEFT, padx=5, fill=tk.Y);
    b1.bind("<Button-1>", lambda e: pick_enc())
    b1.bind("<Enter>", lambda e: b1.config(bg="#cbd5e1"));
    b1.bind("<Leave>", lambda e: b1.config(bg="#e2e8f0"))

    tk.Label(body, text="授权验证码 (密钥模式必填 / 绑定模式可不填)", bg=C_BG, fg="#666666",
             font=("微软雅黑", 12)).pack(anchor="w")
    r2 = tk.Frame(body, bg=C_BG);
    r2.pack(fill=tk.X, pady=(10, 30))
    ent_license = tk.Entry(r2, bg=C_ENTRY, fg=C_TEXT, relief=tk.FLAT, highlightthickness=1,
                           highlightbackground="#e2e8f0", bd=0)
    ent_license.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=10)
    b2 = tk.Label(r2, text="导入", bg="#e2e8f0", fg=C_TEXT, font=("微软雅黑", 12), padx=15, cursor="hand2")
    b2.pack(side=tk.LEFT, padx=5, fill=tk.Y);
    b2.bind("<Button-1>", lambda e: pick_license())
    b2.bind("<Enter>", lambda e: b2.config(bg="#cbd5e1"));
    b2.bind("<Leave>", lambda e: b2.config(bg="#e2e8f0"))

    def on_sub(e=None):
        if not ent_path.get().strip(): return
        res, err = decrypt_core(ent_path.get().strip(), ent_license.get().strip())
        if err:
            messagebox.showerror("开启失败", err)
        else:
            win.destroy(); mwin = tk.Tk(); SecureViewerApp(mwin, zipfile.ZipFile(io.BytesIO(res[1])),
                                                           res[0]); mwin.mainloop()

    btn_sub = tk.Label(body, text="立 即 开 启 安 全 阅 览", bg=C_PRIM, fg="white", font=("微软雅黑", 14, "bold"),
                       pady=15, cursor="hand2")
    btn_sub.pack(fill=tk.X, pady=10);
    btn_sub.bind("<Button-1>", on_sub)
    btn_sub.bind("<Enter>", lambda e: btn_sub.config(bg="#1d66d8"));
    btn_sub.bind("<Leave>", lambda e: btn_sub.config(bg=C_PRIM))

    ft = tk.Frame(win, bg=C_BG);
    ft.pack(side=tk.BOTTOM, fill=tk.X, pady=(0, 25))
    tk.Label(ft, text="* 本产品仅用于地质资料借阅订单数据包解密，禁止用于其他用途！", bg=C_BG, fg="#94a3b8",
             font=("微软雅黑", 9)).pack(pady=(5, 0))
    win.mainloop()


if __name__ == "__main__": run_login()
