import tkinter as tk
from tkinter import messagebox
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
                 buy_lines=None, sell_lines=None, current_price=None,
                 ordered_lines=None, order_actions=None):
        self.win = tk.Toplevel(parent)
        self.ticker = ticker
        # order_actions (Toss auto mode only): {ordered, pending, place, cancel,
        # refresh, lock_gear}. When present, Order/Cancel buttons are shown.
        self.order_actions = order_actions
        self._ordered_side = (order_actions or {}).get('ordered_side')
        self._ordered = self._ordered_side is not None
        name = STOCK_NAMES.get(ticker, ticker)
        suffix = '  (KR)' if ticker.endswith('.KS') else ''
        self.win.title(f"{name}{suffix} — 5-Day Chart")
        self.win.geometry('900x600')
        self.win.resizable(True, True)

        self.ccy           = currency
        self.anchor_label  = anchor_label
        self.anchor_price  = anchor_price
        # buy_lines / sell_lines: list of (label, price, qty); price may be None.
        # These are PROJECTIONS (drawn dotted).
        self.buy_lines     = [b for b in (buy_lines or []) if b[1] is not None]
        self.sell_lines    = [s for s in (sell_lines or []) if s[1] is not None]
        self.current_price = current_price
        # ordered_lines: real working orders on Toss, drawn DASHED:
        # list of {side, price, qty, status}.
        self.ordered_lines = [o for o in (ordered_lines or [])
                              if o.get('price')]

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

        title = name + ("   ● ordered on Toss" if self.ordered_lines else "")
        tk.Label(stats, text=title, font=_F_TITLE,
                 fg=('#0033AA' if self.ordered_lines else 'black')).pack(anchor='w')
        tk.Label(stats,
                 text=f"5-Day High: {fmt_price(max_high, currency)}    "
                      f"5-Day Low: {fmt_price(min_low, currency)}    "
                      f"Volatility: {vol:.2f}%",
                 font=_F_STAT).pack(anchor='w', pady=(4, 2))
        if self.ordered_lines:
            tk.Label(
                stats,
                text="Legend:  ···· projection      ──── ordered (live on Toss)",
                font=_F_REF, fg='#666').pack(anchor='w')

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

        # ── Order bar (Toss auto mode): Buy | Sell (deployed) | Cancel ────────
        if self.order_actions:
            bar = tk.Frame(self.win, padx=12, pady=4)
            bar.pack(fill='x')
            self._buy_btn = tk.Button(bar, text='Buy', font=_F_STAT, width=8,
                                      command=self._do_buy)
            self._buy_btn.pack(side='left', padx=(0, 6))
            self._sell_btn = None
            if self.order_actions.get('deployed'):
                self._sell_btn = tk.Button(bar, text='Sell', font=_F_STAT,
                                           width=8, command=self._do_sell)
                self._sell_btn.pack(side='left', padx=(0, 6))
            self._cancel_btn = tk.Button(bar, text='Cancel', font=_F_STAT,
                                         width=8, command=self._do_cancel)
            self._cancel_btn.pack(side='left', padx=(0, 12))
            self._order_status = tk.Label(bar, text='', font=_F_REF, fg='#333')
            self._order_status.pack(side='left')
            self._update_order_buttons()

        # ── Canvas ────────────────────────────────────────────────────────────
        self.canvas = tk.Canvas(self.win, bg='white', highlightthickness=0)
        self.canvas.pack(fill='both', expand=True, padx=12, pady=(8, 12))
        self.canvas.bind('<Configure>', lambda e: self._draw())

    # ── Order handlers (Buy / Sell / Cancel) ──────────────────────────────────
    def _update_order_buttons(self):
        os_ = self._ordered_side               # 'BUY' | 'SELL' | None
        self._buy_btn.config(state='normal' if os_ is None else 'disabled')
        if self._sell_btn:
            self._sell_btn.config(state='normal' if os_ is None else 'disabled')
        self._cancel_btn.config(state='normal' if os_ is not None else 'disabled')
        msg = {'BUY':  '● buy orders resting — gear locked',
               'SELL': '● sell orders resting — gear locked',
               None:   'no live orders — projection shown'}[os_]
        self._order_status.config(text=msg,
                                  fg=('#0033AA' if os_ else '#666'))

    def _do_buy(self):
        # Buy is laddered: pick which lines to send (default just the first —
        # Load/Buy 1), since funding all three at once is usually unaffordable.
        pend = self.order_actions.get('pending_buy') or []
        if not pend:
            messagebox.showinfo('Buy', 'No buy lines to send.', parent=self.win)
            return
        sel = self._select_buy_lines(pend)
        if not sel:                      # cancelled or nothing checked
            return
        ok, msg = self.order_actions['place_buy'](sel)
        self._after_place('BUY', ok, msg)

    def _do_sell(self):
        # Sell sends the active tiers together (no per-tier picker).
        pend = self.order_actions.get('pending_sell') or []
        if not pend:
            messagebox.showinfo('Sell', 'No sell lines to send.', parent=self.win)
            return
        lines = ['Send these REAL SELL orders to Toss?', ''] + \
                [f"{lbl}:   {q} @ {p}" for _, lbl, p, q in pend]
        if not self._confirm_dialog('Confirm sell', lines):
            return
        ok, msg = self.order_actions['place_sell']()
        self._after_place('SELL', ok, msg)

    def _after_place(self, side, ok, msg):
        self._order_status.config(text=msg, fg=('green' if ok else 'red'))
        if ok:
            self._ordered_side = side
            self._ordered = True
            self.ordered_lines = self.order_actions['refresh']()
            self.order_actions['set_state'](side)
            self._update_order_buttons()
            self._draw()

    def _do_cancel(self):
        if not self._confirm_dialog(
                'Confirm cancel',
                [f'Cancel ALL live Toss orders for {self.ticker}?']):
            return
        ok, msg = self.order_actions['cancel']()
        self._order_status.config(text=msg, fg=('green' if ok else 'red'))
        if ok:
            self._ordered_side = None
            self._ordered = False
            self.ordered_lines = []
            self.order_actions['set_state'](None)
            self._update_order_buttons()
            self._draw()

    # ── Shared centered Yes/No dialogs (consistent placement + button order) ───
    def _center_over(self, dlg):
        """Place the dialog centered over the chart window."""
        dlg.update_idletasks()
        try:
            px, py = self.win.winfo_rootx(), self.win.winfo_rooty()
            pw, ph = self.win.winfo_width(), self.win.winfo_height()
            w, h = dlg.winfo_width(), dlg.winfo_height()
            dlg.geometry(f'+{max(0, px + (pw - w) // 2)}+{max(0, py + (ph - h) // 2)}')
        except Exception:
            pass

    def _dialog_buttons(self, dlg, on_yes):
        """A centered [Yes] [No] row (same order everywhere)."""
        bar = tk.Frame(dlg)
        bar.pack(pady=12)
        tk.Button(bar, text='Yes', width=8,
                  command=lambda: (on_yes(), dlg.destroy())).pack(side='left', padx=6)
        tk.Button(bar, text='No', width=8,
                  command=dlg.destroy).pack(side='left', padx=6)

    def _confirm_dialog(self, title, lines) -> bool:
        """Centered Yes/No confirmation. Returns True on Yes."""
        dlg = tk.Toplevel(self.win)
        dlg.title(title)
        dlg.transient(self.win)
        for i, ln in enumerate(lines):
            tk.Label(dlg, text=ln, font=(_F_STAT if i == 0 else _F_DAY),
                     anchor='w').pack(anchor='w', padx=14,
                                      pady=(12 if i == 0 else 0, 0))
        res = {'ok': False}
        self._dialog_buttons(dlg, lambda: res.__setitem__('ok', True))
        dlg.grab_set()
        self._center_over(dlg)
        dlg.wait_window()
        return res['ok']

    def _select_buy_lines(self, pend):
        """Centered checkbox picker for the buy ladder (default only the first —
        Load / Buy 1). Returns selected indices or None."""
        dlg = tk.Toplevel(self.win)
        dlg.title('Confirm buy')
        dlg.transient(self.win)
        tk.Label(dlg, text='Send which BUY orders to Toss?',
                 font=_F_STAT).pack(anchor='w', padx=14, pady=(12, 6))
        bvars = []
        for i, (s, lbl, p, q) in enumerate(pend):
            v = tk.BooleanVar(value=(i == 0))
            tk.Checkbutton(dlg, variable=v, font=_F_DAY, anchor='w',
                           text=f"{lbl}:   {q} @ {p}").pack(anchor='w', padx=18)
            bvars.append(v)
        res = {'ok': False}
        self._dialog_buttons(dlg, lambda: res.__setitem__('ok', True))
        dlg.grab_set()
        self._center_over(dlg)
        dlg.wait_window()
        if not res['ok']:
            return None
        return [i for i, v in enumerate(bvars) if v.get()]

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
        for o in self.ordered_lines:
            prices.append(o['price'])

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
        def ref_line(price, color, text, width=1.5, dash=(2, 4)):
            y = y_of(price)
            c.create_line(left_pad, y, left_pad + chart_w, y,
                          fill=color, dash=dash, width=width)
            c.create_text(label_x, y, text=text, anchor='w',
                          font=_F_REF, fill=color)

        # Prices that already have a live order — projection lines at (about)
        # the same price are suppressed so only the solid "ordered" line shows.
        ordered_prices = [o['price'] for o in self.ordered_lines]

        def is_ordered(price):
            return any(abs(price - op) <= max(op * 0.0005, 0.01)
                       for op in ordered_prices)

        # Anchor (avg cost / load price) — dotted projection
        if self.anchor_price:
            ref_line(self.anchor_price, _ANCHOR_CLR,
                     f'{self.anchor_label}: {fmt_price(self.anchor_price, self.ccy)}',
                     width=2, dash=(2, 4))

        # Projection ladders (dotted) — both hidden once any orders are live, so
        # only the dashed ordered lines remain (no duplicates).
        if not self._ordered:
            for idx, (lbl, price, qty) in enumerate(self.buy_lines):
                if is_ordered(price):
                    continue
                clr = _BUY_COLORS[min(idx, len(_BUY_COLORS) - 1)]
                qty_txt = f' ×{qty}' if qty else ''
                ref_line(price, clr,
                         f'{lbl}: {fmt_price(price, self.ccy)}{qty_txt}')

            for idx, (lbl, price, qty) in enumerate(self.sell_lines):
                clr = _SELL_COLORS[min(idx, len(_SELL_COLORS) - 1)]
                qty_txt = f' ×{qty}' if qty else ''
                ref_line(price, clr,
                         f'{lbl}: {fmt_price(price, self.ccy)}{qty_txt}')

        # Live orders on Toss — drawn DASHED and bold (BUY blue / SELL green)
        for o in self.ordered_lines:
            clr = '#0033AA' if o.get('side') == 'BUY' else '#008800'
            qty_txt = f" ×{o['qty']}" if o.get('qty') else ''
            y = y_of(o['price'])
            c.create_line(left_pad, y, left_pad + chart_w, y,
                          fill=clr, dash=(8, 3), width=2.5)
            c.create_text(label_x, y,
                          text=f"ORDERED {o.get('side','')}: "
                               f"{fmt_price(o['price'], self.ccy)}{qty_txt}",
                          anchor='w', font=_F_REF, fill=clr)

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
