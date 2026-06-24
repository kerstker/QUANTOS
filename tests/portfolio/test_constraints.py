import math
import pytest
from services.portfolio.constraints import (
    PortfolioConstraints, InfeasibleConstraints,
    project_box, project_hyperplane, project_l1_ball, dykstra_project,
)


def test_project_box_clamps_including_negative_bounds():
    assert project_box([1.5, -2.0, 0.1], [-1.0, -1.0, -1.0], [1.0, 1.0, 1.0]) == [1.0, -1.0, 0.1]


def test_project_hyperplane_lands_on_plane():
    a, b = [1.0, 1.0, 1.0], 1.0
    p = project_hyperplane([0.0, 0.0, 0.0], a, b)
    assert sum(p) == pytest.approx(1.0)


def test_project_l1_ball_noop_inside_and_shrinks_outside():
    assert project_l1_ball([0.2, -0.3], 1.0) == [0.2, -0.3]      # inside -> unchanged
    out = project_l1_ball([1.0, -1.0, 1.0], 1.5)
    assert sum(abs(x) for x in out) == pytest.approx(1.5, abs=1e-9)


def test_dykstra_lands_in_box_and_budget_intersection():
    lb, ub = [0.0, 0.0, 0.0], [0.5, 0.5, 0.5]
    ones = [1.0, 1.0, 1.0]
    p = dykstra_project([0.9, 0.9, 0.9],
                        [lambda x: project_box(x, lb, ub),
                         lambda x: project_hyperplane(x, ones, 1.0)])
    assert sum(p) == pytest.approx(1.0, abs=1e-6)
    assert all(lb[i] - 1e-6 <= p[i] <= ub[i] + 1e-6 for i in range(3))


def test_resolve_bounds_infeasible_budget_raises():
    c = PortfolioConstraints(lower=0.0, upper=0.1, budget=1.0)  # 3*0.1 < 1.0
    with pytest.raises(InfeasibleConstraints):
        c.resolve_bounds(["a", "b", "c"])
