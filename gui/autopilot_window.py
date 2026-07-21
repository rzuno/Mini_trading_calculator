"""Autopilot window — the Daily v^ grid cockpit for one watched stock.

Opened by the card's big Autopilot button. Opening it arms WATCH mode: the
stock is polled every 5 s and this window follows every tick. The bot runs
the Daily v^ Linear Weighted Grid (the DAILY ADVENTURE) — it does NOT use
the card gear system; the cards stay the manual-trading aid.

    WATCH  polling + grid + fill detection only; nothing is placed.
    LIVE   the bot rebalances by itself the moment a grid level is
           crossed; allowed only during regular market hours (drops back
           to WATCH at the close).

Layout is ONE information banner over TWO charts — nothing else:

    header   name · state · market phase · LIVE
    banner   adventure line (anchor @ L±n (A) · level · unit · base · net)
             inventory · reserve · ▲▼ the two next transitions
             engine status + poll time
             today's fills (only when there are any)
    charts   live tick curve inside the grid  |  5-day candle panel

The old right-hand status/fills column and the manual Buy/Sell/Cancel
buttons are gone (the controller still supports manual fire for the
future; the UI just doesn't show it). When the army cannot fund the next
buy level, the line is drawn muted (✕ … no army) and the status says why.

Closing the window in WATCH mode stops the polling; in LIVE the autopilot
keeps running in the background (the card button stays colored).
"""

import time as _time
from datetime import datetime as _dt

import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox

from core.calc import STOCK_NAMES, fmt_price
from core.autopilot import LEVEL_CAP
from gui.candle_chart import (CandlePanel, bounded_label_layout,
                              required_label_pad)

_F_TITLE = ('Segoe UI', 17, 'bold')
_F_STAT  = ('Segoe UI', 13)
_F_BTN   = ('Segoe UI', 13, 'bold')
_F_INFO  = ('Segoe UI', 12)
_F_AXIS  = ('Segoe UI', 10)
_F_REF   = ('Segoe UI', 11, 'bold')
_F_FILLS = ('Consolas', 11)

# KR color language: red = the upper (SELL) half, blue = the lower (BUY)
# half. Soft shades are the far, not-yet-adjacent levels.
_CLR = {
    'up':      '#CC3333',
    'dn':      '#3366CC',
    'up_soft': '#E4A9A9',
    'dn_soft': '#A9C4E4',
    'anchor':  '#E08000',
    'path':    '#1A1A1A',
    'now':     '#CC0000',
    'zone':    '#F4F1FA',
}
_PHASE_TXT = {'REGULAR': ('OPEN (regular)', '#007700'),
              'PRE':     ('pre-market', '#B8860B'),
              'AFTER':   ('after-market', '#B8860B'),
              'CLOSED':  ('CLOSED', '#888888')}
_LIVE_BG = '#CC0000'
_NO_ARMY_CLR = '#999999'      # muted buy line when the army can't fund it
_STATE_CLR = {'WATCHING': '#0033AA', 'ORDER_PENDING': '#B8860B',
              'WAIT_OPEN': '#666666', 'ARMING': '#666666'}

_FILLS_SHOWN = 8              # newest fills listed in the banner


def _grid_by_level(ui):
    return {g['level']: g for g in (ui.get('grid') or [])}


def next_transitions(ui):
    """(up, down) — each {'level','price','qty','side'} or None. qty is the
    inventory delta the adjacent transition would trade right now."""
    grid = _grid_by_level(ui)
    lvl = ui.get('level') or 0
    shares = int(ui.get('shares') or 0)
    out = []
    for step in (+1, -1):
        k = lvl + step
        g = grid.get(k)
        if g is None or abs(k) > LEVEL_CAP:
            out.append(None)
            continue
        delta = g['target'] - shares
        side = 'SELL' if delta < 0 else ('BUY' if delta > 0 else '—')
        out.append({'level': k, 'price': g['price'], 'qty': abs(delta),
                    'side': side})
    return out[0], out[1]


