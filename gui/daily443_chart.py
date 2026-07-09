"""Daily 443 live chart — the ONLY window that follows the 15-second
autopilot ticks (§30.7). The 5-day chart stays refresh-driven.

Shows the intraday tick path inside the "7% commando zone":

    DEPLOYED:  chase line (avg×0.96) … avg … sell line (avg×1.03)
    EMPTY:     load line (anchor−4%, or −3% after an intraday sell)
               … pseudo sell (load×1.03)

As long as the price path stays inside the shaded zone the autopilot is
working the plan; a fill moves the zone itself (new avg / new anchor).
The right panel logs today's campaign fills (what was bought at how much,
until it is all sold) and the currently deployed size.
"""

import tkinter as tk
from core.calc import STOCK_NAMES, fmt_price

_F_TITLE = ('Segoe UI', 17, 'bold')
_F_STAT  = ('Segoe UI', 13)
_F_INFO  = ('Segoe UI', 12)
_F_AXIS  = ('Segoe UI', 10)
_F_REF   = ('Segoe UI', 11, 'bold')
_F_LOG   = ('Consolas', 11)

_CLR = {
    'sell':  '#007700',
    'psell': '#66AA66',
    'chase': '#7E3FBF',
    'load':  '#E08000',
    'avg':   '#FF8C00',
    'anchor': '#888888',
    'path':  '#1A1A1A',
    'now':   '#CC0000',
    'zone':  '#F0F7F0',
    'state_dep': '#0033AA',
    'state_stop': '#880000',
}
_KIND_CLR = {'LOAD': '#E08000', 'CHASE': '#7E3FBF', 'SELL': '#007700'}


