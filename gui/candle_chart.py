import tkinter as tk
from core.calc import STOCK_NAMES, LOAD_GEARS, fmt_price

# ── Fonts (kept at 1.5× — user says graph fonts are fine) ───────────────────
_F_TITLE = ('Segoe UI', 17, 'bold')
_F_STAT  = ('Segoe UI', 14)
_F_DAY   = ('Segoe UI', 12)
_F_AXIS  = ('Segoe UI', 11)
_F_REF   = ('Segoe UI', 11)


class CandleChartWindow:
    """Popup window showing 5-day candle chart with reference lines."""

    def __init__(self, parent, ticker, ohlc_data, currency,
                 mode=None, load_gear=None,
                 avg_cost=0, buy_pct=5, sell_tiers=None,
                 current_price=None):
        self.win = tk.Toplevel(parent)
        name = STOCK_NAMES.get(ticker, ticker)
        suffix = '  (KR)' if ticker.endswith('.KS') else ''
        self.win.title(f"{name}{suffix} \u2014 5-Day Chart")
        self.win.geometry('900x600')
        self.win.resizable(True, True)
        self.ccy = currency
        self.mode = mode
        self.load_gear = load_gear
        self.avg_cost = avg_cost
        self.buy_pct = buy_pct
        self.sell_tiers = sell_tiers or []
        self.current_price = current_price

        if not ohlc_data:
            tk.Label(self.win, text="No data available",
                     font=_F_TITLE).pack(expand=True)
            return

        self.ohlc = ohlc_data

        # ── Stats header ─────────────────────────────────────────────────────
        stats = tk.Frame(self.win, padx=12, pady=8)
        stats.pack(fill='x')

        all_highs = [d['high'] for d in ohlc_data]
        all_lows  = [d['low']  for d in ohlc_data]
        max_high  = max(all_highs)
        min_low   = min(all_lows)
        vol = (max_high - min_low) / min_low * 100 if min_low > 0 else 0

        tk.Label(stats, text=name, font=_F_TITLE).pack(anchor='w')
        tk.Label(stats,
                 text=f"5-Day High: {fmt_price(max_high, currency)}    "
                      f"5-Day Low: {fmt_price(min_low, currency)}    "
                      f"Volatility: {vol:.2f}%",
                 font=_F_STAT).pack(anchor='w', pady=(4, 2))

        # ── Per-day detail (full words) ──────────────────────────────────────
        day_frame = tk.Frame(self.win, padx=12)
        day_frame.pack(fill='x')

        for d in ohlc_data:
            rng = d['high'] - d['low']
            pct = rng / d['low'] * 100 if d['low'] > 0 else 0
            up  = d['close'] >= d['open']
            clr = '#CC3333' if up else '#3366CC'
            txt = (f"{d['date']}:  Open={fmt_price(d['open'], currency)}  "
                   f"High={fmt_price(d['high'], currency)}  "
                   f"Low={fmt_price(d['low'], currency)}  "
                   f"Close={fmt_price(d['close'], currency)}  "
                   f"Range={pct:.1f}%")
            tk.Label(day_frame, text=txt, font=_F_DAY, fg=clr).pack(anchor='w')

        # ── Canvas ───────────────────────────────────────────────────────────
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

        has_refs = self.mode in ('empty', 'deployed')
        left_pad = 80
        right_pad = 130 if has_refs else 50
        top_pad = 20
        bottom_pad = 30

        # ── Collect all prices for Y-axis scaling ────────────────────────────
        all_prices = []
        for d in ohlc:
            all_prices.extend([d['high'], d['low']])

        if self.current_price and self.current_price > 0:
            all_prices.append(self.current_price)

        if self.mode == 'empty' and self.load_gear:
            max_h = max(d['high'] for d in ohlc)
            drop = LOAD_GEARS[self.load_gear]['drop']
            all_prices.append(max_h * (1 - drop))
        elif self.mode == 'deployed' and self.avg_cost > 0:
            all_prices.append(self.avg_cost)
            for active, pct in self.sell_tiers:
                if active:
                    all_prices.append(self.avg_cost * (1 + pct / 100))
            all_prices.append(self.avg_cost * (1 - self.buy_pct / 100))

        p_min = min(all_prices)
        p_max = max(all_prices)
        p_range = p_max - p_min
        if p_range <= 0:
            p_range = 1
        p_min -= p_range * 0.08
        p_max += p_range * 0.08
        p_range = p_max - p_min

        chart_w = w - left_pad - right_pad
        chart_h = h - top_pad - bottom_pad
        if chart_w < 50 or chart_h < 50:
            return
        candle_w = chart_w / n
        body_w = candle_w * 0.55

        def y_of(price):
            return top_pad + chart_h * (1 - (price - p_min) / p_range)

        def x_center(i):
            return left_pad + candle_w * (i + 0.5)

        # ── Grid lines ───────────────────────────────────────────────────────
        for i in range(5):
            price = p_min + p_range * i / 4
            y = y_of(price)
            c.create_line(left_pad, y, left_pad + chart_w, y, fill='#E8E8E8')
            c.create_text(left_pad - 4, y, text=fmt_price(price, self.ccy),
                          anchor='e', font=_F_AXIS, fill='#888')

        # ── Reference lines (drawn behind candles) ───────────────────────────
        if self.mode == 'empty' and self.load_gear:
            self._draw_empty_refs(c, left_pad, chart_w, w, y_of)
        elif self.mode == 'deployed' and self.avg_cost > 0:
            self._draw_deployed_refs(c, left_pad, chart_w, w, y_of)

        # ── Current price line (label on left) ──────────────────────────────
        if self.current_price and self.current_price > 0:
            y_cur = y_of(self.current_price)
            c.create_line(left_pad, y_cur, left_pad + chart_w, y_cur,
                          fill='#333333', width=1.5)
            c.create_text(left_pad - 2, y_cur,
                          text=f'{fmt_price(self.current_price, self.ccy)}',
                          anchor='e', font=_F_REF, fill='#333333')

        # ── Candles ──────────────────────────────────────────────────────────
        for i, d in enumerate(ohlc):
            x = x_center(i)
            y_h = y_of(d['high'])
            y_l = y_of(d['low'])
            y_o = y_of(d['open'])
            y_c = y_of(d['close'])

            # Wick
            c.create_line(x, y_h, x, y_l, fill='#555', width=1)

            # Body (red=up, blue=down)
            up = d['close'] >= d['open']
            color = '#CC3333' if up else '#3366CC'
            y_top = min(y_o, y_c)
            y_bot = max(y_o, y_c)
            if y_bot - y_top < 2:
                y_bot = y_top + 2
            c.create_rectangle(x - body_w / 2, y_top, x + body_w / 2, y_bot,
                               fill=color, outline='#444')

            # Date label
            c.create_text(x, h - 4, text=d['date'],
                          font=_F_AXIS, fill='#555', anchor='s')

    # ── Empty-stock reference lines ──────────────────────────────────────────
    def _draw_empty_refs(self, c, left_pad, chart_w, w, y_of):
        max_high = max(d['high'] for d in self.ohlc)
        min_low  = min(d['low']  for d in self.ohlc)
        drop = LOAD_GEARS[self.load_gear]['drop']
        load_price = max_high * (1 - drop)

        label_x = left_pad + chart_w + 5
        vc1_x = left_pad + chart_w - 8    # High–Low gap connector
        vc2_x = left_pad + chart_w - 28   # High–Load gap connector

        # 5D High line
        y_h = y_of(max_high)
        c.create_line(left_pad, y_h, left_pad + chart_w, y_h,
                      fill='#228B22', dash=(6, 3), width=1.5)
        c.create_text(label_x, y_h,
                      text=f'High: {fmt_price(max_high, self.ccy)}',
                      anchor='w', font=_F_REF, fill='#228B22')

        # 5D Low line
        y_l = y_of(min_low)
        c.create_line(left_pad, y_l, left_pad + chart_w, y_l,
                      fill='#DC143C', dash=(6, 3), width=1.5)
        c.create_text(label_x, y_l,
                      text=f'Low: {fmt_price(min_low, self.ccy)}',
                      anchor='w', font=_F_REF, fill='#DC143C')

        # Vertical connector High→Low with gap %
        c.create_line(vc1_x, y_h, vc1_x, y_l, fill='#555', width=1)
        gap = (max_high - min_low) / min_low * 100 if min_low > 0 else 0
        c.create_text(vc1_x - 4, (y_h + y_l) / 2,
                      text=f'{gap:.1f}%', anchor='e',
                      font=_F_REF, fill='#555')

        # Load target line
        y_load = y_of(load_price)
        c.create_line(left_pad, y_load, left_pad + chart_w, y_load,
                      fill='#FF8C00', dash=(6, 3), width=1.5)
        c.create_text(label_x, y_load,
                      text=f'Load: {fmt_price(load_price, self.ccy)}',
                      anchor='w', font=_F_REF, fill='#FF8C00')

        # Vertical connector High→Load with drop %
        c.create_line(vc2_x, y_h, vc2_x, y_load, fill='#FF8C00', width=1)
        c.create_text(vc2_x - 4, (y_h + y_load) / 2,
                      text=f'-{drop * 100:.0f}%', anchor='e',
                      font=_F_REF, fill='#FF8C00')

    # ── Deployed-stock reference lines ───────────────────────────────────────
    def _draw_deployed_refs(self, c, left_pad, chart_w, w, y_of):
        label_x = left_pad + chart_w + 5

        # Avg cost line
        y_avg = y_of(self.avg_cost)
        c.create_line(left_pad, y_avg, left_pad + chart_w, y_avg,
                      fill='#FF8C00', dash=(8, 4), width=2)
        c.create_text(label_x, y_avg,
                      text=f'Avg: {fmt_price(self.avg_cost, self.ccy)}',
                      anchor='w', font=_F_REF, fill='#FF8C00')

        # Sell tier lines (green shades)
        tier_colors = ['#22AA22', '#118811', '#006600']
        for idx, (active, pct) in enumerate(self.sell_tiers):
            if active:
                sell_p = self.avg_cost * (1 + pct / 100)
                y_sell = y_of(sell_p)
                clr = tier_colors[min(idx, len(tier_colors) - 1)]
                c.create_line(left_pad, y_sell, left_pad + chart_w, y_sell,
                              fill=clr, dash=(4, 3), width=1.5)
                c.create_text(label_x, y_sell,
                              text=f'+{pct}%: {fmt_price(sell_p, self.ccy)}',
                              anchor='w', font=_F_REF, fill=clr)

        # Rescue / buy trigger line
        rescue_p = self.avg_cost * (1 - self.buy_pct / 100)
        y_rescue = y_of(rescue_p)
        c.create_line(left_pad, y_rescue, left_pad + chart_w, y_rescue,
                      fill='#3366CC', dash=(4, 3), width=1.5)
        c.create_text(label_x, y_rescue,
                      text=f'-{self.buy_pct}%: {fmt_price(rescue_p, self.ccy)}',
                      anchor='w', font=_F_REF, fill='#3366CC')