def next_line(ui, currency):
    """One banner line naming the two adjacent transitions: '▲ … · ▼ …'."""
    if not ui.get('grid_ready'):
        return ''
    up, dn = next_transitions(ui)
    parts = []
    if up:
        act = ('watch only' if up['side'] == '—'
               else f"{up['side']} {up['qty']:,}")
        parts.append(f"▲ L{up['level']:+d} @ "
                     f"{fmt_price(up['price'], currency)} → {act}")
    else:
        parts.append(f'▲ edge of the zone (+{LEVEL_CAP})')
    if dn:
        act = ('watch only' if dn['side'] == '—'
               else f"{dn['side']} {dn['qty']:,}")
        tail = (' [NO ARMY]' if ui.get('buy_state') == 'EXHAUSTED'
                and dn['side'] == 'BUY' else '')
        parts.append(f"▼ L{dn['level']:+d} @ "
                     f"{fmt_price(dn['price'], currency)} → {act}{tail}")
    else:
        parts.append(f'▼ edge of the zone (-{LEVEL_CAP})')
    return '      '.join(parts)


def fills_text(ui, currency, limit=_FILLS_SHOWN):
    """Today's fills for the banner — '' when the adventure has none yet.
    Newest last; long days are truncated to the most recent `limit`."""
    events = ui.get('events') or []
    if not events:
        return ''
    shown = events[-limit:]
    head = f"오늘 fills ({len(events)})"
    if len(events) > len(shown):
        head += f' — last {len(shown)}'
    lines = [head + ':']
    for e in shown:
        price = e.get('price')
        p = fmt_price(price, currency) if price else '--'
        lines.append(f"  {e['ts']}  {e['kind']:<9} {e['qty']:+d} @ {p}"
                     f"  → {e['shares']} sh")
    return '\n'.join(lines)


