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
_DOWN_SIGN_FG = '#3366CC'
_UP_SIGN_FG   = '#CC3333'
_STATUS_FG    = '#4B0082'  # indigo

# Daily 443 autopilot line colors (solid, distinct from projections/orders)
_AP_COLORS = {'load': '#E08000', 'chase': '#7E3FBF', 'sell': '#007700'}
_AP_ON_BG  = '#2E8B57'
_AP_LIVE_BG = '#CC0000'
_AP_DRY_BG = '#E08000'


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
                 ordered_lines=None, order_actions=None, autopilot=None):
        self.win = tk.Toplevel(parent)
        self.ticker = ticker
        # order_actions (Toss auto mode only): {ordered, pending, place, cancel,
        # refresh, lock_gear}. When present, Order/Cancel buttons are shown.
        self.order_actions = order_actions
        # autopilot: graph_context() from AutopilotController (Toss auto only).
        self.autopilot = autopilot
        self.ap_lines = {}          # {'load'|'chase'|'sell': (price, qty)}
        self._ap_cb = None
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

        # Trigger status: which baits the current price has crossed. Sell only
        # counts for deployed (anchor 'Avg'); empty sells are pseudo projections.
        cur = self.current_price
        buy_hits = [lbl for (lbl, p, q) in self.buy_lines
                    if cur and p and cur <= p]
        sell_hits = ([lbl for (lbl, p, q) in self.sell_lines
                      if cur and p and cur >= p]
                     if self.anchor_label == 'Avg' else [])

        def trigger_status(sign, sign_fg, text):
            row = tk.Frame(stats)
            row.pack(anchor='w')
            tk.Label(row, text=sign, font=_F_STAT,
                     fg=sign_fg).pack(side='left')
            tk.Label(row, text=' ' + text, font=_F_STAT,
                     fg=_STATUS_FG).pack(side='left')

        if buy_hits:
            trigger_status('\u25bc', _DOWN_SIGN_FG,
                           'Buy triggered: ' + ', '.join(buy_hits))
        elif sell_hits:
            trigger_status('\u25b2', _UP_SIGN_FG,
                           'Sell triggered: ' + ', '.join(sell_hits))

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

        # ── Daily 443 autopilot panel (Toss auto mode) ────────────────────────
        if self.autopilot:
            self._build_ap_panel()

        # ── Canvas ────────────────────────────────────────────────────────────
        self.canvas = tk.Canvas(self.win, bg='white', highlightthickness=0)
        self.canvas.pack(fill='both', expand=True, padx=12, pady=(8, 12))
        self.canvas.bind('<Configure>', lambda e: self._draw())

    # ── Daily 443 autopilot panel ─────────────────────────────────────────────

    def _build_ap_panel(self):
        ap = tk.Frame(self.win, padx=12, pady=4, bd=1, relief='groove')
        ap.pack(fill='x', padx=12)
        tk.Label(ap, text='443 Autopilot', font=_F_STAT,
                 fg=_STATUS_FG).grid(row=0, column=0, sticky='w')
        self._ap_toggle = tk.Button(ap, text='OFF', font=_F_STAT, width=6,
                                    command=self._ap_on_toggle)
        self._ap_toggle.grid(row=0, column=1, padx=(10, 6))
        self._ap_default_bg = self._ap_toggle.cget('bg')
        self._ap_mode_btn = tk.Button(ap, text='DRY RUN', font=_F_STAT,
                                      width=9, state='disabled',
                                      command=self._ap_on_mode)
        self._ap_mode_btn.grid(row=0, column=2, padx=(0, 6))
        self._ap_daily_btn = tk.Button(ap, text='Daily ▸', font=_F_STAT,
                                       width=8, state='disabled',
                                       command=self._ap_open_daily)
        self._ap_daily_btn.grid(row=0, column=3, padx=(0, 12))
        self._ap_state_lbl = tk.Label(ap, text='off', font=_F_STAT,
                                      fg='#666', anchor='w')
        self._ap_state_lbl.grid(row=0, column=4, sticky='w')
        ap.grid_columnconfigure(4, weight=1)
        self._ap_lines_lbl = tk.Label(ap, text='', font=_F_REF, fg='#333',
                                      anchor='w', justify='left')
        self._ap_lines_lbl.grid(row=1, column=0, columnspan=5, sticky='w',
                                pady=(2, 0))

        # Live updates from the controller; detach when the window closes.
        self._ap_cb = self._ap_update
        self.autopilot['subscribe'](self._ap_cb)
        self.win.bind('<Destroy>', self._ap_on_destroy)
        self._ap_update(self.autopilot['ui_state']())

    def _ap_on_destroy(self, event):
        if event.widget is self.win and self._ap_cb:
            self.autopilot['unsubscribe'](self._ap_cb)
            self._ap_cb = None

    def _ap_on_toggle(self):
        if self.autopilot['is_enabled']():
            if self._confirm_dialog(
                    'Turn autopilot off',
                    [f'Stop autopiloting {self.ticker}?',
                     'Resting orders are left as they are.']):
                self.autopilot['disable']()
        else:
            ok, msg = self.autopilot['enable']()
            if not ok:
                messagebox.showwarning('443 Autopilot', msg, parent=self.win)

    def _ap_open_daily(self):
        """Pop the dedicated Daily 443 live chart — the only tick-reactive
        window (this 5-day chart stays refresh-driven)."""
        from gui.daily443_chart import Daily443ChartWindow
        Daily443ChartWindow(self.win, self.ticker, self.ccy, self.autopilot)

    def _ap_on_mode(self):
        ui = self.autopilot['ui_state']() or {}
        if ui.get('mode') == 'LIVE':
            self.autopilot['set_live'](False)
            return
        lines = ui.get('lines') or {}
        detail = [f'443 will manage REAL orders for {self.ticker}.']
        for key, name in (('sell', 'SELL'), ('chase', 'CHASE BUY'),
                          ('load', 'LOAD BUY')):
            if lines.get(key):
                p, q = lines[key]
                detail.append(f'{name}:  {q} @ {fmt_price(p, self.ccy)}')
        detail.append('It will keep cancel/replacing them as the 443 lines move.')
        if self._confirm_dialog('Go LIVE', detail):
            self.autopilot['set_live'](True)

    def _ap_update(self, ui):
        """Controller callback (already on the tk thread)."""
        try:
            if not self._ap_toggle.winfo_exists():
                return
        except tk.TclError:
            return
        if ui is None or not self.autopilot['is_enabled']():
            self._ap_toggle.config(text='OFF', bg=self._ap_default_bg,
                                   fg='black')
            self._ap_mode_btn.config(state='disabled', text='DRY RUN',
                                     bg=self._ap_default_bg, fg='black')
            self._ap_daily_btn.config(state='disabled')
            self._ap_state_lbl.config(text='off', fg='#666')
            self._ap_lines_lbl.config(text='')
            self.ap_lines = {}
            self._update_order_buttons()
            self._redraw()
            return

        self._ap_toggle.config(text='ON', bg=_AP_ON_BG, fg='white')
        self._ap_daily_btn.config(state='normal')
        live = ui.get('mode') == 'LIVE'
        self._ap_mode_btn.config(
            state='normal', text=('LIVE' if live else 'DRY RUN'),
            bg=(_AP_LIVE_BG if live else _AP_DRY_BG), fg='white')
        state = ui.get('state', '?')
        color = ('#880000' if state == 'STOPPED'
                 else '#0033AA' if state == 'DEPLOYED' else '#666')
        self._ap_state_lbl.config(text=f"{state} — {ui.get('status', '')}",
                                  fg=color)

        parts = []
        if ui.get('anchor'):
            parts.append(f"anchor {fmt_price(ui['anchor'], self.ccy)}")
        for key, name in (('load', 'LOAD'), ('chase', 'CHASE'),
                          ('sell', 'SELL')):
            ln = (ui.get('lines') or {}).get(key)
            if ln:
                parts.append(f"{name} {fmt_price(ln[0], self.ccy)} ×{ln[1]}")
        if ui.get('shares'):
            parts.append(f"held {ui['shares']} @ "
                         f"{fmt_price(ui.get('avg_cost'), self.ccy)}")
        if ui.get('chase_count'):
            parts.append(f"chase #{ui['chase_count']}")
        parts.append(f"poll {ui.get('ts', '--')}")
        self._ap_lines_lbl.config(text='    '.join(parts))

        # The 5-day chart is refresh-driven (§30.7): it does NOT follow the
        # live tick price. Only a structural change of the 443 lines (a fill
        # moved the avg / anchor) triggers a redraw of the overlay; the live
        # movement lives in the Daily ▸ window.
        new_lines = {k: v for k, v in (ui.get('lines') or {}).items()
                     if k in _AP_COLORS}
        self._update_order_buttons()
        if new_lines != self.ap_lines:
            self.ap_lines = new_lines
            self._redraw()

    def _redraw(self):
        """Redraw only once the canvas exists (the 443 panel is built first)."""
        if hasattr(self, 'canvas'):
            self._draw()

    # ── Order handlers (Buy / Sell / Cancel) ──────────────────────────────────
    def _update_order_buttons(self):
        if not self.order_actions:
            return
        # While 443 autopilot manages this stock, manual ordering is off.
        if self.autopilot and self.autopilot['is_managing']():
            self._buy_btn.config(state='disabled')
            if self._sell_btn:
                self._sell_btn.config(state='disabled')
            self._cancel_btn.config(state='disabled')
            self._order_status.config(
                text='443 autopilot manages this stock', fg=_STATUS_FG)
            return
        os_ = self._ordered_side               # 'BUY' | 'SELL' | None
        oa = self.order_actions
        # Harpoon: a side fires only when one of its baits is bitten and nothing
        # is already resting on this stock.
        buy_ok = os_ is None and oa.get('buy_trig')
        sell_ok = os_ is None and oa.get('sell_trig')
        self._buy_btn.config(state='normal' if buy_ok else 'disabled')
        if self._sell_btn:
            self._sell_btn.config(state='normal' if sell_ok else 'disabled')
        self._cancel_btn.config(state='normal' if os_ is not None else 'disabled')
        if os_:
            self._order_status.config(
                text=f'● {os_.lower()} orders resting — gear locked', fg='#0033AA')
        else:
            hits = []
            if oa.get('buy_trig'):
                hits.append('buy bait bitten')
            if oa.get('sell_trig'):
                hits.append('sell bait bitten')
            self._order_status.config(
                text=('  '.join(hits) if hits
                      else 'no bait bitten — buttons off until a line is crossed'),
                fg=('#CC0000' if hits else '#888'))

    def _do_buy(self):
        # Buy is laddered: pick which bitten lines to fire (only triggered lines
        # are checkable). Funding all three at once is usually unaffordable.
        pend = self.order_actions.get('pending_buy') or []
        if not pend:
            messagebox.showinfo('Buy', 'No buy lines.', parent=self.win)
            return
        sel = self._select_lines('Confirm buy',
                                 'Fire which BUY orders? (only bitten lines)', pend)
        if not sel:
            return
        ok, msg = self.order_actions['place_buy'](sel)
        self._after_place('BUY', ok, msg)

    def _do_sell(self):
        pend = self.order_actions.get('pending_sell') or []
        if not pend:
            messagebox.showinfo('Sell', 'No sell lines.', parent=self.win)
            return
        sel = self._select_lines('Confirm sell',
                                 'Fire which SELL orders? (only bitten tiers)', pend)
        if not sel:
            return
        ok, msg = self.order_actions['place_sell'](sel)
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

    def _select_lines(self, title, prompt, pend):
        """Centered checkbox picker. Only triggered (bitten) lines are checkable
        and checked by default; untriggered lines are shown greyed/unchecked.
        pend rows are dicts with side/label/price/qty/triggered/selectable/note.
        Returns selected indices or None."""
        dlg = tk.Toplevel(self.win)
        dlg.title(title)
        dlg.transient(self.win)
        tk.Label(dlg, text=prompt, font=_F_STAT).pack(anchor='w', padx=14,
                                                      pady=(12, 6))
        bvars = []
        for it in pend:
            s = it.get('side')
            lbl = it.get('label')
            p = it.get('price')
            q = it.get('qty')
            trig = bool(it.get('triggered'))
            selectable = bool(it.get('selectable', trig))
            note = it.get('note') or ''
            v = tk.BooleanVar(value=selectable)
            if not trig:
                tail = '   (not triggered)'
            elif not selectable:
                tail = f"   ({note or 'not selectable'})"
            else:
                tail = ''
            tk.Checkbutton(dlg, variable=v, font=_F_DAY, anchor='w',
                           text=f"{lbl}:   {q} @ {p}{tail}",
                           state=('normal' if selectable else 'disabled')
                           ).pack(anchor='w', padx=18)
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
        for ln in self.ap_lines.values():
            if ln and ln[0]:
                prices.append(ln[0])

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

        # Projection ladders (dotted). Keep them visible even while gear is
        # locked by a live order; suppress only near-duplicate ordered prices.
        for idx, (lbl, price, qty) in enumerate(self.buy_lines):
            if is_ordered(price):
                continue
            clr = _BUY_COLORS[min(idx, len(_BUY_COLORS) - 1)]
            qty_txt = f' ×{qty}' if qty else ''
            ref_line(price, clr,
                     f'{lbl}: {fmt_price(price, self.ccy)}{qty_txt}')

        for idx, (lbl, price, qty) in enumerate(self.sell_lines):
            if is_ordered(price):
                continue
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

        # Daily 443 autopilot lines — SOLID and bold (the bot's actual lines)
        for key, name in (('load', 'AP LOAD'), ('chase', 'AP CHASE'),
                          ('sell', 'AP SELL')):
            ln = self.ap_lines.get(key)
            if not ln or not ln[0]:
                continue
            price, qty = ln
            clr = _AP_COLORS[key]
            y = y_of(price)
            c.create_line(left_pad, y, left_pad + chart_w, y,
                          fill=clr, width=2.5)
            c.create_text(label_x, y,
                          text=f"{name}: {fmt_price(price, self.ccy)}"
                               f"{f' ×{qty}' if qty else ''}",
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
