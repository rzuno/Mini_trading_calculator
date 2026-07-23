"""Embeddable 5-day candle panel — the informational half of the Autopilot
window (the old standalone Graph popup is gone; same logic, one window).

Shows the 5-day candles with the average COMPLETED-day V (and its /3 grid
hint — the scale-picking indicator), plus whatever reference lines the
caller passes (the watcher's grid rows, current price). Purely
informational: no order buttons live here.
"""

import tkinter as tk
import tkinter.font as tkfont

from core.calc import fmt_price


def avg_bar_day_v(bars):
    """Fallback avg day range ((H−L)/L %) over the given bars — used only
    when the watcher has not supplied the completed-days average yet."""
    vs = [(b['high'] - b['low']) / b['low'] * 100.0
          for b in (bars or []) if b.get('low')]
    return (sum(vs) / len(vs)) if vs else None

_F_STAT = ('Segoe UI', 12, 'bold')
_F_DAY  = ('Segoe UI', 10)
_F_AXIS = ('Segoe UI', 10)
_F_REF  = ('Segoe UI', 10, 'bold')

_CUR_CLR = '#222222'
_UP_CLR = '#CC3333'
_DOWN_CLR = '#3366CC'


def candle_color(day):
    """Use the same direction color for an OHLC row and its candle body."""
    return _UP_CLR if day['close'] >= day['open'] else _DOWN_CLR


def required_label_pad(labels, measure, minimum, padding=12):
    """Right-side canvas space needed to show every label without clipping.

    ``measure`` is injected so the sizing rule stays easy to test without a
    Tk display (at runtime it is ``tkinter.font.Font.measure``).
    """
    widths = [measure(text) for text in labels if text]
    return max(minimum, (max(widths) if widths else 0) + padding)


