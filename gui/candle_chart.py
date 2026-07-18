"""Embeddable 5-day candle panel — the informational half of the Autopilot
window (the old standalone Graph popup is gone; same logic, one window).

Shows the 5-day candles with the V value and its auto gear, plus whatever
reference lines the caller passes (the watcher's live lines, avg/vantage,
current price). Purely informational: no order buttons live here — Buy /
Sell sit next to the live chart in the Autopilot window.
"""

import tkinter as tk

from core.calc import fmt_price, calc_volatility, select_auto_gear

_F_STAT = ('Segoe UI', 12, 'bold')
_F_DAY  = ('Segoe UI', 10)
_F_AXIS = ('Segoe UI', 10)
_F_REF  = ('Segoe UI', 10, 'bold')

_CUR_CLR = '#222222'


class CandlePanel(tk.Frame):
    """5-day candles + reference lines. Call update() with fresh data; the
    panel redraws itself (also on resize)."""

    def __init__(self, parent, currency, width=460):
        super().__init__(parent)
        self.ccy = currency
        self.ohlc = []
        self.ref_lines = []     # [{'label','price','color','dash','width'}]
        self.current = None

        self._stat = tk.Label(self, text='5-day chart — waiting for data',
                              font=_F_STAT, anchor='w')
        self._stat.pack(fill='x')
        self._days = tk.Label(self, text='', font=_F_DAY, fg='#555',
                              anchor='w', justify='left')
        self._days.pack(fill='x')
        self.canvas = tk.Canvas(self, bg='white', highlightthickness=0,
                                width=width)
        self.canvas.pack(fill='both', expand=True, pady=(4, 0))
        self.canvas.bind('<Configure>', lambda e: self._draw())

    # ── Data in ───────────────────────────────────────────────────────────────

    def update(self, ohlc=None, ref_lines=None, current=None):
        if ohlc is not None:
            self.ohlc = list(ohlc)
        if ref_lines is not None:
            self.ref_lines = [r for r in ref_lines if r.get('price')]
        self.current = current
        self._update_stats()
        self._draw()

    def _update_stats(self):
        if not self.ohlc:
            self._stat.config(text='5-day chart — no data')
            self._days.config(text='')
            return
        hi = max(d['high'] for d in self.ohlc)
        lo = min(d['low'] for d in self.ohlc)
        vol = calc_volatility(hi, lo) or 0.0
        self._stat.config(
            text=f'5D  High {fmt_price(hi, self.ccy)}   '
                 f'Low {fmt_price(lo, self.ccy)}   '
                 f'V {vol:.2f}% → G{select_auto_gear(vol)}')
        rows = []
        for d in self.ohlc:
            rng = (d['high'] - d['low']) / d['low'] * 100 if d['low'] else 0
            rows.append(f"{d['date']}  O {fmt_price(d['open'], self.ccy)}"
                        f"  H {fmt_price(d['high'], self.ccy)}"
                        f"  L {fmt_price(d['low'], self.ccy)}"
                        f"  C {fmt_price(d['close'], self.ccy)}"
                        f"  ({rng:.1f}%)")
        self._days.config(text='\n'.join(rows))

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

        left_pad, right_pad, top_pad, bottom_pad = 62, 140, 14, 26

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
            c.create_text(label_x, y, text=r['label'], anchor='w',
                          font=_F_REF, fill=r['color'])

        # Current price — solid
        if self.current and self.current > 0:
            y = y_of(self.current)
            c.create_line(left_pad, y, left_pad + chart_w, y,
                          fill=_CUR_CLR, width=1.5)
            c.create_text(label_x, y,
                          text=f'Now {fmt_price(self.current, self.ccy)}',
                          anchor='w', font=_F_REF, fill=_CUR_CLR)

        # Candles
        for i, d in enumerate(ohlc):
            x = left_pad + candle_w * (i + 0.5)
            c.create_line(x, y_of(d['high']), x, y_of(d['low']),
                          fill='#555', width=1)
            up = d['close'] >= d['open']
            color = '#CC3333' if up else '#3366CC'
            y_top = min(y_of(d['open']), y_of(d['close']))
            y_bot = max(y_of(d['open']), y_of(d['close']))
            if y_bot - y_top < 2:
                y_bot = y_top + 2
            c.create_rectangle(x - body_w / 2, y_top, x + body_w / 2, y_bot,
                               fill=color, outline='#444')
            c.create_text(x, h - 4, text=d['date'],
                          font=_F_AXIS, fill='#555', anchor='s')
