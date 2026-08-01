import tkinter as tk
from core.calc import (
    CAMPAIGN_CAP_UNITS, DEFAULT_EXIT_TIER, DEFAULT_GEAR, EXIT_TIERS, GEARS,
    RELOAD_DROP_PCT, calc_chase_cascade, calc_exit_lines, calc_gap_rate,
    calc_load_ladder, calc_reload_price, chase_drop, clamp_gear, clamp_tier,
    display_name, effective_entry_gear, exit_pct, fmt_price, gap_color,
    gear_button_color, gear_button_fg, gear_detail, gear_for_chase_pct,
    gear_label, gear_menu_label, gear_params, load_drop, load_gap_color,
    select_auto_gear, sell_pct_color, tier_for_exit_pct,
)

# Readable blue for buy-trigger values (matches the chart's chase lines)
_BUY_FG = '#3366CC'
_SELL_FG = '#CC0000'

# -- Fonts (1.3x scale for QHD) ----------------------------------------------
_F_NAME_DEPLOYED = ('Segoe UI', 13, 'bold')  # deployed stocks are bold
_F_NAME_EMPTY    = ('Segoe UI', 13)          # flat stocks are plain
_F_LBL  = ('Segoe UI', 12)
_F_VAL  = ('Segoe UI', 12)
_F_OUT  = ('Segoe UI', 13, 'bold')
_F_SM   = ('Segoe UI', 10)
_F_SM_B = ('Segoe UI', 10, 'bold')
_F_BTN  = ('Segoe UI', 11, 'bold')
_F_TINY = ('Segoe UI', 9)
_F_STATUS = ('Segoe UI', 12, 'bold')
_F_GEAR_BADGE = ('Segoe UI', 18, 'bold')

# Greys for a "muted" (state-inactive but informational) control
_MUTE_TITLE = '#C0C0C0'
_MUTE_BG    = '#F0F0F0'
_MUTE_FG    = '#9A9A9A'
_AP_BUTTON_TOP_GAP = 18       # one heading-line below the gear-box titles