class Daily443ChartWindow:
    """Live intraday zone chart for one autopiloted stock. Subscribes to the
    AutopilotController (via the graph context) and redraws on every tick."""

    def __init__(self, parent, ticker, currency, ap_ctx):
        self.ap = ap_ctx
        self.ticker = ticker
        self.ccy = currency
        self.ui = None

        self.win = tk.Toplevel(parent)
        name = STOCK_NAMES.get(ticker, ticker)
        self.win.title(f'{name} — Daily 443 live')
        self.win.geometry('1180x680')
        self.win.minsize(760, 480)

        head = tk.Frame(self.win, padx=12, pady=8)
        head.pack(fill='x')
        self._title_lbl = tk.Label(head, text=f'{name}  — Daily 443',
                                   font=_F_TITLE)
        self._title_lbl.pack(side='left')
        self._state_lbl = tk.Label(head, text='', font=_F_TITLE)
        self._state_lbl.pack(side='left', padx=(16, 0))
        self._mode_lbl = tk.Label(head, text='', font=_F_STAT)
        self._mode_lbl.pack(side='right')

        self._info_lbl = tk.Label(self.win, text='', font=_F_INFO, fg='#333',
                                  anchor='w')
        self._info_lbl.pack(fill='x', padx=14)
        self._status_lbl = tk.Label(self.win, text='', font=_F_INFO,
                                    fg='#4B0082', anchor='w')
        self._status_lbl.pack(fill='x', padx=14)

        body = tk.Frame(self.win)
        body.pack(fill='both', expand=True, padx=12, pady=(6, 12))

        self.canvas = tk.Canvas(body, bg='white', highlightthickness=0)
        self.canvas.pack(side='left', fill='both', expand=True)
        self.canvas.bind('<Configure>', lambda e: self._draw())

        side = tk.Frame(body)
        side.pack(side='left', fill='y', padx=(10, 0))
        tk.Label(side, text="Today's campaign fills", font=_F_STAT
                 ).pack(anchor='w')
        self._log_txt = tk.Text(side, width=38, font=_F_LOG, state='disabled',
                                bg='#FAFAFA', relief='groove', bd=1)
        self._log_txt.pack(fill='y', expand=True, pady=(4, 0))
        for kind, clr in _KIND_CLR.items():
            self._log_txt.tag_configure(kind, foreground=clr)

        self._cb = self._on_update
        self.ap['subscribe'](self._cb)
        self.win.bind('<Destroy>', self._on_destroy)
        self._on_update(self.ap['ui_state']())

    def _on_destroy(self, event):
        if event.widget is self.win and self._cb:
            self.ap['unsubscribe'](self._cb)
            self._cb = None

    # ── Controller callback (tk thread) ───────────────────────────────────────

    def _on_update(self, ui):
        try:
            if not self.canvas.winfo_exists():
                return
        except tk.TclError:
            return
        if ui is None or not self.ap['is_enabled']():
            self._state_lbl.config(text='autopilot off', fg='#888')
            self._status_lbl.config(text='')
            self.ui = None
            self._draw()
            return
        self.ui = ui

        state = ui.get('state', '?')
        clr = (_CLR['state_stop'] if state == 'STOPPED'
               else _CLR['state_dep'] if state == 'DEPLOYED' else '#666')
        self._state_lbl.config(text=state, fg=clr)
        live = ui.get('mode') == 'LIVE'
        self._mode_lbl.config(text=('LIVE' if live else 'DRY RUN'),
                              fg=('white' if live else 'black'),
                              bg=('#CC0000' if live else '#E8C87A'))
        self._info_lbl.config(text=self._info_text(ui))
        self._status_lbl.config(
            text=f"{ui.get('status', '')}    poll {ui.get('ts', '--')}")
        self._fill_log(ui.get('events') or [])
        self._draw()

    def _info_text(self, ui):
        parts = []
        shares = ui.get('shares') or 0
        price = ui.get('price')
        avg = ui.get('avg_cost') or 0
        unit = ui.get('unit_cash') or 0
        if shares > 0:
            parts.append(f'held {shares} @ {fmt_price(avg, self.ccy)}')
            val = shares * (price or avg)
            if unit > 0:
                parts.append(f'deployed ≈ {val / unit:,.2f} u')
            else:
                parts.append(f'deployed {fmt_price(val, self.ccy)}')
        else:
            src = '-3% of sell' if ui.get('anchor_source') == 'sell' \
                else '-4% of close'
            if ui.get('anchor'):
                parts.append(f"anchor {fmt_price(ui['anchor'], self.ccy)} "
                             f'({src})')
        bp = ui.get('buying_power')
        if bp is not None:
            parts.append(f'reserve {fmt_price(bp, self.ccy)}')
        if ui.get('chase_count'):
            parts.append(f"chase #{ui['chase_count']}")
        return '      '.join(parts)

    def _fill_log(self, events):
        txt = self._log_txt
        txt.config(state='normal')
        txt.delete('1.0', 'end')
        if not events:
            txt.insert('end', '(no fills yet today)\n')
        for e in events:
            price = e.get('price')
            p = fmt_price(price, self.ccy) if price else '--'
            line = (f"{e['ts']}  {e['kind']:<5} {e['qty']:+d} @ {p}"
                    f"  → {e['shares']} sh")
            if e.get('avg'):
                line += f" @ {fmt_price(e['avg'], self.ccy)}"
            txt.insert('end', line + '\n', e['kind'])
        txt.config(state='disabled')
        txt.see('end')

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _draw(self):
        c = self.canvas
        c.delete('all')
        w, h = c.winfo_width(), c.winfo_height()
        if w < 160 or h < 120:
            return
        ui = self.ui
        if not ui:
            c.create_text(w / 2, h / 2, text='Autopilot is off',
                          font=_F_STAT, fill='#888')
            return

        lines = {k: v for k, v in (ui.get('lines') or {}).items()
                 if v and v[0]}
        ticks = ui.get('ticks') or []
        deployed = (ui.get('shares') or 0) > 0
        avg = ui.get('avg_cost') or 0
        anchor = ui.get('anchor')

        prices = [p for _, p in ticks]
        prices += [v[0] for v in lines.values()]
        if deployed and avg > 0:
            prices.append(avg)
        if not deployed and anchor:
            prices.append(anchor)
        if ui.get('price'):
            prices.append(ui['price'])
        if not prices:
            c.create_text(w / 2, h / 2, text='waiting for the first tick…',
                          font=_F_STAT, fill='#888')
            return

        left, right, top, bottom = 90, 185, 18, 32
        cw, ch = w - left - right, h - top - bottom
        if cw < 60 or ch < 60:
            return
        p_min, p_max = min(prices), max(prices)
        p_rng = (p_max - p_min) or (p_max * 0.01) or 1
        p_min -= p_rng * 0.10
        p_max += p_rng * 0.10
        p_rng = p_max - p_min

        def y_of(p):
            return top + ch * (1 - (p - p_min) / p_rng)

        # The 7% zone: buy line up to the (pseudo) sell line, shaded.
        lo = (lines.get('chase') or lines.get('load') or (None,))[0]
        hi = (lines.get('sell') or lines.get('psell') or (None,))[0]
        if lo and hi and hi > lo:
            c.create_rectangle(left, y_of(hi), left + cw, y_of(lo),
                               fill=_CLR['zone'], outline='')

        # Grid + y labels
        for i in range(5):
            p = p_min + p_rng * i / 4
            y = y_of(p)
            c.create_line(left, y, left + cw, y, fill='#E8E8E8')
            c.create_text(left - 5, y, text=fmt_price(p, self.ccy),
                          anchor='e', font=_F_AXIS, fill='#888')

        # Time axis from the first tick (min span 30 min so early ticks
        # don't smear across the full width).
        import time as _time
        now = _time.time()
        t0 = ticks[0][0] if ticks else now
        span = max(now - t0, 1800.0)

        def x_of(t):
            return left + cw * (t - t0) / span

        from datetime import datetime as _dt
        for i in range(5):
            t = t0 + span * i / 4
            x = left + cw * i / 4
            c.create_line(x, top, x, top + ch, fill='#F2F2F2')
            c.create_text(x, h - 6, text=_dt.fromtimestamp(t).strftime('%H:%M'),
                          font=_F_AXIS, fill='#777', anchor='s')

        label_x = left + cw + 6

        def ref(price, color, text, dash=None, width=2.4):
            y = y_of(price)
            c.create_line(left, y, left + cw, y, fill=color,
                          width=width, dash=dash)
            c.create_text(label_x, y, text=text, anchor='w',
                          font=_F_REF, fill=color)

        # Reference lines: anchor/avg dashed grey-orange, 443 lines solid.
        if deployed and avg > 0:
            ref(avg, _CLR['avg'], f'AVG {fmt_price(avg, self.ccy)}',
                dash=(5, 4), width=1.6)
        elif anchor:
            ref(anchor, _CLR['anchor'],
                f'ANCHOR {fmt_price(anchor, self.ccy)}', dash=(5, 4),
                width=1.4)
        for key, name in (('sell', 'SELL'), ('psell', 'SELL (pseudo)'),
                          ('chase', 'CHASE'), ('load', 'LOAD')):
            ln = lines.get(key)
            if ln:
                ref(ln[0], _CLR[key],
                    f'{name} {fmt_price(ln[0], self.ccy)} ×{ln[1]}')

        # Tick path + the live point.
        if len(ticks) >= 2:
            pts = []
            for t, p in ticks:
                pts += [x_of(t), y_of(p)]
            c.create_line(*pts, fill=_CLR['path'], width=2)
        if ticks:
            t, p = ticks[-1]
            x, y = x_of(t), y_of(p)
            c.create_oval(x - 4, y - 4, x + 4, y + 4, fill=_CLR['now'],
                          outline='white')
            c.create_text(x, y - 12, text=fmt_price(p, self.ccy),
                          font=_F_REF, fill=_CLR['now'])
