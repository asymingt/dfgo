from typing import Callable

import jax
import jax_dataclasses
import torch
from jax import numpy as jnp

from .. import validation_tracker
from . import data, math_utils, training_ekf


@jax_dataclasses.pytree_dataclass
class _ValidationMetrics:
    # TODO: this is duplicated from validation_fg
    m_per_m: float
    rad_per_m: float


@jax.jit
def _compute_metrics(
    train_state: training_ekf.TrainState,
    trajectory: data.KittiStructNormalized,
) -> _ValidationMetrics:
    (_timesteps,) = trajectory.get_batch_axes()

    gt_trajectory_raw = trajectory.unnormalize()
    posterior_states = train_state.run_ekf(
        gt_trajectory_raw,
        prng_key=jax.random.PRNGKey(0),
    )
    solved_final_state = jax.tree_map(lambda x: x[-1], posterior_states)

    true_distance_traveled = math_utils.compute_distance_traveled(
        gt_trajectory_raw.x, gt_trajectory_raw.y
    )
    error_m = jnp.sqrt(
        (solved_final_state.x - gt_trajectory_raw.x[-1]) ** 2
        + (solved_final_state.y - gt_trajectory_raw.y[-1]) ** 2
    )
    error_rad = jnp.abs(
        math_utils.wrap_angle(solved_final_state.theta - gt_trajectory_raw.theta[-1])
    )
    assert error_m.shape == error_rad.shape == ()

    return _ValidationMetrics(
        m_per_m=error_m / true_distance_traveled,
        rad_per_m=error_rad / true_distance_traveled,
    )


def make_compute_metrics(
    eval_dataset: torch.utils.data.Dataset[data.KittiStructNormalized],
) -> Callable[[training_ekf.TrainState], validation_tracker.ValidationMetrics]:
    def compute_metrics(
        train_state: training_ekf.TrainState,
    ) -> validation_tracker.ValidationMetrics:
        # Eval mode
        train_state = jax_dataclasses.replace(train_state, train=False)

        metrics_summed = _ValidationMetrics(0.0, 0.0)

        # TODO: there's no reason not to use vmap here
        for i in range(len(eval_dataset)):  # type: ignore
            traj: data.KittiStructNormalized
            traj = eval_dataset[i]

            # Leading axes: (batch, # timesteps)
            (timesteps,) = traj.get_batch_axes()

            batch_metrics = _compute_metrics(
                train_state,
                traj,
            )
            metrics_summed = jax.tree_map(
                lambda a, b: a + b,
                metrics_summed,
                batch_metrics,
            )

        metrics_avg: _ValidationMetrics = jax.tree_map(lambda x: x / len(eval_dataset), metrics_summed)  # type: ignore
        return metrics_avg.m_per_m, vars(metrics_avg)

    return compute_metrics
