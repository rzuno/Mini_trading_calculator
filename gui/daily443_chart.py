"""Autopilot live window — the adaptive-gear watcher's cockpit.

Opened by the card's big Autopilot button. Opening it arms bare WATCH mode:
the stock is polled every 10 s and this window follows every tick — the
5-day chart and the main panel stay refresh-button-driven.

Top-right controls select the mode:

    (no button active)  WATCH — bare live info: zone chart + lines, no orders
    [DRY RUN]           paper simulation (virtual fills, not connected)
    [LIVE]              real watcher: fires a real order when a line is hit;
                        allowed only during regular market hours

Closing the window in WATCH mode stops the polling; in DRY/LIVE the
autopilot keeps running in the background (the card button stays colored).

The gears are NOT chosen here — they are picked automatically from the
stock's deployment ratio (PART II of the manual) and displayed live:
B1/B2/B3 set the chase line (−4/−5/−6%, ×1/2, ×2/3, ×3/4) and S1/S2/S3 the
full-exit line (+2/+2.5/+3%). The chart shows the intraday tick path inside
that shaded gear zone, avg/anchor, the campaign fill log (cleared when the
position is fully sold), deployed size and the market phase.
"""

import time as _time
from datetime import datetime as _dt

import tkinter as tk
from tkinter import messagebox

from core.calc import STOCK_NAMES, fmt_price
from core.autopilot import BUY_GEARS, SELL_GEARS

_F_TITLE = ('Segoe UI', 17, 'bold')
_F_STAT  = ('Segoe UI', 13)
_F_BTN   = ('Segoe UI', 13, 'bold')
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
_KIND_CLR = {'LOAD': '#E08000', 'CHASE': '#7E3FBF', 'SELL': '#007700',
             'HOLD': '#555555'}
_PHASE_TXT = {'REGULAR': ('OPEN (regular)', '#007700'),
              'PRE':     ('pre-market', '#B8860B'),
              'AFTER':   ('after-market', '#B8860B'),
              'CLOSED':  ('CLOSED', '#888888')}
_DRY_BG = '#E08000'
_LIVE_BG = '#CC0000'


