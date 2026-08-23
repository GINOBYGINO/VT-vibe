"""One-shot UI: tune 3-layer 9:16 layout (blur BG / sharp FG / subtitle).

IMPORTANT: preview source must be the *original* landscape VOD
(e.g. jobs/.../01_download/raw_video.mp4), NOT a finished short.
Loading a final mp4 double-composites and looks like two frames fighting.

Usage:
  .\\.venv\\Scripts\\python.exe scripts\\layout_tuner.py --video jobs\\<id>\\01_download\\raw_video.mp4 --t 1040
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from tkinter import (
    BOTH,
    LEFT,
    RIGHT,
    X,
    Y,
    BooleanVar,
    DoubleVar,
    IntVar,
    StringVar,
    Tk,
    filedialog,
    messagebox,
    ttk,
)
from tkinter import Canvas, Frame, Label

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageTk

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common.layout import (  # noqa: E402
    CONTENT_H_RATIO,
    OUT_H,
    OUT_W,
    SUBTITLE_BAR_H,
    SUBTITLE_Y_RATIO,
    content_height,
    content_top,
)

LAYOUT_PY = ROOT / "common" / "layout.py"
TUNE_JSON = ROOT / "configs" / "layout_tune.json"


def extract_frame(video: Path, t_sec: float, out_png: Path) -> None:
    from modules.subtitle.runner import find_ffmpeg

    ffmpeg = find_ffmpeg()
    cmd = [
        ffmpeg,
        "-y",
        "-ss",
        f"{max(0.0, t_sec):.3f}",
        "-i",
        str(video),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(out_png),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not out_png.is_file():
        raise RuntimeError(r.stderr[-800:] if r.stderr else "ffmpeg frame extract failed")


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("msjhbd.ttc", "msjh.ttc", "meiryo.ttc", "arial.ttf"):
        path = Path(r"C:\Windows\Fonts") / name
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _cover_resize(img: Image.Image, tw: int, th: int) -> Image.Image:
    """scale=force_original_aspect_ratio=increase + center crop."""
    sw, sh = img.size
    scale = max(tw / sw, th / sh)
    nw, nh = max(1, int(round(sw * scale))), max(1, int(round(sh * scale)))
    resized = img.resize((nw, nh), Image.Resampling.LANCZOS)
    left = (nw - tw) // 2
    top = (nh - th) // 2
    return resized.crop((left, top, left + tw, top + th))


def _fit_inside(img: Image.Image, tw: int, th: int) -> Image.Image:
    """scale=force_original_aspect_ratio=decrease (fit inside box)."""
    sw, sh = img.size
    scale = min(tw / sw, th / sh)
    nw, nh = max(1, int(round(sw * scale))), max(1, int(round(sh * scale)))
    return img.resize((nw, nh), Image.Resampling.LANCZOS)


def compose_preview(
    source: Image.Image,
    *,
    content_h_ratio: float,
    subtitle_y_ratio: float,
    subtitle_bar_h: int,
    show_guides: bool,
    show_bg: bool = True,
    show_fg: bool = True,
    show_sub: bool = True,
) -> Image.Image:
    """
    Match pipeline letterbox layers:
      L1 background — cover 9:16 + strong blur
      L2 main       — sharp fit-inside; bottom flush with subtitle top
      L3 subtitle   — translucent bar + sample 2-line text
    """
    canvas = Image.new("RGB", (OUT_W, OUT_H), (12, 12, 16))

    if show_bg:
        bg = _cover_resize(source, OUT_W, OUT_H)
        bg = bg.filter(ImageFilter.GaussianBlur(radius=28))
        dark = Image.new("RGBA", (OUT_W, OUT_H), (0, 0, 0, 70))
        canvas = Image.alpha_composite(bg.convert("RGBA"), dark).convert("RGB")
    else:
        canvas = Image.new("RGB", (OUT_W, OUT_H), (40, 40, 48))

    bar_y = int(OUT_H * subtitle_y_ratio)
    bar_h = max(40, int(subtitle_bar_h))
    # Same clamp + flush rule as common.layout
    requested = max(100, int(OUT_H * content_h_ratio))
    ch = min(requested, max(100, bar_y))
    cy = max(0, bar_y - ch)

    if show_fg:
        fg = _fit_inside(source, OUT_W, ch)
        fx = (OUT_W - fg.width) // 2
        # Bottom-flush actual pixels to subtitle top (same as edit overlay y=bar-h).
        fy = max(0, bar_y - fg.height)
        canvas.paste(fg, (fx, fy))
        cy_vis = fy
        ch_vis = fg.height
    else:
        cy_vis, ch_vis = cy, ch

    draw = ImageDraw.Draw(canvas, "RGBA")

    if show_guides:
        draw.rectangle(
            [0, cy_vis, OUT_W - 1, cy_vis + ch_vis - 1],
            outline=(80, 220, 120, 230),
            width=5,
        )
        # Flush seam between L2 bottom and L3 top
        draw.line([(0, bar_y), (OUT_W, bar_y)], fill=(255, 80, 80, 230), width=3)
        draw.text(
            (16, max(8, cy_vis - 36)),
            "L2 主畫面（底貼齊字幕頂）",
            font=_font(28),
            fill=(80, 220, 120),
        )

    if show_sub:
        draw.rectangle([0, bar_y, OUT_W, bar_y + bar_h], fill=(0, 0, 0, 150))
        if show_guides:
            draw.rectangle(
                [72, bar_y, OUT_W - 72, bar_y + bar_h - 1],
                outline=(255, 200, 60, 240),
                width=4,
            )
            draw.text(
                (16, min(OUT_H - 40, bar_y + bar_h + 8)),
                "L3 字幕帶（位置固定）",
                font=_font(28),
                fill=(255, 200, 60),
            )
        font = _font(64)
        sample = "第一行字幕示意文字\n第二行也要看得見"
        bbox = draw.multiline_textbbox((0, 0), sample, font=font, align="center", spacing=8)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        tx = (OUT_W - tw) // 2
        ty = bar_y + max(8, (bar_h - th) // 2)
        draw.multiline_text(
            (tx, ty),
            sample,
            font=font,
            fill=(255, 255, 255, 255),
            align="center",
            spacing=8,
        )

    if show_guides and show_bg:
        draw.text((16, 16), "L1 背景（放大模糊）", font=_font(30), fill=(160, 180, 255))

    hud = _font(26)
    draw.rectangle([0, OUT_H - 90, OUT_W, OUT_H], fill=(0, 0, 0, 160))
    draw.text(
        (16, OUT_H - 78),
        f"L2  ratio={content_h_ratio:.2f}→h={ch}  top={cy}  bottom={cy + ch}(=sub)",
        font=hud,
        fill=(120, 255, 160),
    )
    draw.text(
        (16, OUT_H - 42),
        f"L3  y_ratio={subtitle_y_ratio:.2f}  bar_h={bar_h}  "
        f"clip=y{bar_y}…{bar_y + bar_h}",
        font=hud,
        fill=(255, 210, 80),
    )
    return canvas.convert("RGB")


def _replace_assign(src: str, name: str, value: str) -> str:
    pat = rf"^({re.escape(name)}\s*=\s*).*$"
    new_src, n = re.subn(pat, rf"\g<1>{value}", src, count=1, flags=re.M)
    if n != 1:
        raise ValueError(f"could not find assignment for {name} in layout.py")
    return new_src


def write_layout_py(
    *,
    content_h_ratio: float,
    subtitle_y_ratio: float,
    subtitle_bar_h: int,
) -> None:
    text = LAYOUT_PY.read_text(encoding="utf-8")
    text = _replace_assign(text, "CONTENT_H_RATIO", f"{content_h_ratio:.2f}")
    text = _replace_assign(text, "SUBTITLE_Y_RATIO", f"{subtitle_y_ratio:.2f}")
    text = _replace_assign(text, "SUBTITLE_BAR_H", str(int(subtitle_bar_h)))
    offset = int(OUT_H * subtitle_y_ratio)
    text = _replace_assign(
        text,
        "SUBTITLE_BAR_OFFSET",
        f"int(OUT_H * SUBTITLE_Y_RATIO)  # {offset}",
    )
    LAYOUT_PY.write_text(text, encoding="utf-8")


def _looks_like_final_portrait(img: Image.Image) -> bool:
    w, h = img.size
    return h > w * 1.3  # already ~9:16


class LayoutTunerApp:
    def __init__(self, video: Path | None, t_sec: float) -> None:
        self.root = Tk()
        self.root.title("Layout Tuner — 字幕固定／主畫面底貼齊")
        self.root.geometry("1140x820")

        self.video_path = StringVar(value=str(video) if video else "")
        self.t_sec = DoubleVar(value=t_sec)
        self.content_h_ratio = DoubleVar(value=CONTENT_H_RATIO)
        self.subtitle_y_ratio = DoubleVar(value=SUBTITLE_Y_RATIO)
        self.subtitle_bar_h = IntVar(value=SUBTITLE_BAR_H)
        self.show_guides = BooleanVar(value=True)
        self.show_bg = BooleanVar(value=True)
        self.show_fg = BooleanVar(value=True)
        self.show_sub = BooleanVar(value=True)
        self.status = StringVar(
            value="L2 底邊自動貼齊 L3 頂邊；請用 raw_video"
        )

        self._base: Image.Image | None = None
        self._photo: ImageTk.PhotoImage | None = None
        self._tmp = Path(tempfile.mkdtemp(prefix="layout_tuner_"))
        self._refreshing = False

        self._build()
        if video and Path(video).is_file():
            self.load_frame()

    def _build(self) -> None:
        top = Frame(self.root)
        top.pack(fill=X, padx=8, pady=6)
        ttk.Entry(top, textvariable=self.video_path, width=70).pack(side=LEFT, padx=(0, 6))
        ttk.Button(top, text="選 raw…", command=self.pick_video).pack(side=LEFT, padx=2)
        ttk.Button(top, text="重抓畫面", command=self.load_frame).pack(side=LEFT, padx=2)
        Label(top, text="t(s)").pack(side=LEFT, padx=(10, 2))
        ttk.Entry(top, textvariable=self.t_sec, width=8).pack(side=LEFT)

        body = Frame(self.root)
        body.pack(fill=BOTH, expand=True, padx=8, pady=4)

        left = Frame(body)
        left.pack(side=LEFT, fill=BOTH, expand=True)
        self.canvas = Canvas(left, bg="#111", highlightthickness=0)
        self.canvas.pack(fill=BOTH, expand=True)

        right = Frame(body, width=360)
        right.pack(side=RIGHT, fill=Y, padx=(8, 0))
        right.pack_propagate(False)

        Label(right, text="三層開關", font=("", 10, "bold")).pack(anchor="w")
        ttk.Checkbutton(
            right, text="L1 背景（放大模糊）", variable=self.show_bg, command=self.refresh
        ).pack(anchor="w")
        ttk.Checkbutton(
            right, text="L2 主畫面（銳利）", variable=self.show_fg, command=self.refresh
        ).pack(anchor="w")
        ttk.Checkbutton(
            right, text="L3 字幕帶", variable=self.show_sub, command=self.refresh
        ).pack(anchor="w")
        ttk.Checkbutton(
            right,
            text="輔助框（綠=主畫面區／黃=字幕）",
            variable=self.show_guides,
            command=self.refresh,
        ).pack(anchor="w", pady=(0, 8))

        self._slider(
            right,
            "L2 高度 CONTENT_H_RATIO（超過字幕頂會自動裁到貼齊）",
            self.content_h_ratio,
            0.35,
            0.95,
        )
        Label(
            right,
            text="L2 垂直：鎖定（底邊 = 字幕頂）",
            fg="#066",
            wraplength=340,
            justify="left",
        ).pack(anchor="w", pady=(8, 0))
        self._slider(right, "L3 頂 SUBTITLE_Y_RATIO（固定位置）", self.subtitle_y_ratio, 0.35, 0.85)
        self._slider(right, "L3 高 SUBTITLE_BAR_H", self.subtitle_bar_h, 120, 480)

        ttk.Button(right, text="重設為目前 layout.py", command=self.reset_defaults).pack(
            fill=X, pady=(12, 2)
        )
        ttk.Button(right, text="複製常數到剪貼簿", command=self.copy_values).pack(fill=X, pady=2)
        ttk.Button(right, text="匯出 configs/layout_tune.json", command=self.export_json).pack(
            fill=X, pady=2
        )
        ttk.Button(right, text="寫入 common/layout.py", command=self.apply_layout).pack(
            fill=X, pady=8
        )

        Label(right, textvariable=self.status, wraplength=340, justify="left", fg="#333").pack(
            fill=X, pady=6
        )
        tip = (
            "紅線＝主畫面底／字幕頂貼齊縫。\n"
            "務必載入 jobs/.../01_download/raw_video.mp4。\n"
            "step4 已改為只讀 common/layout.py（忽略舊 job.letterbox_ratio）。\n"
            "寫入後重跑：pipeline --from-step 4"
        )
        Label(right, text=tip, wraplength=340, justify="left", fg="#666").pack(fill=X)

    def _slider(self, parent, label: str, var, frm, to) -> None:
        Label(parent, text=label, wraplength=340, justify="left").pack(anchor="w", pady=(10, 0))
        row = Frame(parent)
        row.pack(fill=X)
        ttk.Scale(
            row,
            from_=frm,
            to=to,
            variable=var,
            command=lambda _=None: self.refresh(),
        ).pack(side=LEFT, fill=X, expand=True)
        val = Label(row, width=6)
        val.pack(side=RIGHT)

        def _sync(*_a) -> None:
            try:
                if isinstance(var, IntVar):
                    val.config(text=str(int(round(float(var.get())))))
                else:
                    val.config(text=f"{float(var.get()):.2f}")
            except Exception:
                pass

        var.trace_add("write", _sync)
        _sync()

    def pick_video(self) -> None:
        path = filedialog.askopenfilename(
            title="選擇原始 raw_video（橫式）",
            initialdir=str(ROOT / "jobs"),
            filetypes=[("Video", "*.mp4;*.mkv;*.webm"), ("All", "*.*")],
        )
        if path:
            self.video_path.set(path)
            self.load_frame()

    def load_frame(self) -> None:
        path = Path(self.video_path.get().strip())
        if not path.is_file():
            messagebox.showerror("找不到影片", str(path))
            return
        out = self._tmp / "frame.png"
        try:
            extract_frame(path, float(self.t_sec.get()), out)
            img = Image.open(out).convert("RGB")
            self._base = img
            warn = ""
            if _looks_like_final_portrait(img):
                warn = " ⚠ 這像已是 9:16 成品，請改載 raw_video"
                messagebox.showwarning(
                    "來源可能不對",
                    "目前畫面是直式（像 short final）。\n"
                    "請改選 jobs/.../01_download/raw_video.mp4，\n"
                    "否則預覽會雙重合成、看起來像兩個畫面互相干擾。",
                )
            self.status.set(
                f"已載入 {path.name} ({img.width}×{img.height}) @ t={float(self.t_sec.get()):.1f}s{warn}"
            )
            self.refresh()
        except Exception as exc:
            messagebox.showerror("擷取畫面失敗", str(exc))

    def reset_defaults(self) -> None:
        from importlib import reload
        import common.layout as layout_mod

        reload(layout_mod)
        self.content_h_ratio.set(layout_mod.CONTENT_H_RATIO)
        self.subtitle_y_ratio.set(layout_mod.SUBTITLE_Y_RATIO)
        self.subtitle_bar_h.set(layout_mod.SUBTITLE_BAR_H)
        self.refresh()
        self.status.set("已重設為 layout.py 目前值")

    def _params(self) -> dict:
        return {
            "content_h_ratio": round(float(self.content_h_ratio.get()), 2),
            "subtitle_y_ratio": round(float(self.subtitle_y_ratio.get()), 2),
            "subtitle_bar_h": int(round(float(self.subtitle_bar_h.get()))),
            "out_w": OUT_W,
            "out_h": OUT_H,
        }

    def refresh(self) -> None:
        if self._base is None or self._refreshing:
            return
        self._refreshing = True
        try:
            p = self._params()
            composed = compose_preview(
                self._base,
                content_h_ratio=p["content_h_ratio"],
                subtitle_y_ratio=p["subtitle_y_ratio"],
                subtitle_bar_h=p["subtitle_bar_h"],
                show_guides=bool(self.show_guides.get()),
                show_bg=bool(self.show_bg.get()),
                show_fg=bool(self.show_fg.get()),
                show_sub=bool(self.show_sub.get()),
            )
            self.canvas.update_idletasks()
            max_h = max(320, self.canvas.winfo_height() or 720)
            max_w = max(180, self.canvas.winfo_width() or 405)
            scale = min(max_w / OUT_W, max_h / OUT_H, 1.0)
            tw, th = max(1, int(OUT_W * scale)), max(1, int(OUT_H * scale))
            shown = composed.resize((tw, th), Image.Resampling.LANCZOS)
            self._photo = ImageTk.PhotoImage(shown)
            self.canvas.delete("all")
            self.canvas.create_image(max_w // 2, max_h // 2, image=self._photo, anchor="center")
        finally:
            self._refreshing = False

    def copy_values(self) -> None:
        p = self._params()
        text = (
            f"CONTENT_H_RATIO = {p['content_h_ratio']}\n"
            f"SUBTITLE_Y_RATIO = {p['subtitle_y_ratio']}\n"
            f"SUBTITLE_BAR_H = {p['subtitle_bar_h']}\n"
            f"# main bottom flush with subtitle top\n"
        )
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status.set("已複製常數到剪貼簿")

    def export_json(self) -> None:
        p = self._params()
        TUNE_JSON.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            **p,
            "video": self.video_path.get(),
            "t_sec": float(self.t_sec.get()),
            "derived": {
                "content_h": content_height(p["content_h_ratio"]),
                "content_top": content_top(content_height(p["content_h_ratio"])),
                "subtitle_bar_top": int(OUT_H * p["subtitle_y_ratio"]),
                "flush": True,
            },
        }
        TUNE_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.status.set(f"已寫入 {TUNE_JSON.relative_to(ROOT)}")

    def apply_layout(self) -> None:
        p = self._params()
        if not messagebox.askyesno(
            "寫入 layout.py？",
            "會覆寫 common/layout.py 的 CONTENT_* / SUBTITLE_*。\n"
            "之後需重跑 --from-step 4 才會反映到影片。",
        ):
            return
        try:
            write_layout_py(
                content_h_ratio=p["content_h_ratio"],
                subtitle_y_ratio=p["subtitle_y_ratio"],
                subtitle_bar_h=p["subtitle_bar_h"],
            )
            self.export_json()
            self.status.set("已寫入 layout.py + layout_tune.json")
            messagebox.showinfo("完成", "已寫入 common/layout.py")
        except Exception as exc:
            messagebox.showerror("寫入失敗", str(exc))

    def run(self) -> None:
        def _on_cfg(event) -> None:
            if event.widget is self.root or event.widget is self.canvas:
                self.refresh()

        self.root.bind("<Configure>", _on_cfg)
        self.root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(description="One-shot 9:16 3-layer layout tuner")
    parser.add_argument(
        "--video",
        type=Path,
        required=True,
        help="Original landscape VOD (jobs/<id>/01_download/raw_video.mp4)",
    )
    parser.add_argument("--t", type=float, default=1040.0, help="seek seconds on raw VOD")
    args = parser.parse_args()
    video = args.video.expanduser().resolve()
    if not video.is_file():
        raise SystemExit(f"--video not found: {video}")
    LayoutTunerApp(video, args.t).run()


if __name__ == "__main__":
    main()
