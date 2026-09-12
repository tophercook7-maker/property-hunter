"""Deterministic financial analysis (spec 22/23/24/25/40).

Plain arithmetic, no AI. Every output carries the assumptions that produced it
so nothing ever reads as a guaranteed return.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import FINANCE_DEFAULTS


def merged(overrides: dict | None = None) -> dict:
    a = dict(FINANCE_DEFAULTS)
    if overrides:
        a.update({k: v for k, v in overrides.items() if v is not None})
    return a


def monthly_payment(principal: float, annual_rate: float, years: int) -> float:
    if principal <= 0:
        return 0.0
    r = annual_rate / 12.0
    n = years * 12
    if r <= 0:
        return principal / n
    return principal * (r * (1 + r) ** n) / ((1 + r) ** n - 1)


# ------------------------------------------------------------------- basis

def total_basis(purchase_price: float, rehab: float, a: dict) -> dict:
    closing = purchase_price * a["closing_cost_pct"]
    title = a["title_legal_flat"]
    carrying = purchase_price * a["carrying_cost_monthly_pct"] * a["carrying_months"]
    financing = purchase_price * (1 - a["down_payment_pct"]) * a["interest_rate"] * (
        a["carrying_months"] / 12.0)
    contingency = (purchase_price + rehab) * a["contingency_pct"]
    total = purchase_price + rehab + closing + title + carrying + financing + contingency
    return {
        "purchase_price": round(purchase_price, 2),
        "rehab": round(rehab, 2),
        "closing_costs": round(closing, 2),
        "title_and_legal": round(title, 2),
        "carrying_costs": round(carrying, 2),
        "financing_costs": round(financing, 2),
        "contingency": round(contingency, 2),
        "total_basis": round(total, 2),
    }


# ------------------------------------------------------------------ rental

def rental_analysis(*, purchase_price: float, monthly_rent: float,
                    rehab: float = 0.0, assessed_value: float = 0.0,
                    overrides: dict | None = None) -> dict:
    a = merged(overrides)
    basis = total_basis(purchase_price, rehab, a)

    gross_annual = monthly_rent * 12
    vacancy = gross_annual * a["vacancy_pct"]
    taxes = (assessed_value or purchase_price) * a["tax_rate_of_assessed"] * 0.2
    insurance = a["insurance_annual_per_100k"] * max(purchase_price + rehab, 1) / 100000.0
    maintenance = gross_annual * a["maintenance_pct"]
    reserves = gross_annual * a["reserves_pct"]
    management = gross_annual * a["management_pct"]
    opex = vacancy + taxes + insurance + maintenance + reserves + management
    noi = gross_annual - opex

    loan = purchase_price * (1 - a["down_payment_pct"])
    pmt = monthly_payment(loan, a["interest_rate"], int(a["loan_term_years"]))
    debt_service = pmt * 12
    cash_flow_annual = noi - debt_service
    cash_in = (purchase_price * a["down_payment_pct"] + rehab
               + basis["closing_costs"] + basis["title_and_legal"])

    cap_rate = noi / basis["total_basis"] if basis["total_basis"] else 0.0
    coc = cash_flow_annual / cash_in if cash_in else 0.0
    grm = basis["total_basis"] / gross_annual if gross_annual else 0.0
    dscr = noi / debt_service if debt_service else None
    break_even_rent = (opex + debt_service) / 12 if True else 0.0
    # opex scales with rent, so solve it properly:
    var_pct = a["vacancy_pct"] + a["maintenance_pct"] + a["reserves_pct"] + a["management_pct"]
    fixed = taxes + insurance
    break_even_rent = ((fixed + debt_service) / (1 - var_pct)) / 12 if var_pct < 1 else 0.0

    projections = []
    rent, value = monthly_rent, max(purchase_price + rehab, 1.0)
    balance = loan
    for year in range(1, 11):
        rent *= 1.025
        value *= 1.03
        for _ in range(12):
            interest = balance * a["interest_rate"] / 12
            balance = max(0.0, balance - (pmt - interest))
        yr_gross = rent * 12
        yr_opex = yr_gross * var_pct + fixed
        yr_noi = yr_gross - yr_opex
        projections.append({
            "year": year, "rent_monthly": round(rent, 2), "noi": round(yr_noi, 2),
            "cash_flow": round(yr_noi - debt_service, 2),
            "value": round(value, 2), "loan_balance": round(balance, 2),
            "equity": round(value - balance, 2),
        })
        if year not in (1, 2, 3, 5, 10):
            projections.pop()

    sensitivity = []
    for label, rent_mult, rehab_mult in (("rent 15% low", 0.85, 1.0),
                                         ("rehab 30% over", 1.0, 1.3),
                                         ("both go wrong", 0.85, 1.3),
                                         ("rent 10% high", 1.10, 1.0)):
        b = total_basis(purchase_price, rehab * rehab_mult, a)
        g = monthly_rent * rent_mult * 12
        o = g * var_pct + fixed
        n = g - o
        sensitivity.append({
            "scenario": label, "noi": round(n, 2),
            "cash_flow": round(n - debt_service, 2),
            "cap_rate": round(n / b["total_basis"], 4) if b["total_basis"] else 0,
        })

    return {
        "assumptions": a,
        "basis": basis,
        "income": {
            "monthly_rent": round(monthly_rent, 2),
            "gross_annual": round(gross_annual, 2),
            "vacancy": round(vacancy, 2),
            "taxes": round(taxes, 2),
            "insurance": round(insurance, 2),
            "maintenance": round(maintenance, 2),
            "reserves": round(reserves, 2),
            "management": round(management, 2),
            "operating_expenses": round(opex, 2),
            "noi": round(noi, 2),
        },
        "debt": {
            "loan_amount": round(loan, 2),
            "monthly_payment": round(pmt, 2),
            "annual_debt_service": round(debt_service, 2),
        },
        "returns": {
            "cash_invested": round(cash_in, 2),
            "monthly_cash_flow": round(cash_flow_annual / 12, 2),
            "annual_cash_flow": round(cash_flow_annual, 2),
            "cap_rate": round(cap_rate, 4),
            "cash_on_cash": round(coc, 4),
            "gross_rent_multiplier": round(grm, 2),
            "dscr": round(dscr, 2) if dscr else None,
            "break_even_rent": round(break_even_rent, 2),
        },
        "projections": projections,
        "sensitivity": sensitivity,
        "caveat": ("Every number above is an estimate built on the assumptions listed. "
                   "The rent figure in particular is the one most likely to be wrong - "
                   "verify it against real rented houses on this street before you act."),
    }


# --------------------------------------------------------------- deal (MAO)

def max_purchase_price(*, after_repair_value: float, rehab: float,
                       desired_return: float | None = None,
                       overrides: dict | None = None) -> dict:
    """Work backwards from value to the most you should pay (spec 25)."""
    a = merged(overrides)
    target = desired_return if desired_return is not None else a["target_return_pct"]

    def solve(profit_pct: float) -> float:
        # ARV = price + rehab + costs(price) + profit
        # costs = price*closing + title + carry + finance + contingency(price+rehab)
        k = (a["closing_cost_pct"]
             + a["carrying_cost_monthly_pct"] * a["carrying_months"]
             + (1 - a["down_payment_pct"]) * a["interest_rate"] * a["carrying_months"] / 12.0
             + a["contingency_pct"])
        fixed = a["title_legal_flat"] + rehab * (1 + a["contingency_pct"])
        numerator = after_repair_value * (1 - profit_pct) - fixed
        return max(0.0, numerator / (1 + k))

    maximum = solve(target * 0.55)
    reasonable = solve(target * 0.8)
    aggressive = solve(target)

    if maximum <= 0:
        plain = [
            "On these numbers there is no price that works - not even free.",
            f"The repair estimate (${rehab:,.0f}) plus costs and a margin already eats "
            f"the whole finished value (${after_repair_value:,.0f}).",
            "That means one of two things: the repair number is too high, or the "
            "finished value is too low. Both are estimates right now - get a real "
            "contractor number and a real comparable sale before believing either.",
        ]
    else:
        plain = [
            f"Based on the numbers entered, I would try to stay around "
            f"${aggressive:,.0f} or less.",
            f"If the purchase price reaches ${reasonable:,.0f}, the deal becomes marginal.",
            f"If it reaches ${maximum:,.0f}, I would pass.",
            "All three move the moment the repair estimate or the finished value moves - "
            "and right now both of those are estimates.",
        ]
    return {
        "after_repair_value": round(after_repair_value, 2),
        "rehab": round(rehab, 2),
        "target_return_pct": round(target, 4),
        "aggressive": round(aggressive, 2),
        "reasonable": round(reasonable, 2),
        "maximum": round(maximum, 2),
        "works_at_any_price": maximum > 0,
        "assumptions": a,
        "plain_english": plain,
    }


# ------------------------------------------------------------------ storage

def storage_analysis(*, acreage: float, purchase_price: float = 0.0,
                     usable_pct: float = 0.65, overrides: dict | None = None) -> dict:
    a = merged(overrides)
    land_sqft = acreage * 43560.0
    usable_sqft = land_sqft * usable_pct
    buildable = usable_sqft * a["storage_coverage_ratio"]
    units_10x10 = int(buildable // 100)
    build_cost = buildable * a["storage_build_cost_per_sqft"]
    gross_potential = buildable * a["storage_rent_per_sqft_monthly"] * 12
    effective = gross_potential * 0.85          # 15% vacancy/collection loss
    opex = effective * a["storage_opex_pct"]
    noi = effective - opex
    total_cost = purchase_price + build_cost
    yield_on_cost = noi / total_cost if total_cost else 0.0
    return {
        "acreage": round(acreage, 3),
        "land_sqft": round(land_sqft),
        "usable_sqft": round(usable_sqft),
        "assumed_usable_pct": usable_pct,
        "buildable_sqft": round(buildable),
        "estimated_units_10x10": units_10x10,
        "estimated_build_cost": round(build_cost, 2),
        "gross_potential_revenue": round(gross_potential, 2),
        "effective_revenue": round(effective, 2),
        "operating_expenses": round(opex, 2),
        "noi": round(noi, 2),
        "total_project_cost": round(total_cost, 2),
        "yield_on_cost": round(yield_on_cost, 4),
        "assumptions": a,
        "caveat": ("This is a sketch, not a site plan. Setbacks, drainage, fire access, "
                   "paving and - above all - whether storage is even a permitted use here "
                   "will change every one of these numbers. Nothing here says the City "
                   "would approve it."),
    }


# ---------------------------------------------------------------- snow cone

def snowcone_analysis(*, road_rank: int = 3, acreage: float = 0.25,
                      competitors: int = 0, overrides: dict | None = None) -> dict:
    a = merged(overrides)
    # Very rough traffic proxy from the road class we observed in OSM.
    traffic_by_rank = {9: 30000, 8: 18000, 7: 12000, 6: 7000, 5: 3500,
                       3: 1200, 2: 600, 1: 200, 0: 0}
    daily_traffic = traffic_by_rank.get(road_rank, 1200)
    customers = (daily_traffic / 1000.0) * a["snowcone_daily_customers_per_1k_traffic"]
    customers *= max(0.55, 1.0 - 0.12 * competitors)
    daily_revenue = customers * a["snowcone_avg_ticket"]
    season_revenue = daily_revenue * a["snowcone_season_days"]
    cogs = season_revenue * 0.28
    labor = season_revenue * 0.25
    other = 4200.0
    profit = season_revenue - cogs - labor - other
    return {
        "assumed_daily_traffic": daily_traffic,
        "estimated_daily_customers": round(customers, 1),
        "estimated_daily_revenue": round(daily_revenue, 2),
        "season_days": a["snowcone_season_days"],
        "estimated_season_revenue": round(season_revenue, 2),
        "cost_of_goods": round(cogs, 2),
        "labor": round(labor, 2),
        "other_costs": round(other, 2),
        "estimated_season_profit": round(profit, 2),
        "competitors_nearby": competitors,
        "acreage": acreage,
        "assumptions": a,
        "caveat": ("The traffic figure is inferred from the road classification in "
                   "OpenStreetMap, not from a traffic count. Treat the revenue as a "
                   "rough shape, not a forecast. A seasonal food business also needs "
                   "health-department approval and a zoning use that allows it - "
                   "neither of which this tool can confirm."),
    }


# -------------------------------------------------------------------- rehab

def rehab_estimate(sqft: float, level: str = "medium") -> dict:
    key = {"light": "rehab_per_sqft_light", "medium": "rehab_per_sqft_medium",
           "heavy": "rehab_per_sqft_heavy"}.get(level, "rehab_per_sqft_medium")
    rate = FINANCE_DEFAULTS[key]
    return {
        "sqft": round(sqft),
        "level": level,
        "rate_per_sqft": rate,
        "estimate": round(sqft * rate, 2),
        "range_low": round(sqft * rate * 0.75, 2),
        "range_high": round(sqft * rate * 1.45, 2),
        "caveat": ("A per-square-foot rule of thumb, nothing more. Nobody has been "
                   "inside this building. Real numbers come from a walkthrough with "
                   "a contractor."),
    }
