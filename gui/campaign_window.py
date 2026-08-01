"""Campaign window — the Gearbox V-Commandos cockpit for one watched stock.

Opened by the card's V-COMMANDOS button. Opening it arms WATCH mode: the
stock is polled every few seconds and this window follows every tick. The bot
follows exactly the lines the CARD draws — same gear, same exit tier.

    WATCH  polling, lines and fill detection only; nothing is placed.
    LIVE   the bot sends the LOAD / CHASE / full EXIT by ITSELF the moment
           the curve crosses a line; regular market hours only (drops back
           to WATCH at the close).

There are no manual Buy/Sell buttons: the point of the bot is that the offer
goes out when the curve touches the line. The only intervention left is
"Cancel all", the escape hatch.

Layout is ONE information banner over TWO charts, plus the campaign log:

    header   name · campaign state · market phase · LIVE
    banner   gear line (G/tier · load% / chase% ×frac · vantage)
             position · reserve · campaign age / chases / low
             ▲ the EXIT   ▼ the next buy and its size, then the ones after
             engine status + poll time
    charts   live tick curve inside the campaign | 5-day candle panel
    log      RECORDED FILLS — one row per trade, grouped by trading day

Closing the window in WATCH mode stops the polling; in LIVE the campaign
keeps running in the background (the card button stays colored).
"""

import time as _time
from datetime import datetime as _dt

import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox

from core.calc import STOCK_NAMES, fmt_price
from gui.candle_chart import (CandlePanel, bounded_label_layout,
                              required_label_pad)

_F_TITLE = ('Segoe UI', 17, 'bold')
_F_STAT  = ('Segoe UI', 13)
_F_BTN   = ('Segoe UI', 13, 'bold')
_F_INFO  = ('Segoe UI', 12)
_F_AXIS  = ('Segoe UI', 10)
_F_REF   = ('Segoe UI', 11, 'bold')
_F_LOG   = ('Consolas', 11)

# KR color language: red = the EXIT (sell) side, blue = the buy side. Soft
# shades are the projected lines the ladder has not reached yet.
_CLR = {
    'exit':      '#CC3333',
    'exit_soft': '#E4A9A9',
    'buy':       '#3366CC',
    'buy_soft':  '#A9C4E4',
    'vantage':   '#E08000',
    'avg':       '#7E3FBF',
    'path':      '#1A1A1A',
    'now':       '#CC0000',
    'zone':      '#F4F1FA',
}
_PHASE_TXT = {'REGULAR': ('OPEN (regular)', '#007700'),
              'PRE':     ('pre-market', '#B8860B'),
              'AFTER':   ('after-market', '#B8860B'),
              'CLOSED':  ('CLOSED', '#888888')}
_LIVE_BG = '#CC0000'
_NO_ARMY_CLR = '#999999'
_STATE_CLR = {
    'FLAT': '#666666', 'ARMED_LOAD': '#0033AA', 'DEPLOYED': '#0033AA',
    'CHASE_PENDING': '#B8860B', 'EXIT_PENDING': '#B8860B',
    'COMPLETED': '#007700', 'RELOAD_ARMED': '#E08000',
    'PAUSED_RECONCILE': '#CC0000', 'ARMING': '#666666',
}
_KIND_CLR = {'LOAD': '#0033AA', 'RELOAD': '#E08000', 'CHASE': '#3366CC',
             'EXIT': '#CC3333', 'PARTIAL': '#B8860B',
             'ADOPT': '#7E3FBF', 'GEAR': '#666666', 'TIER': '#666666'}

_VANTAGE_SRC = {'high5': 'High5', 'close': 'prev close',
                'reload': 'sell fill'}

# Buy lines in the order they would fire, armed first.
_BUY_KEYS = ('load', 'chase', 'chase2', 'chase3')


def campaign_line(ui, currency):
    """The one banner line that names the whole campaign setup."""
    c = ui.get('campaign') or {}
    if not c:
        return 'arming…'
    parts = [f"G{c['gear']} {c['gear_name']}",
             f"LOAD -{c['load_pct']}%",
             f"CHASE -{c['chase_pct']}% ×{c['add_frac']}",
             f"EXIT T{c['exit_tier']} +{c['exit_pct']}% (full)"]
    if c.get('vantage'):
        src = _VANTAGE_SRC.get(c.get('vantage_src'), '')
        parts.append(f"vantage {fmt_price(c['vantage'], currency)} ({src})")
    return '  ·  '.join(parts)