class AutopilotWindow:
    """Live cockpit for one watched stock. Subscribes to the controller and
    redraws on every 5-second tick."""

    def __init__(self, parent, ticker, currency, ap_ctx):
        self.ap = ap_ctx
        self.ticker = ticker
        self.ccy = currency
        self.ui = None

        self.win = tk.Toplevel(parent)
        name = STOCK_NAMES.get(ticker, ticker)
        self.win.title(f'{name} — Autopilot (Daily v^ grid)')
        self.win.geometry('1260x860')
        self.win.minsize(980, 620)
        self._ref_font = tkfont.Font(root=self.win, font=_F_REF)

        # ── Header: title/state left, market phase + LIVE right ──────────────
        head = tk.Frame(self.win, padx=12, pady=8)
        head.pack(fill='x')
        tk.Label(head, text=f'{name}  — Daily v^ Adventure', font=_F_TITLE
                 ).pack(side='left')
        self._state_lbl = tk.Label(head, text='', font=_F_TITLE)
        self._state_lbl.pack(side='left', padx=(16, 0))

        self._live_btn = tk.Button(head, text='LIVE', font=_F_BTN, width=8,
                                   command=self._on_live)
        self._live_btn.pack(side='right', padx=(6, 0))
        self._default_bg = self._live_btn.cget('bg')
        self._phase_lbl = tk.Label(head, text='', font=_F_STAT)
        self._phase_lbl.pack(side='right', padx=(0, 8))

        # ── The unified information banner ───────────────────────────────────
        self._adv_lbl = tk.Label(self.win, text='', font=_F_BTN,
                                 fg='#4B0082', anchor='w')
        self._adv_lbl.pack(fill='x', padx=12)
        self._info_lbl = tk.Label(self.win, text='', font=_F_INFO, fg='#333',
                                  anchor='w')
        self._info_lbl.pack(fill='x', padx=14)
        self._next_lbl = tk.Label(self.win, text='', font=_F_INFO, fg='#333',
                                  anchor='w')
        self._next_lbl.pack(fill='x', padx=14)
        self._status_lbl = tk.Label(self.win, text='', font=_F_INFO,
                                    fg='#4B0082', anchor='w')
        self._status_lbl.pack(fill='x', padx=14)
        self._fills_lbl = tk.Label(self.win, text='', font=_F_FILLS,
                                   fg='#333', anchor='w', justify='left')
        # packed/unpacked on demand in _update_fills

        # ── Body: live grid chart | 5-day candle panel ───────────────────────
        self._body = tk.Frame(self.win)
        self._body.pack(fill='both', expand=True, padx=12, pady=(6, 12))

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
        # background (the card's Autopilot button stays colored).
        if self.ap['mode_of']() == 'WATCH':
            self.ap['disable']()

    # ── Controls ──────────────────────────────────────────────────────────────

    def _on_live(self):
        if self.ap['mode_of']() == 'LIVE':       # click again → back to WATCH
            self.ap['set_mode']('WATCH')
            return
        ui = self.ap['ui_state']() or {}
        up, dn = next_transitions(ui)
        detail = [f'Go LIVE on {self.ticker}?', '',
                  'The Daily v^ grid bot rebalances by ITSELF whenever an '
                  'adjacent level is crossed:']
        if up and up['side'] != '—':
            detail.append(f"  up   L{up['level']:+d}: {up['side']} "
                          f"{up['qty']} @ {fmt_price(up['price'], self.ccy)}")
        if dn and dn['side'] != '—':
            detail.append(f"  down L{dn['level']:+d}: {dn['side']} "
                          f"{dn['qty']} @ {fmt_price(dn['price'], self.ccy)}")
        detail += [
            '',
            f"Zone -{LEVEL_CAP}…+{LEVEL_CAP} around anchor "
            f"{fmt_price(ui.get('anchor'), self.ccy)}, unit "
            f"{ui.get('unit_qty', 0)} sh. One order at a time; every fill "
            f"re-aims the two adjacent levels; V and ^ are both harvested. "
            f"LIVE drops back to WATCH when the market closes.",
        ]
        if not messagebox.askyesno('Autopilot LIVE', '\n'.join(detail),
                                   parent=self.win):
            return
        ok, msg = self.ap['set_mode']('LIVE')
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
        self._state_lbl.config(text=state,
                               fg=_STATE_CLR.get(state, '#666'))

        mode = ui.get('mode', 'WATCH')
        self._live_btn.config(
            bg=(_LIVE_BG if mode == 'LIVE' else self._default_bg),
            fg=('white' if mode == 'LIVE' else 'black'))

        phase = ui.get('phase', 'CLOSED')
        txt, pclr = _PHASE_TXT.get(phase, (phase, '#888'))
        mkt = 'KR' if self.ticker.endswith('.KS') else 'US'
        self._phase_lbl.config(text=f'{mkt} market: {txt}', fg=pclr)

        self._adv_lbl.config(text=self._adventure_text(ui))
        self._info_lbl.config(text=self._info_text(ui))
        self._next_lbl.config(text=next_line(ui, self.ccy))
        self._status_lbl.config(
            text=f"{ui.get('status', '')}    poll {ui.get('ts', '--')}")
        self._update_fills(ui)
        self._draw()
        self._refresh_candles()

    def _adventure_text(self, ui):
        if not ui.get('grid_ready'):
            ref = ui.get('reference_close')
            tail = (f' · prev close {fmt_price(ref, self.ccy)}' if ref else '')
            return f'daily adventure — waiting for the regular open{tail}'
        gap = ui.get('gap_mode') or 'NONE'
        gap_txt = '' if gap == 'NONE' else f' · {gap.lower()} gap'
        a_lvl = ui.get('anchor_level') or 0
        net = (ui.get('sell_value') or 0) - (ui.get('buy_value') or 0)
        return (f"anchor {fmt_price(ui.get('anchor'), self.ccy)}"
                f" @ L{a_lvl:+d} (A){gap_txt}"
                f" · now L{ui.get('level', 0):+d}"
                f" · unit {ui.get('unit_qty', 0)} sh"
                f" · base {ui.get('base_inventory', 0)} sh"
                f" · today net {fmt_price(net, self.ccy)}"
                f" ({ui.get('fills') or 0} fills)")

    def _info_text(self, ui):
        parts = []
        shares = ui.get('shares') or 0
        price = ui.get('price')
        avg = ui.get('avg_cost') or 0
        unit = ui.get('unit_cash') or 0
        parts.append(f'inventory {shares:,} sh'
                     + (f' @ {fmt_price(avg, self.ccy)} avg' if avg else ''))
        if shares and price and unit > 0:
            parts.append(f'≈ {shares * price / unit:,.2f} u deployed')
        if price:
            parts.append(f'now {fmt_price(price, self.ccy)}')
        bp = ui.get('buying_power')
        if bp is not None:
            parts.append(f'reserve {fmt_price(bp, self.ccy)}')
        orders = ui.get('orders') or []
        if orders:
            sides = sorted({o.get('side') for o in orders if o.get('side')})
            parts.append('● resting: ' + '/'.join(sides))
        return '      '.join(parts)

    def _update_fills(self, ui):
        text = fills_text(ui, self.ccy)
        if text:
            self._fills_lbl.config(text=text)
            if not self._fills_lbl.winfo_ismapped():
                self._fills_lbl.pack(fill='x', padx=14, pady=(2, 0),
                                     before=self._body)
        elif self._fills_lbl.winfo_ismapped():
            self._fills_lbl.pack_forget()

    # ── Grid line rows shared by both charts ─────────────────────────────────

    def _grid_rows(self, ui):
        """[(price, color, text, bold)] for every grid level. The anchor
        level carries the (A) marker — L+0 (A) on a normal day, L∓1 (A) on
        a gap day where the OPEN is the anchor. Adjacent watch levels are
        bold and carry the trade they would fire; far levels are soft; an
        unfundable down-line is muted (✕ … no army)."""
        rows = []
        lvl = ui.get('level') or 0
        a_lvl = ui.get('anchor_level') or 0
        no_army = ui.get('buy_state') == 'EXHAUSTED'
        up, dn = next_transitions(ui)
        for g in (ui.get('grid') or []):
            k, price, target = g['level'], g['price'], g['target']
            is_anchor = bool(g.get('anchor', k == a_lvl))
            adj = None
            if up and k == up['level']:
                adj = up
            elif dn and k == dn['level']:
                adj = dn
            text = f'L{k:+d}'
            if is_anchor:
                text += ' (A)'
            text += f' {fmt_price(price, self.ccy)}'
            if is_anchor:
                color = _CLR['anchor']
            else:
                # Side colors relative to the ANCHOR level, not zero.
                color = (_CLR['up'] if k > a_lvl else _CLR['dn'])
                if adj is None:
                    color = (_CLR['up_soft'] if k > a_lvl
                             else _CLR['dn_soft'])
            if adj is not None and adj['side'] != '—':
                text += f"  {adj['side']} {adj['qty']}"
                if adj['side'] == 'BUY' and no_army:
                    color = _NO_ARMY_CLR
                    text = f'✕ {text} (no army)'
            elif k == lvl:
                text += '  ← here'
            rows.append((price, color, text, adj is not None or is_anchor))
        return rows

    def _refresh_candles(self):
        ui = self.ui
        ohlc = self.ap['ohlc']() or []
        refs = []
        if ui and ui.get('grid_ready'):
            up, dn = next_transitions(ui)
            a_lvl = ui.get('anchor_level') or 0
            refs.append({'label': f"L{a_lvl:+d} (A) "
                                  f"{fmt_price(ui.get('anchor'), self.ccy)}",
                         'price': ui.get('anchor'), 'color': _CLR['anchor'],
                         'dash': (5, 4), 'width': 2})
            no_army = ui.get('buy_state') == 'EXHAUSTED'
            for t in (up, dn):
                if not t or t['side'] == '—':
                    continue
                color = _CLR['up'] if t['side'] == 'SELL' else _CLR['dn']
                label = (f"L{t['level']:+d} {t['side']} {t['qty']} @ "
                         f"{fmt_price(t['price'], self.ccy)}")
                if t['side'] == 'BUY' and no_army:
                    color, label = _NO_ARMY_CLR, f'✕ {label} (no army)'
                refs.append({'label': label, 'price': t['price'],
                             'color': color, 'dash': (2, 4), 'width': 1.6})
        self.candle_panel.update(ohlc=ohlc, ref_lines=refs,
                                 current=(ui or {}).get('price'))

    # ── Live chart ────────────────────────────────────────────────────────────

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

        ticks = ui.get('ticks') or []
        grid_rows = self._grid_rows(ui)
        up, dn = next_transitions(ui)

        # Scale to the action: ticks, current, anchor-side context — the two
        # watch lines plus one level beyond each. Far levels only draw when
        # they fall inside this range (no 30%-tall flat charts).
        prices = [p for _, p in ticks]
        if ui.get('price'):
            prices.append(ui['price'])
        grid = _grid_by_level(ui)
        lvl = ui.get('level') or 0
        for k in (lvl, lvl + 1, lvl - 1, lvl + 2, lvl - 2):
            g = grid.get(k)
            if g:
                prices.append(g['price'])
        if not prices:
            c.create_text(w / 2, h / 2, text='waiting for the first tick…',
                          font=_F_STAT, fill='#888')
            return

        left, top, bottom = 90, 18, 32
        wanted_right = required_label_pad(
            [row[2] for row in grid_rows], self._ref_font.measure,
            minimum=185, padding=16)
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

        # Shade the corridor between the two adjacent watch lines.
        lo = dn['price'] if dn else None
        hi = up['price'] if up else None
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
            text_x, anchor, wrap_width = label_x, 'w', None
            if narrow_labels:
                text_x, anchor, wrap_width = bounded_label_layout(
                    w, self._ref_font.measure(text))
            options = {'text': text, 'anchor': anchor,
                       'font': _F_REF, 'fill': color}
            if wrap_width is not None:
                options['width'] = wrap_width
            c.create_text(text_x, y, **options)

        for price, color, text, bold in grid_rows:
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
