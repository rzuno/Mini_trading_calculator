import tkinter as tk
from core.calc import (
    display_name, normalize_load_pct, sell_pct_color, gap_color,
    load_gap_color, fmt_price,
    calc_load_ladder, calc_buy_cascade, calc_sell_tiers, calc_gap_rate,
    select_auto_gear, effective_entry_gear, RE_BAIT_GEAR,
    AUTO_GEARS, BUY_GEAR_PCTS, gear_for_pct,
    gear_for_sell_pct, gear_label, clamp_gear, gear_button_color,
    gear_button_fg, gear_detail, gear_menu_label,
)
from gui.stepper import Stepper

# Readable blue for buy-trigger values (matches the chart's rescue lines)
_BUY_FG = '#3366CC'
_SELL_FG = '#CC0000'
_STATUS_FG = '#4B0082'  # indigo

# -- Fonts (1.3x scale for QHD) ----------------------------------------------
_F_NAME_DEPLOYED = ('Segoe UI', 13, 'bold')  # deployed stocks are bold
_F_NAME_EMPTY    = ('Segoe UI', 13)          # empty stocks are plain
_F_LBL  = ('Segoe UI', 12)
_F_VAL  = ('Segoe UI', 12)
_F_OUT  = ('Segoe UI', 13, 'bold')
_F_SM   = ('Segoe UI', 10)
_F_SM_B = ('Segoe UI', 10, 'bold')
_F_BTN  = ('Segoe UI', 11, 'bold')
_F_STATUS = ('Segoe UI', 12, 'bold')
_F_GEAR_BADGE = ('Segoe UI', 18, 'bold')

# Greys for a "muted" (state-inactive but informational) gear box
_MUTE_TITLE = '#C0C0C0'
_MUTE_BG    = '#F0F0F0'
_MUTE_FG    = '#9A9A9A'
_AP_BUTTON_TOP_GAP = 18       # one heading-line below the gear-box titles