def campaign_age_line(ui, currency):
    """Campaign identity + progress: id, start, chases, low, peak deployment."""
    c = ui.get('campaign') or {}
    if not c or not c.get('campaign_id'):
        return ''
    bits = [f"campaign {c['campaign_id']}"]
    if c.get('campaign_start'):
        bits.append(f"since {c['campaign_start']}")
    bits.append(f"chases {c.get('chase_count', 0)}")
    if c.get('campaign_low'):
        bits.append(f"low {fmt_price(c['campaign_low'], currency)}")
    if c.get('max_cost'):
        bits.append(f"peak {fmt_price(c['max_cost'], currency)}")
    if c.get('manually_modified'):
        bits.append('MANUALLY_MODIFIED')
    return '   ·   '.join(bits)


def buy_lines(ui):
    """The buy ladder in firing order: the armed line, then the projections."""
    lines = ui.get('lines') or {}
    return [lines[k] for k in _BUY_KEYS if lines.get(k)]


def next_line(ui, currency):
    """'▲ EXIT …   ▼ next buy …   then …' — the sell line on top, the next
    buy and its size below it, and where the ladder goes after that."""
    lines = ui.get('lines') or {}
    parts = []
    sell = lines.get('exit') or lines.get('pexit')
    if sell:
        tag = 'EXIT' if lines.get('exit') else 'exit if loaded'
        parts.append(f"▲ {tag} {fmt_price(sell['price'], currency)} × "
                     f"{sell['qty']}")
    else:
        parts.append('▲ no exit line')

    ladder = buy_lines(ui)
    if ladder:
        armed = ladder[0]
        tail = (' [NO ARMY]' if ui.get('buy_state') != 'OK' else '')
        parts.append(f"▼ {armed['label']} {fmt_price(armed['price'], currency)}"
                     f" × {armed['qty']}{tail}")
        rest = ['%s × %s' % (fmt_price(e['price'], currency), e['qty'])
                for e in ladder[1:]]
        if rest:
            parts.append('then ' + '  ·  '.join(rest))
    else:
        parts.append('▼ no buy line')
    return '      '.join(parts)


def fill_log_rows(ui, currency, limit=40):
    """[(text, kind)] for the campaign log — newest last, one row per trade,
    with a day header whenever the trading date changes."""
    events = (ui.get('events') or [])[-limit:]
    rows, day = [], None
    for e in events:
        d = e.get('date') or ''
        if d and d != day:
            day = d
            rows.append((f'── {d} ' + '─' * 24, 'DAY'))
        price = e.get('price')
        p = fmt_price(price, currency) if price else '--'
        kind = e.get('kind', '?')
        if e.get('qty'):
            line = (f"{e['ts']}  {kind:<7} {e['qty']:+d} @ {p}"
                    f"  → {e['shares']} sh")
            if e.get('avg'):
                line += f" @ {fmt_price(e['avg'], currency)}"
            if e.get('source') == 'EXT':
                line += '  [hand]'
        else:
            line = f"{e['ts']}  {kind:<7} {e.get('note', '')}"
        if e.get('note') and e.get('qty'):
            line += f"  {e['note']}"
        rows.append((line, kind if kind in _KIND_CLR else 'OTHER'))
    return rows