class Daily443ChartWindow:
    """Live cockpit for one watched stock. Subscribes to the controller and
    redraws on every 10-second tick."""

    def __init__(self, parent, ticker, currency, ap_ctx):
        self.ap = ap_ctx
        self.ticker = ticker
        self.ccy = currency
        self.ui = None

        self.win = tk.Toplevel(parent)
        name = STOCK_NAMES.get(ticker, ticker)
        self.win.title(f'{name} — Autopilot live')
        self.win.geometry('1180x680')
        self.win.minsize(780, 480)

        head = tk.Frame(self.win, padx=12, pady=8)
        head.pack(fill='x')
        tk.Label(head, text=f'{name}  — Autopilot', font=_F_TITLE
                 ).pack(side='left')
        self._state_lbl = tk.Label(head, text='', font=_F_TITLE)
        self._state_lbl.pack(side='left', padx=(16, 0))

        # Mode controls, right-aligned. Gears are automatic (no selector) —
        # the current B/S gear pair is displayed next to the market phase.
        self._live_btn = tk.Button(head, text='LIVE', font=_F_BTN, width=8,
                                   command=lambda: self._on_mode('LIVE'))
        self._live_btn.pack(side='right', padx=(6, 0))
        self._dry_btn = tk.Button(head, text='DRY RUN', font=_F_BTN, width=9,
                                  command=lambda: self._on_mode('DRY'))
        self._dry_btn.pack(side='right', padx=(6, 0))
        self._default_bg = self._dry_btn.cget('bg')

        self._gear_lbl = tk.Label(head, text='', font=_F_BTN, fg='#4B0082')
        self._gear_lbl.pack(side='right', padx=(12, 12))

        self._phase_lbl = tk.Label(head, text='', font=_F_STAT)
        self._phase_lbl.pack(side='right', padx=(0, 4))

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
        tk.Label(side, text='Campaign fills', font=_F_STAT).pack(anchor='w')
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
        if event.widget is not self.win:
            return
        if self._cb:
            self.ap['unsubscribe'](self._cb)
            self._cb = None
        # Bare watching stops with its window; DRY/LIVE keep running in the
        # background (the card's 443 button stays colored).
        if self.ap['mode_of']() == 'WATCH':
            self.ap['disable']()

    # ── Mode / pedal controls ─────────────────────────────────────────────────

    def _on_mode(self, mode):
        current = self.ap['mode_of']()
        if current == mode:                    # click active button → WATCH
            self.ap['set_mode']('WATCH')
            return
        if mode == 'LIVE':
            ui = self.ap['ui_state']() or {}
            lines = ui.get('lines') or {}
            detail = [f'Go LIVE on {self.ticker}?', '',
                      'The watcher fires REAL orders when a line is hit:']
            for key, tag in (('sell', 'SELL all'), ('chase', 'CHASE BUY'),
                             ('load', 'LOAD BUY')):
                if lines.get(key):
                    p, q = lines[key]
                    detail.append(f'  {tag}:  {q} @ {fmt_price(p, self.ccy)}')
            detail.append('')
            detail.append('Gears follow the deployed army automatically. '
                          'Nothing rests before a trigger; LIVE drops back '
                          'to WATCH when the market closes.')
            if not messagebox.askyesno('Autopilot LIVE', '\n'.join(detail),
                                       parent=self.win):
                return
        ok, msg = self.ap['set_mode'](mode)
        if not ok:
            messagebox.showwarning('Autopilot', msg, parent=self.win)

    # ── Controller callback (tk thread) ───────────────────────────────────────

    def _on_update(self, ui):
        try:
            if not self.canvas.winfo_exists():
                return
        except tk.TclError:
            return
        if ui is None or not self.ap['is_enabled']():
            self._state_lbl.config(text='off', fg='#888')
            self._status_lbl.config(text='')
            self.ui = None
            self._draw()
            return
        self.ui = ui

        state = ui.get('state', '?')
        clr = (_CLR['state_stop'] if state == 'STOPPED'
               else _CLR['state_dep'] if state == 'DEPLOYED' else '#666')
        self._state_lbl.config(text=state, fg=clr)

        mode = ui.get('mode', 'WATCH')
        self._dry_btn.config(
            bg=(_DRY_BG if mode == 'DRY' else self._default_bg),
            fg=('white' if mode == 'DRY' else 'black'))
        self._live_btn.config(
            bg=(_LIVE_BG if mode == 'LIVE' else self._default_bg),
            fg=('white' if mode == 'LIVE' else 'black'))

        phase = ui.get('phase', 'CLOSED')
        txt, pclr = _PHASE_TXT.get(phase, (phase, '#888'))
        mkt = 'KR' if self.ticker.endswith('.KS') else 'US'
        self._phase_lbl.config(text=f'{mkt} market: {txt}', fg=pclr)

        # Auto-chosen gear pair (deployment-driven). Red when exhausted.
        bg_, sg_ = ui.get('buy_gear'), ui.get('sell_gear')
        if bg_ and sg_:
            gear_txt = (f"{BUY_GEARS[bg_]['label']}   "
                        f"{SELL_GEARS[sg_]['label']}   "
                        f"deploy {ui.get('deploy_ratio', 0) * 100:.0f}%")
            if ui.get('buy_state') == 'EXHAUSTED':
                gear_txt += '   EXHAUSTED'
            elif ui.get('buy_state') == 'FALLBACK':
                gear_txt += '   B1 fallback'
            self._gear_lbl.config(
                text=gear_txt,
                fg=('#880000' if ui.get('buy_state') == 'EXHAUSTED'
                    else '#4B0082'))
        else:
            self._gear_lbl.config(text='gears: auto (empty — load -4%)',
                                  fg='#888')

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
            src = ('sell point' if ui.get('anchor_source') == 'sell'
                   else 'prev close')
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
            txt.insert('end', '(campaign log is empty —\n cleared after '
                              'each full sell)\n')
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

        # The pedal zone: buy line up to the (pseudo) sell line, shaded.
        lo = (lines.get('chase') or lines.get('load') or (None,))[0]
        hi = (lines.get('sell') or lines.get('psell') or (None,))[0]
        if lo and hi and hi > lo:
            c.create_rectangle(left, y_of(hi), left + cw, y_of(lo),
                               fill=_CLR['zone'], outline='')

        for i in range(5):
            p = p_min + p_rng * i / 4
            y = y_of(p)
            c.create_line(left, y, left + cw, y, fill='#E8E8E8')
            c.create_text(left - 5, y, text=fmt_price(p, self.ccy),
                          anchor='e', font=_F_AXIS, fill='#888')

        # Time axis from the first tick (min span 30 min so early ticks
        # don't smear across the full width).
        now = _time.time()
        t0 = ticks[0][0] if ticks else now
        span = max(now - t0, 1800.0)

        def x_of(t):
            return left + cw * (t - t0) / span

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
