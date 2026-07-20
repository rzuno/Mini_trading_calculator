"""Autopilot window — the Daily v^ grid cockpit for one watched stock.

Opened by the card's big Autopilot button. Opening it arms WATCH mode: the
stock is polled every 10 s and this window follows every tick. The bot runs
the Daily v^ Linear Weighted Grid (the DAILY ADVENTURE) — it does NOT use
the card gear system; the cards stay the manual-trading aid.

    WATCH  a due grid transition lights the trigger row; the Buy / Sell
           buttons let the commander fire that same order manually.
    LIVE   the bot rebalances by itself the moment a grid level is
           crossed; allowed only during regular market hours (drops back
           to WATCH at the close). While LIVE, the manual buttons rest.

Left: the live tick curve inside the grid — every level line from -5 to +5
(clipped to the visible range), the anchor, and the two ADJACENT watch
lines emphasized. Right: the adventure status snapshot, today's fill log,
and the 5-day candle panel.
When the reserve army cannot fund the next buy level, no popup fires: the
lower watch line is drawn muted (✕ … no army) and the trigger row explains
why the manual Buy button is off — the buy fires by itself when cash
returns.

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
_F_LOG   = ('Consolas', 11)
_F_LOG_B = ('Consolas', 11, 'bold')

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
    'state_on': '#0033AA',
}
_PHASE_TXT = {'REGULAR': ('OPEN (regular)', '#007700'),
              'PRE':     ('pre-market', '#B8860B'),
              'AFTER':   ('after-market', '#B8860B'),
              'CLOSED':  ('CLOSED', '#888888')}
_LIVE_BG = '#CC0000'
_NO_ARMY_CLR = '#999999'      # muted buy line when the army can't fund it
_STATE_CLR = {'WATCHING': '#0033AA', 'ORDER_PENDING': '#B8860B',
              'WAIT_OPEN': '#666666', 'ARMING': '#666666'}


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


def adventure_status_text(ui, currency):
    """Read-only adventure snapshot shown above the day's fill list.
    Presentation only — never becomes an event, never touches state."""
    ui = ui or {}
    state = ui.get('state') or 'ARMING'
    mode = ui.get('mode') or 'WATCH'
    shares = int(ui.get('shares') or 0)
    price = ui.get('price')
    parts = [f'ADVENTURE — {state} · {mode}']

    if ui.get('grid_ready'):
        gap = ui.get('gap_mode') or 'NONE'
        gap_txt = '' if gap == 'NONE' else f'  ({gap.lower()} gap open)'
        parts.append(f"Anchor: {fmt_price(ui.get('anchor'), currency)}"
                     f'{gap_txt}')
        parts.append(f"Level: {ui.get('level', 0):+d}   "
                     f"Inventory: {shares:,} sh "
                     f"(base {ui.get('base_inventory', 0):,}, "
                     f"unit {ui.get('unit_qty', 0):,})")
        if price:
            parts.append(f'Now: {fmt_price(price, currency)}')
        up, dn = next_transitions(ui)
        if up:
            parts.append(f"Up   L{up['level']:+d} @ "
                         f"{fmt_price(up['price'], currency)} → "
                         f"{up['side']} {up['qty']:,}")
        else:
            parts.append(f'Up   edge of the zone (+{LEVEL_CAP})')
        if dn:
            tail = ('  [NO ARMY]' if ui.get('buy_state') == 'EXHAUSTED'
                    and dn['side'] == 'BUY' else '')
            parts.append(f"Down L{dn['level']:+d} @ "
                         f"{fmt_price(dn['price'], currency)} → "
                         f"{dn['side']} {dn['qty']:,}{tail}")
        else:
            parts.append(f'Down edge of the zone (-{LEVEL_CAP})')
        net = (ui.get('sell_value') or 0) - (ui.get('buy_value') or 0)
        parts.append(f"Today: buys {fmt_price(ui.get('buy_value') or 0, currency)}"
                     f" · sells {fmt_price(ui.get('sell_value') or 0, currency)}"
                     f" · net {fmt_price(net, currency)}"
                     f" ({ui.get('fills') or 0} fills)")
    else:
        parts.append('Grid not built yet — the adventure starts at the '
                     'regular open.')
        if ui.get('reference_close'):
            parts.append(f"Prev close: "
                         f"{fmt_price(ui['reference_close'], currency)}")
        parts.append(f'Holding: {shares:,} sh')
        if price:
            parts.append(f'Now: {fmt_price(price, currency)}')

    orders = ui.get('orders') or []
    if orders:
        sides = sorted({o.get('side') for o in orders if o.get('side')})
        if sides:
            parts.append('Resting on Toss: ' + '/'.join(sides))
    return '\n'.join(parts)


class AutopilotWindow:
    """Live cockpit for one watched stock. Subscribes to the controller and
    redraws on every 10-second tick."""

    def __init__(self, parent, ticker, currency, ap_ctx):
        self.ap = ap_ctx
        self.ticker = ticker
        self.ccy = currency
        self.ui = None

        self.win = tk.Toplevel(parent)
        name = STOCK_NAMES.get(ticker, ticker)
        self.win.title(f'{name} — Autopilot (Daily v^ grid)')
        self.win.geometry('1620x720')
        self.win.minsize(1280, 560)
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

        # ── Adventure summary line ───────────────────────────────────────────
        head2 = tk.Frame(self.win, padx=12)
        head2.pack(fill='x')
        self._adv_lbl = tk.Label(head2, text='', font=_F_BTN, fg='#4B0082',
                                 anchor='w')
        self._adv_lbl.pack(side='left')

        self._info_lbl = tk.Label(self.win, text='', font=_F_INFO, fg='#333',
                                  anchor='w')
        self._info_lbl.pack(fill='x', padx=14)
        self._status_lbl = tk.Label(self.win, text='', font=_F_INFO,
                                    fg='#4B0082', anchor='w')
        self._status_lbl.pack(fill='x', padx=14)

        # ── Trigger row: due-transition indicator + manual Buy/Sell/Cancel ───
        trig = tk.Frame(self.win, padx=12, pady=4)
        trig.pack(fill='x')
        self._trig_lbl = tk.Label(trig, text='no level crossed', font=_F_STAT,
                                  fg='#888', width=60, anchor='w')
        self._trig_lbl.pack(side='left')
        self._buy_btn = tk.Button(trig, text='Buy', font=_F_STAT, width=8,
                                  state='disabled',
                                  command=lambda: self._do_fire('BUY'))
        self._buy_btn.pack(side='left', padx=(6, 6))
        self._sell_btn = tk.Button(trig, text='Sell', font=_F_STAT, width=8,
                                   state='disabled',
                                   command=lambda: self._do_fire('SELL'))
        self._sell_btn.pack(side='left', padx=(0, 6))
        self._cancel_btn = tk.Button(trig, text='Cancel', font=_F_STAT,
                                     width=8, state='disabled',
                                     command=self._do_cancel)
        self._cancel_btn.pack(side='left', padx=(0, 12))
        self._fire_msg = tk.Label(trig, text='', font=_F_INFO, fg='#333')
        self._fire_msg.pack(side='left')

        # ── Body: live grid chart | status + fills | 5-day panel ─────────────
        body = tk.Frame(self.win)
        body.pack(fill='both', expand=True, padx=12, pady=(6, 12))

        self.canvas = tk.Canvas(body, bg='white', highlightthickness=0)
        self.canvas.pack(side='left', fill='both', expand=True)
        self.canvas.bind('<Configure>', lambda e: self._draw())

        side = tk.Frame(body)
        side.pack(side='left', fill='y', padx=(10, 0))
        tk.Label(side, text='Adventure status & fills', font=_F_STAT
                 ).pack(anchor='w')
        self._adv_status_lbl = tk.Label(
            side, text='', width=38, font=_F_LOG, anchor='nw', justify='left',
            wraplength=360, bg='#F5F5F5', relief='groove', bd=1,
            padx=6, pady=5)
        self._adv_status_lbl.pack(fill='x', pady=(4, 4))
        self._log_txt = tk.Text(side, width=38, font=_F_LOG, state='disabled',
                                wrap='word', bg='#FAFAFA', relief='groove', bd=1)
        self._log_txt.pack(fill='y', expand=True)
        self._log_txt.tag_configure('BUY', foreground=_CLR['dn'])
        self._log_txt.tag_configure('SELL', foreground=_CLR['up'])
        self._log_txt.tag_configure('OTHER', foreground='#555555')
        self._log_txt.tag_configure('HEADER', foreground='#333333',
                                    font=_F_LOG_B)

        self.candle_panel = CandlePanel(body, currency, width=470)
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

    def _do_fire(self, side):
        trig = ((self.ui or {}).get('trigger') or {}).get(side)
        if not trig:
            return
        q, p = trig['qty'], trig['price']
        text = (f"{trig.get('label', side)}\n\n"
                f"{side} {q} @ {fmt_price(p, self.ccy)}\n\n"
                f"Send this order to Toss now?")
        if not messagebox.askyesno(f'Manual {side}', text, parent=self.win):
            return
        ok, msg = self.ap['manual_fire'](side)
        self._fire_msg.config(text=msg, fg=('green' if ok else 'red'))

    def _do_cancel(self):
        if not messagebox.askyesno(
                'Cancel orders',
                f'Cancel ALL resting Toss orders for {self.ticker}?',
                parent=self.win):
            return
        ok, msg = self.ap['cancel_all']()
        self._fire_msg.config(text=msg, fg=('green' if ok else 'red'))

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
        self._status_lbl.config(
            text=f"{ui.get('status', '')}    poll {ui.get('ts', '--')}")
        self._update_trigger_row(ui)
        self._adv_status_lbl.config(text=adventure_status_text(ui, self.ccy))
        self._fill_log(ui)
        self._draw()
        self._refresh_candles()

    def _adventure_text(self, ui):
        if not ui.get('grid_ready'):
            ref = ui.get('reference_close')
            tail = (f' · prev close {fmt_price(ref, self.ccy)}' if ref else '')
            return f'daily adventure — waiting for the regular open{tail}'
        gap = ui.get('gap_mode') or 'NONE'
        gap_txt = '' if gap == 'NONE' else f' · {gap.lower()} gap'
        net = (ui.get('sell_value') or 0) - (ui.get('buy_value') or 0)
        return (f"anchor {fmt_price(ui.get('anchor'), self.ccy)}{gap_txt}"
                f" · L{ui.get('level', 0):+d}"
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
        bp = ui.get('buying_power')
        if bp is not None:
            parts.append(f'reserve {fmt_price(bp, self.ccy)}')
        if ui.get('buy_state') == 'EXHAUSTED':
            parts.append('next buy level unfunded — waits for the army')
        return '      '.join(parts)

    def _update_trigger_row(self, ui):
        trig = ui.get('trigger') or {}
        buy_t, sell_t = trig.get('BUY'), trig.get('SELL')
        mode = ui.get('mode', 'WATCH')
        manual_ok = mode != 'LIVE'
        self._buy_btn.config(state=('normal' if buy_t and manual_ok
                                    else 'disabled'))
        self._sell_btn.config(state=('normal' if sell_t and manual_ok
                                     else 'disabled'))
        self._cancel_btn.config(state=('normal' if ui.get('orders')
                                       else 'disabled'))
        if sell_t:
            self._trig_lbl.config(
                text=f"▲ L{sell_t.get('level', 0):+d} crossed → SELL "
                     f"{sell_t['qty']} @ {fmt_price(sell_t['price'], self.ccy)}"
                     + ('' if manual_ok else '   (LIVE fires it)'),
                fg=_CLR['up'])
        elif buy_t:
            self._trig_lbl.config(
                text=f"▼ L{buy_t.get('level', 0):+d} crossed → BUY "
                     f"{buy_t['qty']} @ {fmt_price(buy_t['price'], self.ccy)}"
                     + ('' if manual_ok else '   (LIVE fires it)'),
                fg=_CLR['dn'])
        elif ui.get('trigger_note'):
            # A level IS crossed but cannot be traded (e.g. no reserve
            # army) — say exactly why the manual button is off.
            self._trig_lbl.config(text=ui['trigger_note'], fg='#B8860B')
        elif ui.get('orders'):
            sides = {o.get('side') for o in ui['orders']}
            self._trig_lbl.config(
                text='● ' + '/'.join(s.lower() for s in sides if s)
                     + ' order resting on Toss', fg='#0033AA')
        else:
            self._trig_lbl.config(text='no level crossed', fg='#888')

    # ── Grid line rows shared by both charts ─────────────────────────────────

    def _grid_rows(self, ui):
        """[(price, color, text, bold)] for every grid level. The adjacent
        watch levels are bold and carry the trade they would fire; far
        levels are soft; an unfundable down-line is muted (✕ … no army)."""
        rows = []
        lvl = ui.get('level') or 0
        no_army = ui.get('buy_state') == 'EXHAUSTED'
        up, dn = next_transitions(ui)
        for g in (ui.get('grid') or []):
            k, price, target = g['level'], g['price'], g['target']
            adj = None
            if up and k == up['level']:
                adj = up
            elif dn and k == dn['level']:
                adj = dn
            if k == 0:
                color = _CLR['anchor']
                text = f'A {fmt_price(price, self.ccy)}'
            else:
                color = (_CLR['up'] if k > 0 else _CLR['dn'])
                soft = (_CLR['up_soft'] if k > 0 else _CLR['dn_soft'])
                text = f'L{k:+d} {fmt_price(price, self.ccy)}'
                if adj is None:
                    color = soft
            if adj is not None and adj['side'] != '—':
                text += f"  {adj['side']} {adj['qty']}"
                if adj['side'] == 'BUY' and no_army:
                    color = _NO_ARMY_CLR
                    text = f'✕ {text} (no army)'
            elif k == lvl:
                text += '  ← here'
            rows.append((price, color, text, adj is not None or k == 0))
        return rows

    def _refresh_candles(self):
        ui = self.ui
        ohlc = self.ap['ohlc']() or []
        refs = []
        if ui and ui.get('grid_ready'):
            up, dn = next_transitions(ui)
            refs.append({'label': f"A {fmt_price(ui.get('anchor'), self.ccy)}",
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

    def _fill_log(self, ui):
        events = ui.get('events') or []
        txt = self._log_txt
        txt.config(state='normal')
        txt.delete('1.0', 'end')
        txt.insert('end', "TODAY'S ADVENTURE FILLS\n", 'HEADER')
        if not events:
            txt.insert('end', '(no fills yet today — the log\n'
                              'clears at each daily rebase)\n')
        for e in events:
            price = e.get('price')
            p = fmt_price(price, self.ccy) if price else '--'
            line = (f"{e['ts']}  {e['kind']:<9} {e['qty']:+d} @ {p}"
                    f"  → {e['shares']} sh")
            kind = ('BUY' if str(e['kind']).startswith('BUY')
                    else 'SELL' if str(e['kind']).startswith('SELL')
                    else 'OTHER')
            txt.insert('end', line + '\n', kind)
        txt.config(state='disabled')
        txt.see('end')

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