class CampaignWindow:
    """Live cockpit for one V-Commandos campaign. Subscribes to the
    controller and redraws on every tick."""

    def __init__(self, parent, ticker, currency, ap_ctx):
        self.ap = ap_ctx
        self.ticker = ticker
        self.ccy = currency
        self.ui = None

        self.win = tk.Toplevel(parent)
        name = STOCK_NAMES.get(ticker, ticker)
        self.win.title(f'{name} — V-Commandos campaign')
        self.win.geometry('1260x900')
        self.win.minsize(980, 640)
        self._ref_font = tkfont.Font(root=self.win, font=_F_REF)

        # ── Header ────────────────────────────────────────────────────────────
        head = tk.Frame(self.win, padx=12, pady=8)
        head.pack(fill='x')
        tk.Label(head, text=f'{name}  — V-Commandos Gearbox', font=_F_TITLE
                 ).pack(side='left')
        self._state_lbl = tk.Label(head, text='', font=_F_TITLE)
        self._state_lbl.pack(side='left', padx=(16, 0))

        self._live_btn = tk.Button(head, text='LIVE', font=_F_BTN, width=8,
                                   command=self._on_live)
        self._live_btn.pack(side='right', padx=(6, 0))
        self._default_bg = self._live_btn.cget('bg')
        self._cancel_btn = tk.Button(head, text='Cancel all', font=_F_INFO,
                                     command=self._do_cancel)
        self._cancel_btn.pack(side='right', padx=(6, 0))
        self._phase_lbl = tk.Label(head, text='', font=_F_STAT)
        self._phase_lbl.pack(side='right', padx=(0, 8))

        # ── Banner ────────────────────────────────────────────────────────────
        self._gear_lbl = tk.Label(self.win, text='', font=_F_BTN,
                                  fg='#4B0082', anchor='w')
        self._gear_lbl.pack(fill='x', padx=12)
        self._age_lbl = tk.Label(self.win, text='', font=_F_INFO, fg='#666',
                                 anchor='w')
        self._age_lbl.pack(fill='x', padx=14)
        self._info_lbl = tk.Label(self.win, text='', font=_F_INFO, fg='#333',
                                  anchor='w')
        self._info_lbl.pack(fill='x', padx=14)
        self._next_lbl = tk.Label(self.win, text='', font=_F_INFO, fg='#333',
                                  anchor='w')
        self._next_lbl.pack(fill='x', padx=14)
        self._status_lbl = tk.Label(self.win, text='', font=_F_INFO,
                                    fg='#4B0082', anchor='w')
        self._status_lbl.pack(fill='x', padx=14, pady=(0, 4))

        # ── Campaign log (bottom strip, grows with the campaign) ─────────────
        log_box = tk.Frame(self.win)
        log_box.pack(fill='x', side='bottom', padx=12, pady=(0, 10))
        self._log_txt = tk.Text(log_box, height=7, font=_F_LOG, bd=1,
                                relief='sunken', wrap='none',
                                background='#FBFBFB')
        bar = tk.Scrollbar(log_box, command=self._log_txt.yview)
        self._log_txt.config(yscrollcommand=bar.set)
        bar.pack(side='right', fill='y')
        self._log_txt.pack(side='left', fill='x', expand=True)
        self._log_txt.tag_config('HEADER', foreground='#000000',
                                 font=('Consolas', 11, 'bold'))
        self._log_txt.tag_config('DAY', foreground='#888888')
        self._log_txt.tag_config('OTHER', foreground='#333333')
        for kind, color in _KIND_CLR.items():
            self._log_txt.tag_config(kind, foreground=color)
        self._log_txt.config(state='disabled')

        # ── Body: live chart | 5-day candle panel ─────────────────────────────
        self._body = tk.Frame(self.win)
        self._body.pack(fill='both', expand=True, padx=12, pady=(6, 8))

        self.canvas = tk.Canvas(self._body, bg='white', highlightthickness=0)
        self.canvas.pack(side='left', fill='both', expand=True)
        self.canvas.bind('<Configure>', lambda e: self._draw())

        self.candle_panel = CandlePanel(self._body, currency, width=470)
        self.candle_panel.pack(side='left', fill='both', padx=(10, 0))

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
        # Bare watching stops with its window; LIVE keeps running in the
        # background (the card's button stays colored).
        if self.ap['mode_of']() == 'WATCH':
            self.ap['disable']()

    # ── Controls ──────────────────────────────────────────────────────────────

    def _on_live(self):
        if self.ap['mode_of']() == 'LIVE':       # click again → back to WATCH
            self.ap['set_mode']('WATCH')
            return
        ui = self.ap['ui_state']() or {}
        c = ui.get('campaign') or {}
        lines = ui.get('lines') or {}
        detail = [f'Go LIVE on {self.ticker}?', '',
                  'The campaign bot sends these by ITSELF the moment the '
                  'price touches the line:']
        for e in buy_lines(ui)[:1]:
            detail.append(f"  BUY   {e['qty']} @ "
                          f"{fmt_price(e['price'], self.ccy)}   ({e['label']})")
        if lines.get('exit'):
            detail.append(f"  SELL  {lines['exit']['qty']} (ALL) @ "
                          f"{fmt_price(lines['exit']['price'], self.ccy)}")
        detail += [
            '',
            f"Gear {c.get('gear', '?')}: LOAD -{c.get('load_pct', '?')}%, "
            f"CHASE -{c.get('chase_pct', '?')}% ×{c.get('add_frac', '?')}, "
            f"one full EXIT at T{c.get('exit_tier', '?')} "
            f"+{c.get('exit_pct', '?')}%. One order at a time; every fill "
            f"recomputes both lines from the broker's real average; the "
            f"campaign ends only when the holding is zero. The chase stops "
            f"when the army runs out — there is no other cap. LIVE drops "
            f"back to WATCH when the market closes.",
        ]
        if not messagebox.askyesno('Campaign LIVE', '\n'.join(detail),
                                   parent=self.win):
            return
        ok, msg = self.ap['set_mode']('LIVE')
        if not ok:
            messagebox.showwarning('Autopilot', msg, parent=self.win)

    def _do_cancel(self):
        if not messagebox.askyesno(
                'Cancel', f'Cancel EVERY live order on {self.ticker}?',
                parent=self.win):
            return
        ok, msg = self.ap['cancel_all']()
        if not ok:
            messagebox.showwarning('Cancel', msg, parent=self.win)

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

        state = ui.get('campaign_state') or ui.get('state', '?')
        self._state_lbl.config(text=state, fg=_STATE_CLR.get(state, '#666'))

        mode = ui.get('mode', 'WATCH')
        self._live_btn.config(
            bg=(_LIVE_BG if mode == 'LIVE' else self._default_bg),
            fg=('white' if mode == 'LIVE' else 'black'))

        phase = ui.get('phase', 'CLOSED')
        txt, pclr = _PHASE_TXT.get(phase, (phase, '#888'))
        mkt = 'KR' if self.ticker.endswith('.KS') else 'US'
        self._phase_lbl.config(text=f'{mkt} market: {txt}', fg=pclr)

        self._gear_lbl.config(text=campaign_line(ui, self.ccy))
        self._age_lbl.config(text=campaign_age_line(ui, self.ccy))
        self._info_lbl.config(text=self._info_text(ui))
        self._next_lbl.config(text=next_line(ui, self.ccy))
        self._status_lbl.config(
            text=f"{ui.get('status', '')}    poll {ui.get('ts', '--')}",
            fg=('#CC0000' if ui.get('buy_state') != 'OK' else '#4B0082'))
        self._update_log(ui)
        self._draw()
        self._refresh_candles()

    def _info_text(self, ui):
        parts = []
        shares = ui.get('shares') or 0
        price = ui.get('price')
        avg = ui.get('avg_cost') or 0
        unit = ui.get('unit_cash') or 0
        parts.append(f'{shares:,} sh'
                     + (f' @ {fmt_price(avg, self.ccy)} avg' if avg else ''))
        if shares and avg and unit > 0:
            parts.append(f'{shares * avg / unit:,.2f} u deployed')
        if price:
            parts.append(f'now {fmt_price(price, self.ccy)}')
        if shares and avg and price:
            parts.append(f'P&L {(price - avg) / avg * 100:+.2f}%')
        c = ui.get('campaign') or {}
        if c.get('gross_target'):
            parts.append(f"target {fmt_price(c['gross_target'], self.ccy)}")
        bp = ui.get('buying_power')
        if bp is not None:
            parts.append(f'army {fmt_price(bp, self.ccy)}')
        orders = ui.get('orders') or []
        if orders:
            sides = sorted({o.get('side') for o in orders if o.get('side')})
            parts.append('● resting: ' + '/'.join(sides))
        return '      '.join(parts)

    def _update_log(self, ui):
        rows = fill_log_rows(ui, self.ccy)
        txt = self._log_txt
        txt.config(state='normal')
        txt.delete('1.0', 'end')
        txt.insert('end', 'RECORDED FILLS\n', 'HEADER')
        if not rows:
            state = ui.get('campaign_state') or ui.get('state') or 'ARMING'
            txt.insert('end', {
                'DEPLOYED': '(no buy/sell change recorded since this '
                            'campaign was adopted)\n',
                'FLAT': '(no campaign open — waiting for the LOAD)\n',
            }.get(state, '(watcher is arming)\n'), 'OTHER')
        for line, kind in rows:
            txt.insert('end', line + '\n', kind)
        txt.config(state='disabled')
        txt.see('end')

    # ── Campaign reference lines, shared by both charts ──────────────────────

    def _line_rows(self, ui):
        """[(price, color, text, bold)] — the armed lines are bold, the
        projected ones soft, exactly like the grid window's watch levels."""
        rows = []
        lines = ui.get('lines') or {}
        c = ui.get('campaign') or {}
        avg = ui.get('avg_cost') or 0
        no_army = ui.get('buy_state') != 'OK'

        for key in _BUY_KEYS:
            e = lines.get(key)
            if not e:
                continue
            armed = bool(e.get('armed'))
            text = (f"{e['label']} {fmt_price(e['price'], self.ccy)} "
                    f"× {e['qty']}")
            color = _CLR['buy'] if armed else _CLR['buy_soft']
            if armed and no_army:
                color, text = _NO_ARMY_CLR, f'✕ {text} (no army)'
            rows.append((e['price'], color, text, armed))

        if avg > 0:
            rows.append((avg, _CLR['avg'],
                         f'avg {fmt_price(avg, self.ccy)}', True))

        sell = lines.get('exit') or lines.get('pexit')
        if sell:
            armed = bool(sell.get('armed'))
            rows.append((sell['price'],
                         _CLR['exit'] if armed else _CLR['exit_soft'],
                         f"{sell['label']} "
                         f"{fmt_price(sell['price'], self.ccy)} × {sell['qty']}"
                         + ('' if armed else ' (projected)'),
                         armed))

        if c.get('vantage'):
            v = c['vantage']
            src = _VANTAGE_SRC.get(c.get('vantage_src'), '')
            rows.append((v, _CLR['vantage'],
                         f'vantage {fmt_price(v, self.ccy)} ({src})', False))
        return rows

    def _refresh_candles(self):
        """The candle panel draws the SAME rows as the live chart. Bold rows
        (the armed buy, the average, the exit) always show; soft projections
        only when they fall inside the candles' own price range."""
        ui = self.ui
        ohlc = self.ap['ohlc']() or []
        refs = []
        if ui:
            span = [b['low'] for b in ohlc] + [b['high'] for b in ohlc]
            if ui.get('price'):
                span.append(ui['price'])
            lo, hi = (min(span), max(span)) if span else (None, None)
            for price, color, text, bold in self._line_rows(ui):
                if not (bold or (lo is not None and lo <= price <= hi)):
                    continue
                refs.append({'label': text, 'price': price, 'color': color,
                             'dash': ((2, 4) if bold else (3, 6)),
                             'width': (1.8 if bold else 1.1)})
        self.candle_panel.update(ohlc=ohlc, ref_lines=refs,
                                 current=(ui or {}).get('price'),
                                 day_v_avg=(ui or {}).get('day_v_avg'))

    # ── Live chart ────────────────────────────────────────────────────────────

    def _draw(self):
        c = self.canvas
        c.delete('all')
        w, h = c.winfo_width(), c.winfo_height()
        if w < 160 or h < 120:
            return
        ui = self.ui
        if not ui:
            c.create_text(w / 2, h / 2, text='Campaign bot is off',
                          font=_F_STAT, fill='#888')
            return

        ticks = ui.get('ticks') or []
        rows = self._line_rows(ui)

        # Scale to the action: the ticks, the current price, and the lines the
        # campaign is actually working between. Far projections only draw when
        # they already fall inside that range.
        prices = [p for _, p in ticks]
        if ui.get('price'):
            prices.append(ui['price'])
        for price, _clr, _txt, bold in rows:
            if bold:
                prices.append(price)
        ladder = buy_lines(ui)
        if len(ladder) > 1:
            prices.append(ladder[1]['price'])     # one projection of headroom
        if not prices:
            c.create_text(w / 2, h / 2, text='waiting for the first tick…',
                          font=_F_STAT, fill='#888')
            return

        left, top, bottom = 90, 18, 32
        wanted_right = required_label_pad(
            [r[2] for r in rows], self._ref_font.measure,
            minimum=200, padding=16)
        max_right = max(100, w - left - 80)
        right = min(wanted_right, max_right)
        narrow_labels = wanted_right > max_right
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

        # Shade the corridor the campaign lives in: armed buy → exit.
        lines = ui.get('lines') or {}
        lo = ladder[0]['price'] if ladder else None
        sell = lines.get('exit') or lines.get('pexit')
        hi = sell['price'] if sell else None
        if lo and hi and hi > lo:
            c.create_rectangle(left, y_of(min(hi, p_max)),
                               left + cw, y_of(max(lo, p_min)),
                               fill=_CLR['zone'], outline='')

        for i in range(5):
            p = p_min + p_rng * i / 4
            y = y_of(p)
            c.create_line(left, y, left + cw, y, fill='#E8E8E8')
            c.create_text(left - 5, y, text=fmt_price(p, self.ccy),
                          anchor='e', font=_F_AXIS, fill='#888')

        # Time axis from the first tick (min span 30 min so early ticks don't
        # smear across the full width).
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
            text_x, anchor, wrap_width = label_x, 'w', None
            if narrow_labels:
                text_x, anchor, wrap_width = bounded_label_layout(
                    w, self._ref_font.measure(text))
            options = {'text': text, 'anchor': anchor,
                       'font': _F_REF, 'fill': color}
            if wrap_width is not None:
                options['width'] = wrap_width
            c.create_text(text_x, y, **options)

        for price, color, text, bold in rows:
            if price < p_min or price > p_max:
                continue
            ref(price, color, text,
                dash=(None if bold else (2, 5)),
                width=(2.4 if bold else 1.2))

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