def bounded_label_layout(canvas_width, text_width, inset=6):
    """Keep a right-edge label horizontally inside a narrow canvas.

    A label that fits is right-anchored.  If the complete text is wider than
    the canvas, return a bounded wrap width instead of letting either edge be
    clipped.  Normal-width charts do not need this fallback.
    """
    canvas_width = max(1, int(canvas_width))
    inset = min(max(0, int(inset)), canvas_width // 2)
    usable = max(1, canvas_width - inset * 2)
    if text_width <= usable:
        return canvas_width - inset, 'e', None
    return inset, 'w', usable


class CandlePanel(tk.Frame):
    """5-day candles + reference lines. Call update() with fresh data; the
    panel redraws itself (also on resize)."""

    def __init__(self, parent, currency, width=460):
        super().__init__(parent)
        self.ccy = currency
        self.ohlc = []
        self.ref_lines = []     # [{'label','price','color','dash','width'}]
        self.current = None
        self.day_v_avg = None   # completed-days avg V from the watcher
        self._ref_font = tkfont.Font(root=self, font=_F_REF)
        self._row_wrap = max(100, width - 10)

        self._stat = tk.Label(self, text='5-day chart — waiting for data',
                              font=_F_STAT, anchor='w')
        self._stat.pack(fill='x')
        self._days = tk.Frame(self)
        self._days.pack(fill='x')
        self.canvas = tk.Canvas(self, bg='white', highlightthickness=0,
                                width=width)
        self.canvas.pack(fill='both', expand=True, pady=(4, 0))
        self.canvas.bind('<Configure>', lambda e: self._draw())
        self.bind('<Configure>', self._resize_day_rows)

    def _resize_day_rows(self, event):
        """Keep every colored OHLC row inside the panel as it is resized."""
        self._row_wrap = max(100, event.width - 10)
        for child in self._days.winfo_children():
            child.config(wraplength=self._row_wrap)

    # ── Data in ───────────────────────────────────────────────────────────────

    def update(self, ohlc=None, ref_lines=None, current=None,
               day_v_avg=None):
        if ohlc is not None:
            self.ohlc = list(ohlc)
        if ref_lines is not None:
            self.ref_lines = [r for r in ref_lines if r.get('price')]
        self.current = current
        self.day_v_avg = day_v_avg
        self._update_stats()
        self._draw()

    def _update_stats(self):
        if not self.ohlc:
            self._stat.config(text='5-day chart — no data')
            for child in self._days.winfo_children():
                child.destroy()
            return
        hi = max(d['high'] for d in self.ohlc)
        lo = min(d['low'] for d in self.ohlc)
        # Scale indicator: mean of the past 5 COMPLETED days' ranges (from
        # the watcher; today's growing bar excluded) and its /3 grid hint.
        v = self.day_v_avg
        if v is None:
            v = avg_bar_day_v(self.ohlc) or 0.0
        self._stat.config(
            text=f'5D  High {fmt_price(hi, self.ccy)}   '
                 f'Low {fmt_price(lo, self.ccy)}   '
                 f'avg day V {v:.1f}% → /3 = {v / 3:.1f}% grid')
        for child in self._days.winfo_children():
            child.destroy()
        for d in self.ohlc:
            rng = (d['high'] - d['low']) / d['low'] * 100 if d['low'] else 0
            row = (f"{d['date']}  O {fmt_price(d['open'], self.ccy)}"
                   f"  H {fmt_price(d['high'], self.ccy)}"
                   f"  L {fmt_price(d['low'], self.ccy)}"
                   f"  C {fmt_price(d['close'], self.ccy)}"
                   f"  ({rng:.1f}%)")
            tk.Label(self._days, text=row, font=_F_DAY,
                     fg=candle_color(d), anchor='w', justify='left',
                     wraplength=self._row_wrap
                     ).pack(fill='x', anchor='w')

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _draw(self):
        c = self.canvas
        c.delete('all')
        w, h = c.winfo_width(), c.winfo_height()
        if w < 120 or h < 80:
            return
        ohlc = self.ohlc
        if not ohlc:
            c.create_text(w / 2, h / 2, text='no candle data',
                          font=_F_DAY, fill='#888')
            return
        n = len(ohlc)

        left_pad, top_pad, bottom_pad = 62, 14, 26
        label_texts = [r['label'] for r in self.ref_lines]
        if self.current and self.current > 0:
            label_texts.append(f'Now {fmt_price(self.current, self.ccy)}')
        wanted_right = required_label_pad(
            label_texts, self._ref_font.measure, minimum=140, padding=14)
        max_right = max(90, w - left_pad - 80)
        right_pad = min(wanted_right, max_right)
        narrow_labels = wanted_right > max_right

        prices = []
        for d in ohlc:
            prices.extend([d['high'], d['low']])
        for r in self.ref_lines:
            prices.append(r['price'])
        if self.current and self.current > 0:
            prices.append(self.current)

        p_min, p_max = min(prices), max(prices)
        p_range = (p_max - p_min) or 1
        p_min -= p_range * 0.08
        p_max += p_range * 0.08
        p_range = p_max - p_min

        chart_w = w - left_pad - right_pad
        chart_h = h - top_pad - bottom_pad
        if chart_w < 50 or chart_h < 50:
            return
        candle_w = chart_w / n
        body_w = candle_w * 0.55
        label_x = left_pad + chart_w + 5

        def y_of(price):
            return top_pad + chart_h * (1 - (price - p_min) / p_range)

        # Grid + left axis
        for i in range(5):
            price = p_min + p_range * i / 4
            y = y_of(price)
            c.create_line(left_pad, y, left_pad + chart_w, y, fill='#E8E8E8')
            c.create_text(left_pad - 4, y, text=fmt_price(price, self.ccy),
                          anchor='e', font=_F_AXIS, fill='#888')

        # Reference lines (labels on the right)
        for r in self.ref_lines:
            y = y_of(r['price'])
            c.create_line(left_pad, y, left_pad + chart_w, y,
                          fill=r['color'], dash=r.get('dash', (2, 4)),
                          width=r.get('width', 1.5))
            text_x, anchor, wrap_width = label_x, 'w', None
            if narrow_labels:
                text_x, anchor, wrap_width = bounded_label_layout(
                    w, self._ref_font.measure(r['label']))
            options = {'text': r['label'], 'anchor': anchor,
                       'font': _F_REF, 'fill': r['color']}
            if wrap_width is not None:
                options['width'] = wrap_width
            c.create_text(text_x, y, **options)

        # Current price — solid
        if self.current and self.current > 0:
            y = y_of(self.current)
            c.create_line(left_pad, y, left_pad + chart_w, y,
                          fill=_CUR_CLR, width=1.5)
            now_text = f'Now {fmt_price(self.current, self.ccy)}'
            text_x, anchor, wrap_width = label_x, 'w', None
            if narrow_labels:
                text_x, anchor, wrap_width = bounded_label_layout(
                    w, self._ref_font.measure(now_text))
            options = {'text': now_text, 'anchor': anchor,
                       'font': _F_REF, 'fill': _CUR_CLR}
            if wrap_width is not None:
                options['width'] = wrap_width
            c.create_text(text_x, y, **options)

        # Candles
        for i, d in enumerate(ohlc):
            x = left_pad + candle_w * (i + 0.5)
            c.create_line(x, y_of(d['high']), x, y_of(d['low']),
                          fill='#555', width=1)
            color = candle_color(d)
            y_top = min(y_of(d['open']), y_of(d['close']))
            y_bot = max(y_of(d['open']), y_of(d['close']))
            if y_bot - y_top < 2:
                y_bot = y_top + 2
            c.create_rectangle(x - body_w / 2, y_top, x + body_w / 2, y_bot,
                               fill=color, outline='#444')
            c.create_text(x, h - 4, text=d['date'],
                          font=_F_AXIS, fill='#555', anchor='s')
