import tkinter as tk
from core.calc import STOCK_NAMES, fmt_price, calc_volatility

# ── Fonts (kept at 1.5× — user says graph fonts are fine) ───────────────────
_F_TITLE = ('Segoe UI', 17, 'bold')
_F_STAT  = ('Segoe UI', 14)
_F_DAY   = ('Segoe UI', 12)
_F_AXIS  = ('Segoe UI', 11)
_F_REF   = ('Segoe UI', 11)

# Buy cascade shades (descending) and sell tier shades (ascending)
_BUY_COLORS  = ['#3366CC', '#284E9E', '#1C3A75']
_SELL_COLORS = ['#22AA22', '#118811', '#006600']
_ANCHOR_CLR  = '#FF8C00'
_CUR_CLR     = '#222222'


class CandleChartWindow:
    """Popup 5-day candle chart with a unified reference overlay.

    The same drawing is used for empty and deployed stocks: an anchor line
    (avg cost for deployed, load price for empty), the buy ladder, the active
    sell tiers, and the current price. All reference labels — including the
    current price — sit on the right edge so they never collide with the
    y-axis price grid on the left."""

    def __init__(self, parent, ticker, ohlc_data, currency,
                 anchor_label='Avg', anchor_price=None,
                 buy_lines=None, sell_lines=None, current_price=None):
        self.win = tk.Toplevel(parent)
        name = STOCK_NAMES.get(ticker, ticker)
        suffix = '  (KR)' if ticker.endswith('.KS') else ''
        self.win.title(f"{name}{suffix} — 5-Day Chart")
        self.win.geometry('900x600')
        self.win.resizable(True, True)

        self.ccy           = currency
        self.anchor_label  = anchor_label
        self.anchor_price  = anchor_price
        # buy_lines / sell_lines: list of (label, price, qty); price may be None
        self.buy_lines     = [b for b in (buy_lines or []) if b[1] is not None]
        self.sell_lines    = [s for s in (sell_lines or []) if s[1] is not None]
        self.current_price = current_price

        if not ohlc_data:
            tk.Label(self.win, text="No data available",
                     font=_F_TITLE).pack(expand=True)
            return
        self.ohlc = ohlc_data

        # ── Stats header (5-day high/low/volatility live here as text) ────────
        stats = tk.Frame(self.win, padx=12, pady=8)
        stats.pack(fill='x')

        max_high = max(d['high'] for d in ohlc_data)
        min_low  = min(d['low']  for d in ohlc_data)
        vol = calc_volatility(max_high, min_low) or 0

        tk.Label(stats, text=name, font=_F_TITLE).pack(anchor='w')
        tk.Label(stats,
                 text=f"5-Day High: {fmt_price(max_high, currency)}    "
                      f"5-Day Low: {fmt_price(min_low, currency)}    "
                      f"Volatility: {vol:.2f}%",
                 font=_F_STAT).pack(anchor='w', pady=(4, 2))

        # ── Per-day detail ────────────────────────────────────────────────────
        day_frame = tk.Frame(self.win, padx=12)
        day_frame.pack(fill='x')
        for d in ohlc_data:
            rng = d['high'] - d['low']
            pct = rng / d['low'] * 100 if d['low'] > 0 else 0
            clr = '#CC3333' if d['close'] >= d['open'] else '#3366CC'
            tk.Label(day_frame,
                     text=(f"{d['date']}:  Open={fmt_price(d['open'], currency)}  "
                           f"High={fmt_price(d['high'], currency)}  "
                           f"Low={fmt_price(d['low'], currency)}  "
                           f"Close={fmt_price(d['close'], currency)}  "
                           f"Range={pct:.1f}%"),
                     font=_F_DAY, fg=clr).pack(anchor='w')

        # ── Canvas ────────────────────────────────────────────────────────────
        self.canvas = tk.Canvas(self.win, bg='white', highlightthickness=0)
        self.canvas.pack(fill='both', expand=True, padx=12, pady=(8, 12))
        self.canvas.bind('<Configure>', lambda e: self._draw())

    # ─────────────────────────────────────────────────────────────────────────
    def _draw(self):
        c = self.canvas
        c.delete('all')
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 120 or h < 80:
            return

        ohlc = self.ohlc
        n = len(ohlc)

        left_pad, right_pad, top_pad, bottom_pad = 70, 150, 20, 30

        # ── Y-axis range from candles + every reference price ─────────────────
        prices = []
        for d in ohlc:
            prices.extend([d['high'], d['low']])
        if self.current_price and self.current_price > 0:
            prices.append(self.current_price)
        if self.anchor_price:
            prices.append(self.anchor_price)
        for _, p, _ in self.buy_lines:
            prices.append(p)
        for _, p, _ in self.sell_lines:
            prices.append(p)

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

        def x_center(i):
            return left_pad + candle_w * (i + 0.5)

        # ── Grid lines + axis labels (left) ───────────────────────────────────
        for i in range(5):
            price = p_min + p_range * i / 4
            y = y_of(price)
            c.create_line(left_pad, y, left_pad + chart_w, y, fill='#E8E8E8')
            c.create_text(left_pad - 4, y, text=fmt_price(price, self.ccy),
                          anchor='e', font=_F_AXIS, fill='#888')

        # ── Reference lines (labels on the right) ─────────────────────────────
        def ref_line(price, color, text, width=1.5, dash=(4, 3)):
            y = y_of(price)
            c.create_line(left_pad, y, left_pad + chart_w, y,
                          fill=color, dash=dash, width=width)
            c.create_text(label_x, y, text=text, anchor='w',
                          font=_F_REF, fill=color)

        # Anchor (avg cost / load price)
        if self.anchor_price:
            ref_line(self.anchor_price, _ANCHOR_CLR,
                     f'{self.anchor_label}: {fmt_price(self.anchor_price, self.ccy)}',
                     width=2, dash=(8, 4))

        # Buy ladder
        for idx, (lbl, price, qty) in enumerate(self.buy_lines):
            clr = _BUY_COLORS[min(idx, len(_BUY_COLORS) - 1)]
            qty_txt = f' ×{qty}' if qty else ''
            ref_line(price, clr,
                     f'{lbl}: {fmt_price(price, self.ccy)}{qty_txt}')

        # Sell tiers
        for idx, (lbl, price, qty) in enumerate(self.sell_lines):
            clr = _SELL_COLORS[min(idx, len(_SELL_COLORS) - 1)]
            qty_txt = f' ×{qty}' if qty else ''
            ref_line(price, clr,
                     f'{lbl}: {fmt_price(price, self.ccy)}{qty_txt}')

        # Current price — solid line, label on the right
        if self.current_price and self.current_price > 0:
            y_cur = y_of(self.current_price)
            c.create_line(left_pad, y_cur, left_pad + chart_w, y_cur,
                          fill=_CUR_CLR, width=1.5)
            c.create_text(label_x, y_cur,
                          text=f'Now: {fmt_price(self.current_price, self.ccy)}',
                          anchor='w', font=_F_REF, fill=_CUR_CLR)

        # ── Candles ───────────────────────────────────────────────────────────
        for i, d in enumerate(ohlc):
            x = x_center(i)
            y_h, y_l = y_of(d['high']), y_of(d['low'])
            y_o, y_c = y_of(d['open']), y_of(d['close'])
            c.create_line(x, y_h, x, y_l, fill='#555', width=1)
            up = d['close'] >= d['open']
            color = '#CC3333' if up else '#3366CC'
            y_top, y_bot = min(y_o, y_c), max(y_o, y_c)
            if y_bot - y_top < 2:
                y_bot = y_top + 2
            c.create_rectangle(x - body_w / 2, y_top, x + body_w / 2, y_bot,
                               fill=color, outline='#444')
            c.create_text(x, h - 4, text=d['date'],
                          font=_F_AXIS, fill='#555', anchor='s')
