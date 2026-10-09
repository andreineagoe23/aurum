"""Phase 4 exit gate: the loss at the stop, after rounding the volume, never exceeds the budget."""

import math

from hypothesis import given
from hypothesis import strategies as st

from aurum.core.models import SymbolSpec
from aurum.risk.policy import size_volume

STEPS = st.sampled_from([0.01, 0.1, 1.0])


@st.composite
def specs(draw: st.DrawFn) -> SymbolSpec:
    step = draw(STEPS)
    tick_size = draw(st.sampled_from([0.01, 0.001, 0.1]))
    return SymbolSpec(
        name="XAUUSD",
        digits=3,
        point=tick_size,
        tick_size=tick_size,
        tick_value=draw(st.floats(0.001, 10.0)),
        contract_size=100.0,
        volume_min=step,
        volume_max=draw(st.sampled_from([10.0, 100.0, 500.0])),
        volume_step=step,
    )


@given(
    spec=specs(),
    budget=st.floats(0.01, 1e6),
    distance=st.floats(0.01, 500.0),
    commission=st.floats(0.0, 20.0),
    slippage=st.floats(0.0, 100.0),
)
def test_risk_after_rounding_never_exceeds_the_budget(
    spec: SymbolSpec, budget: float, distance: float, commission: float, slippage: float
) -> None:
    lots, per_lot = size_volume(
        budget=budget,
        stop_distance=distance,
        spec=spec,
        commission_per_lot_side=commission,
        stop_slippage_points=slippage,
    )
    assert lots * per_lot <= budget
    assert 0 <= lots <= spec.volume_max
    steps = lots / spec.volume_step
    assert math.isclose(steps, round(steps), abs_tol=1e-9)
    # Maximal: one more step would break the budget (unless capped at volume_max).
    if lots + spec.volume_step <= spec.volume_max:
        assert (lots + spec.volume_step) * per_lot > budget * (1 - 1e-9)