class StockRow:
    """One Gearbox V-Commandos campaign card (both FLAT and DEPLOYED).

    ONE gearbox drives the card AND the campaign bot. A gear fixes three
    things for the whole campaign:

        LOAD    vantage (High5) × (1 - gear.load%)     ≈ 1 unit of cash
        CHASE   actual avg     × (1 - gear.chase%)     shares × gear.ratio
        EXIT    actual avg     × (1 + tier%)           the WHOLE position

    * DEPLOYED — the buy ladder is the real chase cascade off the broker's
      average cost (Chase 1/2/3); the exit row shows all three tiers of the
      gear with the SELECTED one armed for the full position.
    * FLAT — the ladder is the projected entry: Load (one unit) plus two
      projected chases, and the exit tiers are computed as if it had filled.

    The gear is chosen before the campaign and does not adapt inside it: AUTO
    picks it from the 5-day range while the card is FLAT and then pins it once
    deployed. Changing it on a deployed card is an explicit manual override
    (the bot logs GEAR_OVERRIDE).

    The parent grids ``self.frame``; the row does not place itself.
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
        self.vantage        = None    # High5 / prev close / same-day sell fill
        self.vantage_src    = 'high5' # 'high5' | 'close' | 'reload'
        self.volatility     = None
        self._army_pct      = None
        self._gap           = None    # current vs anchor %, for ordering
        self._computing     = False
        self._syncing_gear  = False
        self._order_side    = None
        self._base_gear = None        # pure volatility gear
        self._eff_gear  = None        # after the heavy-unit rule

        # -- Card frame --------------------------------------------------------
        self.frame = tk.Frame(parent, bd=1, relief='groove', padx=8, pady=3)

        # -- Input variables ---------------------------------------------------
        self.shares_var   = tk.StringVar(
            value=str(pos.get('shares', 0)) if pos.get('shares', 0) > 0 else '')
        self.avg_cost_var = tk.StringVar(value=self._fmt_init(pos.get('avg_cost', 0)))

        self.gear_var = tk.IntVar(value=self._initial_gear(pos))
        self.tier_var = tk.IntVar(value=self._initial_tier(pos, self.gear_var.get()))
        self.auto_var = tk.BooleanVar(value=bool(pos.get('auto_mode', True)))

        # -- Output variables --------------------------------------------------
        self.current_var  = tk.StringVar(value='--')
        self.gap_var      = tk.StringVar(value='--')
        self.cost_var     = tk.StringVar(value='--')
        self.army_pct_var = tk.StringVar(value='')
        self.vol_var      = tk.StringVar(value='V --')
        self.gear_txt_var = tk.StringVar(value='')
        self.buy_info_var = [tk.StringVar(value='--') for _ in range(3)]
        self.t_info_var   = [tk.StringVar(value='--') for _ in range(3)]
        # Buy-ladder row labels differ by state (Load/Chase 1/2 vs Chase 1/2/3)
        self._buy_lbl_var = [tk.StringVar(value='') for _ in range(3)]

        self._build_title_row(row_num)
        self._build_body()

        # -- Reactive traces (after all widgets exist) -------------------------
        self.shares_var.trace_add('write', lambda *_: self._on_input_change())
        self.avg_cost_var.trace_add('write', lambda *_: self._on_input_change())
        self.gear_var.trace_add('write', lambda *_: self._on_input_change())
        self.tier_var.trace_add('write', lambda *_: self._on_input_change())
        self.auto_var.trace_add('write', lambda *_: self._on_input_change())

        # Initial styling pass (rows are computed on the first price fetch).
        self._refresh_gear_styles()
        self._update_auto_btn()
        self._update_vol_label()

    # ── Initial gear / tier (with legacy CSV migration) ──────────────────────

    @staticmethod
    def _initial_gear(pos):
        if pos.get('gear'):
            return clamp_gear(pos['gear'])
        # Legacy positions carried a bait DROP PERCENT, not a gear number.
        legacy = pos.get('buy_pct') or pos.get('load_gear')
        return gear_for_chase_pct(legacy) if legacy else DEFAULT_GEAR

    @staticmethod
    def _initial_tier(pos, gear):
        if pos.get('exit_tier'):
            return clamp_tier(pos['exit_tier'])
        # Legacy files stored three tier percents plus on/off flags: the
        # lowest ACTIVE one becomes the selected tier.
        live = [pos.get(f't{i}_pct') for i in EXIT_TIERS
                if pos.get(f't{i}_active')]
        for pct in sorted(p for p in live if p):
            t = tier_for_exit_pct(gear, pct)
            if t:
                return t
        return DEFAULT_EXIT_TIER

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

        # Right side: "[DEPLOYED] [ordered status]".
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

        # Ladder: 3 columns. Buys on top (Load/Chase), the gear's three EXIT
        # tiers beneath — every tier sells the WHOLE position, only the
        # selected one is armed.
        ladder = tk.Frame(left)
        ladder.pack(fill='x', pady=(2, 0))

        self.buy_info_lbl = []
        self.t_info_lbl   = []
        self.t_head_lbl   = []
        for col in range(3):
            cell = tk.Frame(ladder)
            cell.grid(row=0, column=col, sticky='w', padx=(0, 14))

            bf = tk.Frame(cell)
            bf.pack(anchor='w')
            tk.Label(bf, textvariable=self._buy_lbl_var[col], fg='#888',
                     font=_F_SM, width=8, anchor='w').pack(side='left')
            blbl = tk.Label(bf, textvariable=self.buy_info_var[col],
                            font=_F_OUT, fg=_BUY_FG)
            blbl.pack(side='left')
            self.buy_info_lbl.append(blbl)

            sf = tk.Frame(cell)
            sf.pack(anchor='w')
            head = tk.Label(sf, text=f'T{col+1}:', fg='#888', font=_F_SM,
                            width=8, anchor='w')
            head.pack(side='left')
            self.t_head_lbl.append(head)
            slbl = tk.Label(sf, textvariable=self.t_info_var[col], font=_F_OUT)
            slbl.pack(side='left')
            self.t_info_lbl.append(slbl)

        # Gear + exit-tier boxes on the right
        self._build_gear_boxes(body)

    def _build_gear_boxes(self, body):
        wrap = tk.Frame(body)
        wrap.pack(side='left', anchor='n', padx=(16, 0))
        self._gear_title = {}

        # -- Gear picker (one gear = load %, chase %, add size, tier table) ----
        gear_box = tk.Frame(wrap)
        gear_box.pack(side='left', anchor='n', padx=(0, 12))
        self._gear_title['gear'] = tk.Label(gear_box, text='Gear',
                                            font=_F_SM, fg='#888')
        self._gear_title['gear'].grid(row=0, column=0, sticky='w')
        menu_row = tk.Frame(gear_box)
        menu_row.grid(row=1, column=0, sticky='w')
        self.gear_menu = tk.Menubutton(
            menu_row, textvariable=self.gear_txt_var, font=_F_SM,
            width=16, relief='raised', takefocus=0)
        self._gear_menu_default_bg = self.gear_menu.cget('bg')
        self._gear_menu_default_fg = self.gear_menu.cget('fg')
        menu = tk.Menu(self.gear_menu, tearoff=0)
        for gear in sorted(GEARS):
            menu.add_command(label=gear_menu_label(gear),
                             command=lambda g=gear: self._on_gear_select(g))
        self.gear_menu.config(menu=menu)
        self.gear_menu.pack(side='left')
        badge_row = tk.Frame(gear_box)
        badge_row.grid(row=2, column=0, sticky='w', pady=(3, 0))
        tk.Label(badge_row, text='gear:', font=_F_SM, fg='#888'
                 ).pack(side='left', padx=(0, 3))
        self.gear_badge = tk.Canvas(
            badge_row, width=46, height=46, highlightthickness=0, bd=0)
        self.gear_badge.pack(side='left')
        self.pin_lbl = tk.Label(badge_row, text='', font=_F_TINY, fg='#888')
        self.pin_lbl.pack(side='left', padx=(4, 0))

        # -- Exit tier (ONE tier, full position — no split) --------------------
        exit_box = tk.Frame(wrap)
        exit_box.pack(side='left', anchor='n')
        self._gear_title['exit'] = tk.Label(exit_box, text='Exit',
                                            font=_F_SM, fg='#888')
        self._gear_title['exit'].grid(row=0, column=0, columnspan=2, sticky='w')
        self._tier_btns = {}
        for disp, t in enumerate(reversed(EXIT_TIERS)):    # T3 on top
            btn = tk.Radiobutton(
                exit_box, variable=self.tier_var, value=t, indicatoron=False,
                width=8, font=_F_SM, bd=1, takefocus=0)
            btn.grid(row=disp + 1, column=0, sticky='w', pady=1)
            self._tier_btns[t] = btn

        # -- Autopilot buttons: the campaign bot (big) + the v^ grid (small) --
        if self.on_autopilot:
            ap = tk.Frame(wrap)
            ap.pack(side='left', anchor='n', padx=(12, 0),
                    pady=(_AP_BUTTON_TOP_GAP, 0))
            self.ap_btn = tk.Button(
                ap, text='V-COMMANDOS', font=_F_SM_B, width=12,
                height=3, bd=2, takefocus=0,
                command=lambda: self.on_autopilot(self.ticker, 'VCG'))
            self._ap_btn_default_bg = self.ap_btn.cget('bg')
            self.ap_btn.pack(side='top')
            self.grid_btn = tk.Button(
                ap, text='v^ grid', font=_F_TINY, width=12,
                height=1, bd=1, takefocus=0,
                command=lambda: self.on_autopilot(self.ticker, 'GRID'))
            self._grid_btn_default_bg = self.grid_btn.cget('bg')
            self.grid_btn.pack(side='top', pady=(2, 0))

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

    # ── Gear / tier getters ───────────────────────────────────────────────────

    def _get_gear(self) -> int:
        try:
            return clamp_gear(self.gear_var.get())
        except (tk.TclError, ValueError):
            return 3

    def _get_tier(self) -> int:
        try:
            return clamp_tier(self.tier_var.get())
        except (tk.TclError, ValueError):
            return DEFAULT_EXIT_TIER

    def _set_gear(self, gear: int):
        gear = clamp_gear(gear)
        if self._get_gear() == gear:
            return
        self._syncing_gear = True
        try:
            self.gear_var.set(gear)
        finally:
            self._syncing_gear = False

    def _gear_pinned(self) -> bool:
        """A live campaign keeps its gear: AUTO only picks while FLAT
        (manual §11 — no adaptive switching inside a campaign)."""
        return self.deployed

    def _on_gear_select(self, gear):
        self._set_gear(gear)
        self._on_input_change()

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
        elif self._gear_pinned():
            self.vol_var.set(f'V {vol:.1f}% · G{self._get_gear()} pinned')
        elif self.vantage_src == 'reload':
            self.vol_var.set(f'V {vol:.1f}% → reload -3%')
        else:
            base = select_auto_gear(vol)
            eff = self._eff_gear or base
            heavy = ' ▲heavy' if eff > base else ''
            self.vol_var.set(f'V {vol:.1f}% → G{eff}{heavy}')

    def _apply_auto(self):
        """AUTO picks the gear from the 5-day range while the card is FLAT,
        with the heavy-unit minimum entry gear applied on top. A DEPLOYED card
        keeps its campaign gear (a change there is a manual override), and
        everything freezes while live orders rest."""
        if self._order_locked or not self.auto_var.get():
            return
        self._base_gear = select_auto_gear(self.volatility)
        if self._gear_pinned():
            self._eff_gear = self._get_gear()
            return
        if self.volatility is None:
            return
        ref_price = self.current_price or self.vantage
        gear = effective_entry_gear(self.volatility, ref_price,
                                    self.get_unit_cash())
        self._eff_gear = gear
        self._set_gear(gear)

    def _refresh_gear_title_text(self):
        gear = self._get_gear()
        g = gear_params(gear)
        self._gear_title['gear'].config(
            text=f"Gear ({gear_label(gear)} {g['name']})")
        self._gear_title['exit'].config(
            text=f"Exit (T{self._get_tier()} "
                 f"+{exit_pct(gear, self._get_tier())}%)")
        self.gear_txt_var.set(gear_detail(gear))

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
        """Title status shows live resting orders only."""
        if self._order_side == 'BUY':
            self._status_lbl.config(text='buy ordered', fg=_SELL_FG)
        elif self._order_side == 'SELL':
            self._status_lbl.config(text='sell ordered', fg=_BUY_FG)
        else:
            self._status_lbl.config(text='')

    def _refresh_gear_styles(self):
        self._refresh_gear_title_text()
        self._draw_gear_badge(self._get_gear())
        self._style_tier_buttons()

        # While orders are live the projection must not move: lock everything.
        if self._order_locked:
            self.auto_btn.config(state='disabled')
            self.gear_menu.config(state='disabled')
            for b in self._tier_btns.values():
                b.config(state='disabled')
            self.pin_lbl.config(text='locked', fg='#CC0000')
            return

        self.auto_btn.config(state='normal')
        for b in self._tier_btns.values():
            b.config(state='normal')

        # AUTO only drives a FLAT card; a deployed campaign keeps its gear, so
        # the picker stays live there even in AUTO (that IS the override path).
        auto_locked = self.auto_var.get() and not self._gear_pinned()
        self.gear_menu.config(state='disabled' if auto_locked else 'normal')
        self.gear_menu.config(
            fg=self._gear_menu_default_fg, bg=self._gear_menu_default_bg,
            activeforeground=self._gear_menu_default_fg,
            activebackground=self._gear_menu_default_bg,
            disabledforeground='#888888', font=_F_SM)
        self.pin_lbl.config(
            text='pinned' if self._gear_pinned() else '',
            fg='#0033AA')

    def _style_tier_buttons(self):
        gear = self._get_gear()
        sel = self._get_tier()
        for t, btn in self._tier_btns.items():
            pct = exit_pct(gear, t)
            if t == sel:
                bg = sell_pct_color(float(pct))
                fg = 'white' if pct >= 7 else 'black'
            else:
                bg, fg = _MUTE_BG, _MUTE_FG
            btn.config(text=f'T{t} +{pct}%', bg=bg, fg=fg,
                       selectcolor=bg, activebackground=bg,
                       activeforeground=fg)

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

    def _compute_impl(self):
        self._apply_auto()
        self._refresh_gear_styles()
        self._update_auto_btn()
        self._update_vol_label()

        ccy  = self.currency
        gear = self._get_gear()
        tier = self._get_tier()

        try:
            shares = int(self.shares_var.get().replace(',', '') or 0)
        except ValueError:
            shares = 0
        try:
            avg_cost = float(self.avg_cost_var.get().replace(',', '') or 0)
        except ValueError:
            avg_cost = 0.0

        if self.deployed:
            buy_labels = ['Chase 1', 'Chase 2', 'Chase 3']
            anchor_price = avg_cost if avg_cost > 0 else None
            if shares > 0 and avg_cost > 0:
                self.cost_var.set(fmt_price(shares * avg_cost, ccy))
                buy_lines  = calc_chase_cascade(shares, avg_cost, gear, 3)
                exit_lines = calc_exit_lines(shares, avg_cost, gear)
            else:
                self.cost_var.set('--')
                buy_lines  = [{'price': None, 'qty': None} for _ in range(3)]
                exit_lines = [{'price': None, 'qty': None} for _ in range(3)]
        else:
            buy_labels = ['Load', 'Chase 1', 'Chase 2']
            reload_mode = (self.vantage_src == 'reload')
            if self.vantage and self.vantage > 0:
                if reload_mode:
                    # After a same-day full exit the entry is a flat -3% off
                    # the actual sell fill, not the gear's load drop.
                    load_price = calc_reload_price(self.vantage)
                    unit = self.get_unit_cash()
                    load_shares = (max(1, int(unit / load_price + 0.5))
                                   if unit > 0 and load_price > 0 else 0)
                    buy_lines = [{'price': load_price, 'qty': load_shares}]
                    buy_lines += calc_chase_cascade(load_shares, load_price,
                                                    gear, 2)
                else:
                    buy_lines, load_price, load_shares = calc_load_ladder(
                        self.vantage, gear, self.get_unit_cash(), chases=2)
                anchor_price = load_price if load_price > 0 else None
                exit_lines = calc_exit_lines(load_shares, load_price, gear)
                tag = ' (reload)' if reload_mode else ''
                self.cost_var.set(fmt_price(self.vantage, ccy) + tag)
            else:
                anchor_price = None
                buy_lines  = [{'price': None, 'qty': None} for _ in range(3)]
                exit_lines = [{'price': None, 'qty': None} for _ in range(3)]
                self.cost_var.set('--')

        # Current price + gap
        if self.current_price:
            self.current_var.set(fmt_price(self.current_price, ccy))
        else:
            self.current_var.set('--')

        # Deployed: gap = current vs avg cost (P&L red/blue).
        # Flat: gap = current vs the VANTAGE — how much of the pullback the
        # price still owes before the LOAD line is reached.
        trigger_pct = (RELOAD_DROP_PCT if self.vantage_src == 'reload'
                       else load_drop(gear))
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
            self.gap_lbl.config(fg=load_gap_color(gap, trigger_pct))
        else:
            self._gap = None
            self.gap_var.set('--')
            self.gap_lbl.config(fg='black')

        # Flat stocks turn the Current value green once the price is at/below
        # the LOAD trigger (the entry condition is met).
        if (not self.deployed and anchor_price and self.current_price
                and self.current_price <= anchor_price):
            self.current_lbl.config(fg='#006400')
        else:
            self.current_lbl.config(fg='black')

        # Fill the ladder. A line is "triggered" once the current price crosses
        # it (buy: current <= line; exit: current >= line). A flat card's exits
        # are projections, so they stay informational instead of alarming.
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

            s = exit_lines[i]
            armed = (i + 1) == tier
            self.t_head_lbl[i].config(
                text=f'▶ T{i+1}:' if armed else f'T{i+1}:',
                fg='#000' if armed else '#BBB',
                font=_F_SM_B if armed else _F_SM)
            if s['price'] is not None:
                qty = s['qty'] if armed else ''
                self.t_info_var[i].set(
                    f"{fmt_price(s['price'], ccy)} × {qty}" if armed
                    else fmt_price(s['price'], ccy))
                hit = (armed and self.deployed and cur is not None
                       and cur >= s['price'])
                self.t_info_lbl[i].config(
                    fg=(_SELL_FG if hit else ('black' if armed else '#AAA')),
                    font=_F_OUT if armed else _F_SM)
            else:
                self.t_info_var[i].set('--')
                self.t_info_lbl[i].config(fg='#CCC', font=_F_SM)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_row_num(self, n: int):
        self._name_lbl.config(text=f"{n}. {self._disp_name}")

    # Autopilot button styling per status (None = off).
    _AP_STYLES = {
        None:    ('V-COMMANDOS',        None,      'black'),
        'WATCH': ('V-COMMANDOS\nWATCH', '#3366CC', 'white'),
        'LIVE':  ('V-COMMANDOS\nLIVE',  '#CC0000', 'white'),
    }
    _GRID_STYLES = {
        None:    ('v^ grid',       None,      'black'),
        'WATCH': ('v^ WATCH',      '#3366CC', 'white'),
        'LIVE':  ('v^ LIVE',       '#CC0000', 'white'),
    }

    def set_autopilot(self, key, strategy='VCG'):
        """Color an autopilot button to its status (None/'WATCH'/'LIVE')."""
        if strategy == 'GRID':
            btn = getattr(self, 'grid_btn', None)
            styles, default = self._GRID_STYLES, self._grid_btn_default_bg
        else:
            btn = getattr(self, 'ap_btn', None)
            styles, default = self._AP_STYLES, self._ap_btn_default_bg
        if btn is None:
            return
        text, bg, fg = styles.get(key, styles[None])
        bg = bg or default
        btn.config(text=text, bg=bg, fg=fg,
                   activebackground=bg, activeforeground=fg)

    def line_config(self) -> dict:
        """The campaign parameters the bot must follow — exactly what this card
        shows right now. One gearbox, one source."""
        return {
            'gear':      self._get_gear(),
            'exit_tier': self._get_tier(),
            'cap_units': CAMPAIGN_CAP_UNITS,
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
        gear = self._get_gear()
        tier = self._get_tier()
        tiers = gear_params(gear)['tiers']
        return {
            'ticker':     self.ticker,
            'tier':       self.tier,
            'is_deployed': self.deployed,
            'shares':     shares,
            'avg_cost':   avg_cost,
            'cost_basis': shares * avg_cost,
            'gear':       gear,
            'exit_tier':  tier,
            # Legacy columns kept readable by older builds / scripts.
            'load_gear':  load_drop(gear),
            'buy_pct':    chase_drop(gear),
            't1_pct':     tiers[0],
            't2_pct':     tiers[1],
            't3_pct':     tiers[2],
            't1_active':  tier == 1,
            't2_active':  tier == 2,
            't3_active':  tier == 3,
            'auto_mode':  self.auto_var.get(),
        }