class StockRow:
    """One unified stock card, used for both EMPTY and DEPLOYED stocks.

    ONE gear system drives everything: a single drop percent (G1 -4% … G5
    -8%) is both the LOAD trigger (below the VANTAGE point — prev session
    close, or the same-day sell fill) and the chase/rescue trigger (below the
    avg cost). The autopilot follows exactly these lines (``line_config``).

    * DEPLOYED — the buy ladder is the rescue cascade from the real avg cost
      (Buy 1/2/3) and the sells are real.
    * EMPTY — the buy ladder is the projected entry: Load (initial unit) plus
      two projected rescues (Buy 1/2), and the sells are *pseudo* (computed as
      if the load had filled).

    The parent grids ``self.frame``; the row does not place itself. The program
    still keeps deployed and empty rows in two separate lists.
    """

    def __init__(self, parent, row_num: int, pos: dict, deployed: bool,
                 get_unit_cash, on_compute=None, editable=True,
                 on_autopilot=None):
        self.deployed       = deployed
        self.editable       = editable
        self._order_locked  = False   # True while live orders rest (gear frozen)
        self.ticker         = pos['ticker']
        self.tier           = pos.get('tier', 'Major')
        self.currency       = 'KRW' if self.ticker.endswith('.KS') else 'USD'
        self.get_unit_cash  = get_unit_cash
        self.on_autopilot   = on_autopilot
        self._on_compute_cb = on_compute
        self.current_price  = None
        self.vantage        = None    # prev session close / same-day sell fill
        self.vantage_src    = 'close' # 'close' | 'sell' (re-bait → G1 pinned)
        self.volatility     = None
        self._army_pct      = None
        self._gap           = None    # current vs anchor %, for ordering
        self._computing     = False
        self._syncing_gear  = False
        self._order_side    = None
        self._base_gear = None        # pure volatility gear
        self._eff_gear  = None        # after heavy-unit / re-bait rules

        # -- Card frame --------------------------------------------------------
        self.frame = tk.Frame(parent, bd=1, relief='groove', padx=8, pady=3)

        # -- Input variables ---------------------------------------------------
        self.shares_var   = tk.StringVar(
            value=str(pos.get('shares', 0)) if pos.get('shares', 0) > 0 else '')
        self.avg_cost_var = tk.StringVar(value=self._fmt_init(pos.get('avg_cost', 0)))

        pct_init = normalize_load_pct(pos.get('buy_pct')
                                      or pos.get('load_gear', 5))
        init_gear = gear_for_pct(pct_init)
        self.pct_var = tk.IntVar(value=AUTO_GEARS[init_gear]['pct'])
        self.buy_gear_var = tk.StringVar(
            value=self._gear_button_text(init_gear))

        # Default sell-tier activation: only T2 on; others off but clickable.
        self.t_active = [
            tk.BooleanVar(value=bool(pos.get(f't{i+1}_active', d)))
            for i, d in enumerate((False, True, False))]
        self.t_pct = [
            tk.IntVar(value=int(pos.get(f't{i+1}_pct', [4, 6, 8][i])))
            for i in range(3)]

        self.auto_var = tk.BooleanVar(value=bool(pos.get('auto_mode', True)))

        # -- Output variables --------------------------------------------------
        self.current_var  = tk.StringVar(value='--')
        self.gap_var      = tk.StringVar(value='--')
        self.cost_var     = tk.StringVar(value='--')
        self.army_pct_var = tk.StringVar(value='')
        self.vol_var      = tk.StringVar(value='V --')
        self.buy_info_var = [tk.StringVar(value='--') for _ in range(3)]
        self.t_info_var   = [tk.StringVar(value='--') for _ in range(3)]
        # Buy-ladder row labels differ by state (Load/Buy 1/Buy 2 vs Buy 1/2/3)
        self._buy_lbl_var = [tk.StringVar(value='') for _ in range(3)]

        self._build_title_row(row_num)
        self._build_body()

        # -- Reactive traces (after all widgets exist) -------------------------
        self.shares_var.trace_add('write', lambda *_: self._on_input_change())
        self.avg_cost_var.trace_add('write', lambda *_: self._on_input_change())
        for i in range(3):
            self.t_active[i].trace_add('write', lambda *_: self._on_input_change())
            self.t_pct[i].trace_add('write', lambda *_: self._on_input_change())
        self.auto_var.trace_add('write', lambda *_: self._on_input_change())

        # Initial styling pass (rows are computed on the first price fetch).
        self._refresh_gear_styles()
        self._update_auto_btn()
        self._update_vol_label()

    # ── Build: title row ──────────────────────────────────────────────────────

    def _build_title_row(self, row_num):
        r0 = tk.Frame(self.frame)
        r0.pack(fill='x', pady=(0, 2))

        name = display_name(self.ticker)
        if self.ticker.endswith('.KS'):
            name += ' (KR)'
        self._disp_name = name
        # Bold marks DEPLOYED (not KR — KR already shows the (KR) tag).
        name_font = _F_NAME_DEPLOYED if self.deployed else _F_NAME_EMPTY
        self._name_lbl = tk.Label(r0, text=f"{row_num}. {name}",
                                  font=name_font, anchor='w')
        self._name_lbl.pack(side='left')

        # Right side: "[DEPLOYED] [ordered status]" — the bait-hit tag is gone
        # (§30.8 point 1): triggers show only as ladder-number colors, so the
        # title keeps room for Avg Cost / Shares.
        self._status_frame = tk.Frame(r0)
        self._status_frame.pack(side='right', padx=(6, 2))
        self._status_lbl = tk.Label(self._status_frame, text='',
                                    font=_F_STATUS)
        self._status_lbl.pack(side='left', padx=(2, 0))
        if self.deployed:
            tk.Label(r0, text='DEPLOYED', font=_F_SM_B, fg='#0033AA'
                     ).pack(side='right', padx=(4, 0))

        # Army % (deployed only)
        tk.Label(r0, textvariable=self.army_pct_var,
                 font=_F_SM, fg='#888').pack(side='left', padx=(3, 6))

        self.auto_btn = tk.Checkbutton(
            r0, variable=self.auto_var, indicatoron=False, takefocus=0,
            width=7, font=_F_BTN, bd=1)
        self.auto_btn.pack(side='left', padx=(0, 6))
        tk.Label(r0, textvariable=self.vol_var, font=_F_SM, fg='#666'
                 ).pack(side='left', padx=(0, 12))

        ent_state = 'normal' if self.editable else 'readonly'
        tk.Label(r0, text='Avg Cost:', font=_F_LBL).pack(side='left')
        self.avg_entry = tk.Entry(r0, textvariable=self.avg_cost_var,
                                  width=10, justify='right', font=_F_VAL,
                                  state=ent_state)
        self.avg_entry.pack(side='left', padx=(2, 8))
        self.avg_entry.bind('<FocusOut>', lambda e: self._format_avg())

        tk.Label(r0, text='Shares:', font=_F_LBL).pack(side='left')
        tk.Entry(r0, textvariable=self.shares_var,
                 width=6, justify='right', font=_F_VAL, state=ent_state
                 ).pack(side='left', padx=(2, 8))

        if not self.deployed and self.editable:
            tk.Label(r0, text='(fill & Save to deploy)', font=_F_SM,
                     fg='#AAA').pack(side='left', padx=(2, 0))

    # ── Build: body (left stats+ladder | right gear boxes) ────────────────────

    def _build_body(self):
        body = tk.Frame(self.frame)
        body.pack(fill='x')

        left = tk.Frame(body)
        left.pack(side='left')

        # Stats: anchor value | current | gap
        stats = tk.Frame(left)
        stats.pack(fill='x')

        def _out(label, var, **kw):
            tk.Label(stats, text=label, fg='#888', font=_F_SM).pack(side='left')
            lbl = tk.Label(stats, textvariable=var, font=_F_OUT, **kw)
            lbl.pack(side='left', padx=(2, 12))
            return lbl

        cost_label = 'Total Cost:' if self.deployed else 'Vantage:'
        _out(cost_label, self.cost_var)
        _out('Current:', self.current_var)
        self.current_lbl = stats.winfo_children()[-1]  # the Current value label
        self.gap_lbl = _out('Gap:', self.gap_var, width=9)

        # Buy/sell ladder: 3 columns, buy on top, matching sell beneath.
        ladder = tk.Frame(left)
        ladder.pack(fill='x', pady=(2, 0))

        self.buy_info_lbl = []
        self.t_info_lbl   = []
        for col in range(3):
            cell = tk.Frame(ladder)
            cell.grid(row=0, column=col, sticky='w', padx=(0, 14))

            bf = tk.Frame(cell)
            bf.pack(anchor='w')
            tk.Label(bf, textvariable=self._buy_lbl_var[col], fg='#888',
                     font=_F_SM, width=7, anchor='w').pack(side='left')
            blbl = tk.Label(bf, textvariable=self.buy_info_var[col],
                            font=_F_OUT, fg=_BUY_FG)
            blbl.pack(side='left')
            self.buy_info_lbl.append(blbl)

            sf = tk.Frame(cell)
            sf.pack(anchor='w')
            tk.Label(sf, text=f'Sell T{col+1}:', fg='#888', font=_F_SM,
                     width=7, anchor='w').pack(side='left')
            slbl = tk.Label(sf, textvariable=self.t_info_var[col], font=_F_OUT)
            slbl.pack(side='left')
            self.t_info_lbl.append(slbl)

        # Gear boxes on the right: unified Load/Buy | Sell
        self._build_gear_boxes(body)

    def _build_gear_boxes(self, body):
        wrap = tk.Frame(body)
        wrap.pack(side='left', anchor='n', padx=(16, 0))
        self._gear_title = {}

        # -- Unified load/buy gear (compact menu) ------------------------------
        buy_box = tk.Frame(wrap)
        buy_box.pack(side='left', anchor='n', padx=(0, 12))
        buy_head = tk.Frame(buy_box)
        buy_head.grid(row=0, column=0, sticky='w')
        self._gear_title['load_buy'] = tk.Label(
            buy_head, text='Buy' if self.deployed else 'Load',
            font=_F_SM, fg='#888')
        self._gear_title['load_buy'].pack(side='left')
        menu_row = tk.Frame(buy_box)
        menu_row.grid(row=1, column=0, sticky='w')
        self.buy_menu = tk.Menubutton(
            menu_row, textvariable=self.buy_gear_var, font=_F_SM,
            width=12, relief='raised', takefocus=0)
        self._buy_menu_default_bg = self.buy_menu.cget('bg')
        self._buy_menu_default_fg = self.buy_menu.cget('fg')
        menu = tk.Menu(self.buy_menu, tearoff=0)
        for gear in sorted(AUTO_GEARS):
            menu.add_command(
                label=gear_menu_label(gear, self.deployed),
                command=lambda g=gear: self._on_buy_gear_select(g))
        self.buy_menu.config(menu=menu)
        self.buy_menu.pack(side='left')
        badge_row = tk.Frame(buy_box)
        badge_row.grid(row=2, column=0, sticky='w', pady=(3, 0))
        tk.Label(badge_row, text='gear:', font=_F_SM, fg='#888'
                 ).pack(side='left', padx=(0, 3))
        self.gear_badge = tk.Canvas(
            badge_row, width=46, height=46, highlightthickness=0, bd=0)
        self.gear_badge.pack(side='left')

        # -- Sell gear (3 tiers: toggle + stepper, T3 on top) ------------------
        sell_box = tk.Frame(wrap)
        sell_box.pack(side='left', anchor='n')
        self._gear_title['sell'] = tk.Label(sell_box, text='Sell',
                                            font=_F_SM, fg='#888')
        self._gear_title['sell'].grid(row=0, column=0, columnspan=3, sticky='w')
        self._steppers = [None, None, None]
        self._tier_checks = [None, None, None]
        for disp, ti in enumerate([2, 1, 0]):
            grow = disp + 1
            tk.Label(sell_box, text=f'T{ti+1}', font=_F_SM
                     ).grid(row=grow, column=0, sticky='e', padx=(0, 1))
            chk = tk.Checkbutton(sell_box, variable=self.t_active[ti],
                                 takefocus=0, bd=0, pady=0)
            chk.grid(row=grow, column=1)
            self._tier_checks[ti] = chk
            step = Stepper(sell_box, self.t_pct[ti], 1, 20,
                           entry_width=3, value_font=_F_SM, btn_font=_F_SM)
            step.grid(row=grow, column=2, sticky='w')
            self._steppers[ti] = step

        # -- Big Autopilot button (right of the sell gear) ---------------------
        # Opens the Autopilot live window; its color IS the autopilot status.
        if self.on_autopilot:
            self.ap_btn = tk.Button(
                wrap, text='AUTOPILOT', font=_F_SM_B, width=10,
                height=3, bd=2, takefocus=0,
                command=lambda: self.on_autopilot(self.ticker))
            self._ap_btn_default_bg = self.ap_btn.cget('bg')
            # Sit one label-line below the gear-box headings so the large
            # button aligns with the actual controls instead of their titles.
            self.ap_btn.pack(side='left', anchor='n', padx=(12, 0),
                             pady=(_AP_BUTTON_TOP_GAP, 0))

    # ── Formatting ────────────────────────────────────────────────────────────

    def _fmt_init(self, avg):
        if avg is None or avg <= 0:
            return ''
        return f"{avg:,.0f}" if self.currency == 'KRW' else f"{avg:,.2f}"

    def _format_avg(self):
        try:
            v = float(self.avg_cost_var.get().replace(',', ''))
            self.avg_cost_var.set(
                f"{v:,.0f}" if self.currency == 'KRW' else f"{v:,.2f}")
        except ValueError:
            pass

    # ── Gear value getters ────────────────────────────────────────────────────

    def _get_pct(self) -> int:
        """THE drop percent: one number drives the load AND the chase."""
        try:
            v = int(self.pct_var.get())
        except (tk.TclError, ValueError):
            v = 5
        return v if v in BUY_GEAR_PCTS else 5

    def _gear_button_text(self, gear: int):
        return gear_detail(gear)

    def _current_gear(self):
        if self.auto_var.get() and self._eff_gear:
            return self._eff_gear
        return gear_for_pct(self._get_pct())

    def _set_gear(self, gear: int):
        gear = clamp_gear(gear)
        pct = AUTO_GEARS[gear]['pct']
        self._syncing_gear = True
        try:
            if self._get_pct() != pct:
                self.pct_var.set(pct)
        finally:
            self._syncing_gear = False
        self.buy_gear_var.set(self._gear_button_text(gear))

    def _sync_buy_menu(self):
        self.buy_gear_var.set(self._gear_button_text(self._current_gear()))

    # ── Auto / manual ─────────────────────────────────────────────────────────

    def _update_auto_btn(self):
        if self.auto_var.get():
            self.auto_btn.config(text='AUTO', fg='white', bg='#2E8B57',
                                 selectcolor='#2E8B57')
        else:
            self.auto_btn.config(text='MANUAL', fg='black', bg='#E6B800',
                                 selectcolor='#E6B800')

    def _update_vol_label(self):
        vol = self.volatility
        if vol is None:
            self.vol_var.set('V --')
        elif not self.auto_var.get():
            self.vol_var.set(f'V {vol:.1f}%')
        elif not self.deployed and self.vantage_src == 'sell':
            self.vol_var.set(f'V {vol:.1f}% → G1 재입질')
        else:
            base = select_auto_gear(vol)
            eff = self._eff_gear or base
            heavy = ' ▲heavy' if eff > base else ''
            self.vol_var.set(f'V {vol:.1f}% → G{eff}{heavy}')

    def _apply_auto(self):
        """In auto mode the gear comes from the 5-day volatility. Empty cards
        additionally apply the two exceptions: the heavy-unit minimum ENTRY
        gear (one share too big for a shallow bait) and the same-day re-bait
        (vantage = today's sell fill → gear 1). Frozen while orders rest so
        the ordered gear can't be overwritten."""
        if self._order_locked or not self.auto_var.get():
            return
        if not self.deployed and self.vantage_src == 'sell':
            gear = RE_BAIT_GEAR          # same-day re-bait: gear 1 for today
        elif self.volatility is None:
            return
        elif self.deployed:
            gear = select_auto_gear(self.volatility)
        else:
            ref_price = self.current_price or self.vantage
            gear = effective_entry_gear(self.volatility, ref_price,
                                        self.get_unit_cash())
        self._base_gear = select_auto_gear(self.volatility)
        self._eff_gear = gear
        g = AUTO_GEARS[gear]
        if self._get_pct() != g['pct']:
            self.pct_var.set(g['pct'])
        self._sync_buy_menu()
        for i in range(3):
            if self.t_pct[i].get() != g['tiers'][i]:
                self.t_pct[i].set(g['tiers'][i])

    def _current_gear_labels(self):
        gear = self._current_gear()
        if self.auto_var.get() and self._eff_gear:
            sell_gear = self._eff_gear
        else:
            sell_gear = gear_for_sell_pct(self.t_pct[1].get())
        return gear, sell_gear

    def _refresh_gear_title_text(self):
        load_buy_gear, sell_gear = self._current_gear_labels()
        title = 'Buy' if self.deployed else 'Load'
        self._gear_title['load_buy'].config(
            text=f"{title} ({gear_label(load_buy_gear)})")
        self._gear_title['sell'].config(text=f"Sell ({gear_label(sell_gear)})")

    # ── Gear styling (enabled + state muting) ─────────────────────────────────

    def set_gear_locked(self, locked: bool):
        """Freeze (or release) every gear control while live orders rest."""
        self._order_locked = bool(locked)
        self._refresh_gear_styles()

    def set_order_state(self, side):
        """Reflect live Toss orders in the title status and lock resting gear."""
        self._order_side = side
        self._refresh_status()
        self.set_gear_locked(side is not None)

    def _refresh_status(self):
        """Title status shows live resting orders only (no bait-hit tag)."""
        if self._order_side == 'BUY':
            self._status_lbl.config(text='buy ordered', fg=_SELL_FG)
        elif self._order_side == 'SELL':
            self._status_lbl.config(text='sell ordered', fg=_BUY_FG)
        else:
            self._status_lbl.config(text='')

    def _refresh_gear_styles(self):
        self._sync_buy_menu()
        self._refresh_gear_title_text()
        # While orders are live the projection must not move: lock everything.
        if self._order_locked:
            self.auto_btn.config(state='disabled')   # can't flip AUTO/MANUAL
            self.buy_menu.config(state='disabled')
            self._update_buy_color()
            for s in self._steppers:
                s.set_enabled(False)
            for ch in self._tier_checks:
                if ch:
                    ch.config(state='disabled')
            self._gear_title['load_buy'].config(fg='#888')
            self._gear_title['sell'].config(fg=_MUTE_TITLE)
            return

        self.auto_btn.config(state='normal')
        for ch in self._tier_checks:
            if ch:
                ch.config(state='normal')

        auto = self.auto_var.get()

        # Unified load/buy gear: auto drives it; manual can choose G1..G5.
        self.buy_menu.config(state='disabled' if auto else 'normal')
        self._update_buy_color()
        self._gear_title['load_buy'].config(fg='#888')

        # Sell gear: same muting rule; tier toggles always editable.
        sell_muted = not self.deployed
        for i in range(3):
            self._steppers[i].set_enabled(not auto)
            self._color_spn(self._steppers[i], self.t_pct[i].get(),
                            muted=sell_muted)
        self._gear_title['sell'].config(fg=_MUTE_TITLE if sell_muted else '#888')

    def _draw_gear_badge(self, gear):
        if not hasattr(self, 'gear_badge'):
            return
        gear = clamp_gear(gear)
        bg = gear_button_color(gear)
        fg = gear_button_fg(gear)
        self.gear_badge.delete('all')
        self.gear_badge.create_oval(
            3, 3, 43, 43, fill=bg, outline='#555555', width=1)
        self.gear_badge.create_text(
            23, 23, text=str(gear), fill=fg, font=_F_GEAR_BADGE)

    def _update_buy_color(self, muted=False):
        gear = self._current_gear()
        default_bg = getattr(self, '_buy_menu_default_bg', '#F0F0F0')
        default_fg = getattr(self, '_buy_menu_default_fg', 'black')
        self.buy_menu.config(
            fg=default_fg, bg=default_bg,
            activeforeground=default_fg, activebackground=default_bg,
            disabledforeground='#888888', font=_F_SM)
        self._draw_gear_badge(gear)

    def _color_spn(self, stepper, pct, muted=False):
        if muted:
            stepper.set_value_color(_MUTE_BG, _MUTE_FG)
        else:
            c = sell_pct_color(float(pct))
            stepper.set_value_color(c, 'white' if float(pct) >= 7 else 'black')

    def _on_buy_gear_select(self, gear):
        self._set_gear(gear)
        self._update_buy_color()
        self._on_input_change()

    # ── Compute ───────────────────────────────────────────────────────────────

    def _on_input_change(self):
        if not self._computing and not self._syncing_gear:
            self.compute()
            if self._on_compute_cb:
                self._on_compute_cb()

    def compute(self):
        if self._computing:
            return
        self._computing = True
        try:
            self._compute_impl()
        finally:
            self._computing = False

    def _enforce_order(self):
        if self._order_locked:
            return
        try:
            p = [self.t_pct[i].get() for i in range(3)]
        except Exception:
            return
        if p[1] <= p[0]:
            self.t_pct[1].set(p[0] + 1); p[1] = p[0] + 1
        if p[2] <= p[1]:
            self.t_pct[2].set(p[1] + 1)

    def _compute_impl(self):
        self._apply_auto()
        self._enforce_order()
        self._refresh_gear_styles()
        self._update_auto_btn()
        self._update_vol_label()

        ccy  = self.currency
        pcts = [self.t_pct[i].get() for i in range(3)]
        acts = [self.t_active[i].get() for i in range(3)]
        pct = self._get_pct()

        try:
            shares = int(self.shares_var.get().replace(',', '') or 0)
        except ValueError:
            shares = 0
        try:
            avg_cost = float(self.avg_cost_var.get().replace(',', '') or 0)
        except ValueError:
            avg_cost = 0.0

        if self.deployed:
            buy_labels = ['Buy 1', 'Buy 2', 'Buy 3']
            anchor_price = avg_cost if avg_cost > 0 else None
            if shares > 0 and avg_cost > 0:
                self.cost_var.set(fmt_price(shares * avg_cost, ccy))
                buy_lines  = calc_buy_cascade(shares, avg_cost, pct, 3)
                sell_lines = calc_sell_tiers(shares, avg_cost, pcts, acts)
            else:
                self.cost_var.set('--')
                buy_lines  = [{'price': None, 'qty': None} for _ in range(3)]
                sell_lines = [{'price': None, 'qty': None} for _ in range(3)]
        else:
            buy_labels = ['Load', 'Buy 1', 'Buy 2']
            if self.vantage and self.vantage > 0:
                buy_lines, load_price, load_shares = calc_load_ladder(
                    self.vantage, pct, self.get_unit_cash(), rescues=2)
                anchor_price = load_price if load_price > 0 else None
                sell_lines = calc_sell_tiers(load_shares, load_price, pcts, acts)
                tag = ' (재입질)' if self.vantage_src == 'sell' else ''
                self.cost_var.set(fmt_price(self.vantage, ccy) + tag)
            else:
                anchor_price = None
                buy_lines  = [{'price': None, 'qty': None} for _ in range(3)]
                sell_lines = [{'price': None, 'qty': None} for _ in range(3)]
                self.cost_var.set('--')

        # Current price + gap
        if self.current_price:
            self.current_var.set(fmt_price(self.current_price, ccy))
        else:
            self.current_var.set('--')

        # Deployed: gap = current vs avg cost (P&L red/blue).
        # Empty: gap = current vs the VANTAGE point — today's move from the
        # entry origin. The bait sits at -pct, so this gap says how much of
        # the day's drop is still needed before the next battle starts.
        if self.deployed and self.current_price and anchor_price:
            gap = calc_gap_rate(self.current_price, anchor_price)
            self._gap = gap
            self.gap_var.set(f"{gap:+.2f}%")
            self.gap_lbl.config(fg=gap_color(gap))
        elif (not self.deployed and self.current_price
                and self.vantage and self.vantage > 0):
            gap = (self.current_price - self.vantage) / self.vantage * 100.0
            self._gap = gap
            self.gap_var.set(f"{gap:+.2f}%")
            self.gap_lbl.config(fg=load_gap_color(gap, pct))
        else:
            self._gap = None
            self.gap_var.set('--')
            self.gap_lbl.config(fg='black')

        # Empty stocks turn the Current value green once price is at/below the
        # load trigger (the entry condition is met).
        if (not self.deployed and anchor_price and self.current_price
                and self.current_price <= anchor_price):
            self.current_lbl.config(fg='#006400')
        else:
            self.current_lbl.config(fg='black')

        # Fill the ladder. A line is "triggered" once the current price crosses
        # it (buy: current <= line; sell: current >= line). Empty-card sells are
        # projections only, so they stay informational instead of alarming.
        cur = self.current_price
        for i in range(3):
            self._buy_lbl_var[i].set(f'{buy_labels[i]}:')
            e = buy_lines[i]
            if e['price'] is not None:
                self.buy_info_var[i].set(f"{fmt_price(e['price'], ccy)} × {e['qty']}")
                hit = cur is not None and cur <= e['price']
                self.buy_info_lbl[i].config(fg=(_BUY_FG if hit else 'black'))
            else:
                self.buy_info_var[i].set('--')
                self.buy_info_lbl[i].config(fg='#CCC')
            s = sell_lines[i]
            if s['price'] is not None:
                self.t_info_var[i].set(f"{fmt_price(s['price'], ccy)} × {s['qty']}")
                hit = self.deployed and cur is not None and cur >= s['price']
                self.t_info_lbl[i].config(fg=(_SELL_FG if hit else 'black'))
            else:
                self.t_info_var[i].set('--')
                self.t_info_lbl[i].config(fg='#CCC')


    # ── Public API ────────────────────────────────────────────────────────────

    def set_row_num(self, n: int):
        self._name_lbl.config(text=f"{n}. {self._disp_name}")

    # Big Autopilot button styling per status (None = off).
    _AP_STYLES = {
        None:    ('AUTOPILOT',           None,      'black'),
        'WATCH': ('AUTOPILOT\nWATCH',    '#3366CC', 'white'),
        'LIVE':  ('AUTOPILOT\nLIVE',     '#CC0000', 'white'),
    }

    def set_autopilot(self, key):
        """Color the big Autopilot button to the status (None/'WATCH'/'LIVE')."""
        if not hasattr(self, 'ap_btn'):
            return
        text, bg, fg = self._AP_STYLES.get(key, self._AP_STYLES[None])
        bg = bg or self._ap_btn_default_bg
        self.ap_btn.config(text=text, bg=bg, fg=fg,
                           activebackground=bg, activeforeground=fg)

    def line_config(self) -> dict:
        """The gear numbers the autopilot must follow — exactly what this
        card shows right now (one unified logic, the card is the source)."""
        try:
            tier_pcts = [int(self.t_pct[i].get()) for i in range(3)]
        except (tk.TclError, ValueError):
            tier_pcts = list(AUTO_GEARS[self._current_gear()]['tiers'])
        return {
            'gear': self._current_gear(),
            'pct': self._get_pct(),
            'tier_pcts': tier_pcts,
            'tier_actives': [bool(self.t_active[i].get()) for i in range(3)],
        }

    def current_shares(self) -> int:
        try:
            return int(self.shares_var.get().replace(',', '') or 0)
        except ValueError:
            return 0

    def set_army_pct(self, pct):
        self._army_pct = pct
        self.army_pct_var.set(
            f"({pct:.1f}%)" if (pct is not None and self.deployed) else '')

    def update_live(self, price=None, vantage=None, volatility=None,
                    vantage_src=None):
        if price is not None:
            self.current_price = price
        if vantage is not None:
            self.vantage = vantage
        if vantage_src is not None:
            self.vantage_src = vantage_src
        if volatility is not None:
            self.volatility = volatility

    def get_state(self) -> dict:
        try:
            shares = int(self.shares_var.get().replace(',', '') or 0)
        except ValueError:
            shares = 0
        try:
            avg_cost = float(self.avg_cost_var.get().replace(',', '') or 0)
        except ValueError:
            avg_cost = 0.0
        return {
            'ticker':     self.ticker,
            'tier':       self.tier,
            'is_deployed': self.deployed,
            'shares':     shares,
            'avg_cost':   avg_cost,
            'cost_basis': shares * avg_cost,
            'load_gear':  self._get_pct(),
            'buy_pct':    self._get_pct(),
            't1_pct':     self.t_pct[0].get(),
            't2_pct':     self.t_pct[1].get(),
            't3_pct':     self.t_pct[2].get(),
            't1_active':  self.t_active[0].get(),
            't2_active':  self.t_active[1].get(),
            't3_active':  self.t_active[2].get(),
            'auto_mode':  self.auto_var.get(),
        }
