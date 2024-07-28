import os
import sys

import gymnasium as gym
import numpy as np
import stable_baselines3
import torch
from environment import CustomCustomEnv
from magic import MagicCallback, BruteMagicCallback
from simulator import CustomSimulator
from stable_baselines3 import SAC
from stable_baselines3.common.callbacks import EvalCallback
from stable_baselines3.common.env_util import make_vec_env
from stable_baselines3.sac.policies import MlpPolicy
from wrappers import *

from double_pendulum.model.model_parameters import model_parameters
from double_pendulum.model.symbolic_plant import SymbolicDoublePendulum
from double_pendulum.simulation.gym_env import double_pendulum_dynamics_func
from double_pendulum.simulation.simulation import Simulator

np.random.seed(0)
torch.manual_seed(0)
torch.random.manual_seed(0)
torch.backends.cudnn.deterministic = True
stable_baselines3.common.utils.set_random_seed(0)

class MyEnv(CustomCustomEnv):
    def reward_func(self, terminated, action):
        _, theta2, omega1, omega2 = self.dynamics_func.unscale_state(self.observation)
        costheta2 = np.cos(theta2)

        a = action[0]
        delta_action = np.abs(a - self.previous_action)
        lambda_delta = 0.05
        lambda_action = 0.02
        lambda_velocities = 0.005
        if not terminated:
            if self.stabilisation_mode:
                reward = (
                    self.V()
                    + 2 * (1 + costheta2) ** 2                     * 2
                    - self.T()
                    - 5 * lambda_action * np.square(a)             * 2
                    - 3 * lambda_delta * delta_action              * 2
                )
            else:
                reward = (
                    (1 - np.abs(a)) * self.V()
                    - lambda_action * np.square(a)                         * 2
                    - 2 * lambda_velocities * (omega1**2 + omega2**2)      * 2
                    - 3 * lambda_delta * delta_action                      * 2
                )
        else:
            reward = -1.0
        return reward


assert (
    len(sys.argv) >= 3
), "Please provide: [max torque] [robustness] [window_size (0 = no window)] [include_time]"
max_torque = float(sys.argv[1])
robustness = float(sys.argv[2])
WINDOW_SIZE = int(sys.argv[3])
INCLUDE_TIME = bool(int(sys.argv[4]))
FOLDER_ID = f"{os.path.basename(__file__)}-{max_torque}-{robustness}-{WINDOW_SIZE}-{int(INCLUDE_TIME)}"
TERMINATION = False

# setting log path for the training
log_dir = f"./log_data/SAC_training/{FOLDER_ID}"
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

# define robot variation
robot = "acrobot"

design = "design_C.1"
model = "model_1.0"
model_par_path = (
    "../../../data/system_identification/identified_parameters/"
    + design
    + "/"
    + model
    + "/model_parameters.yml"
)

# model and reward parameter
max_velocity = 50
torque_limit = [max_torque, 0] if robot == "pendubot" else [0, max_torque]

mpar = model_parameters(filepath=model_par_path)
mpar.set_torque_limit(torque_limit)
dt = 0.01
integrator = "runge_kutta"

plant = SymbolicDoublePendulum(model_pars=mpar)
simulator = CustomSimulator(plant=plant, robustness=robustness, max_torque=max_torque)
eval_simulator = Simulator(plant=plant)

# learning environment parameters
state_representation = 2

obs_space = gym.spaces.Box(np.array([-1.0] * 4), np.array([1.0] * 4))
act_space = gym.spaces.Box(np.array([-1]), np.array([1]))
max_steps = 10 / dt

###############################################################################

n_envs = 50
training_steps = 10_000_000
verbose = 1
eval_freq = 1000
n_eval_episodes = 1
# a patto che i reward istantanei siano piccoli
# 0.01 -> 1500000 -> 7
# 0.003 -> 1500000 -> 46
# 0.001 -> 1500000 -> 38
# 0.0003 -> 1500000 -> 19
learning_rate = 0.00003

###############################################################################

# initialize double pendulum dynamics
dynamics_func = double_pendulum_dynamics_func(
    simulator=simulator,
    dt=dt,
    integrator=integrator,
    robot=robot,
    state_representation=state_representation,
    max_velocity=max_velocity,
    torque_limit=torque_limit,
)

eval_dynamics_func = double_pendulum_dynamics_func(
    simulator=eval_simulator,
    dt=dt,
    integrator=integrator,
    robot=robot,
    state_representation=state_representation,
    max_velocity=max_velocity,
    torque_limit=torque_limit,
)


def zero_reset_func():
    observation = [-1.0, -1.0, 0.0, 0.0]
    return observation


###############################################################################


def wrap(env):
    if INCLUDE_TIME:
        env = TimeAwareWrapper(env, dt=dt)
    if WINDOW_SIZE > 0:
        env = StateActionHistoryWrapper(env, history_length=WINDOW_SIZE)
    return env


envs = make_vec_env(
    env_id=MyEnv,
    wrapper_class=wrap,
    n_envs=n_envs,
    env_kwargs={
        "dynamics_func": dynamics_func,
        "reset_func": zero_reset_func,
        "terminates": TERMINATION,
        "obs_space": obs_space,
        "act_space": act_space,
        "max_episode_steps": max_steps,
    },
)

eval_env = wrap(
    MyEnv(
        dynamics_func=eval_dynamics_func,
        reset_func=zero_reset_func,
        obs_space=obs_space,
        act_space=act_space,
        max_episode_steps=max_steps,
        terminates=TERMINATION,
    )
)

eval_callback = EvalCallback(
    eval_env,
    callback_after_eval=BruteMagicCallback(
        f"./models/",
        folder_id=FOLDER_ID,
        dynamics_func=eval_dynamics_func,
        robot=robot,
        window_size=WINDOW_SIZE,
        max_torque=max_torque,
        include_time=INCLUDE_TIME,
    ),
    best_model_save_path=os.path.join(log_dir, "best_model"),
    log_path=log_dir,
    eval_freq=eval_freq,
    verbose=verbose,
    n_eval_episodes=n_eval_episodes,
)

###############################################################################

agent = SAC(
    MlpPolicy,
    envs,
    verbose=verbose,
    learning_rate=learning_rate,
)
agent.set_parameters(input("input model zip file path"))

import asyncio
async def main():
    agent.learn(total_timesteps=training_steps, callback=eval_callback)

asyncio.run(main())