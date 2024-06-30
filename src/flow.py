import torch
import proxy
from tqdm import tqdm
import openmm.unit as unit
from utils.utils import *


class FlowNetAgent:
    def __init__(self, args, md):
        self.a = md.a
        self.num_particles = md.num_particles
        self.std = torch.tensor(
            md.std.value_in_unit(unit.nanometer / unit.femtosecond),
            dtype=torch.float,
            device=args.device,
        )
        self.m = torch.tensor(
            md.m.value_in_unit(md.m.unit),
            dtype=torch.float,
            device=args.device,
        ).unsqueeze(-1)
        self.policy = getattr(proxy, args.molecule.title())(args, md)
        # self.normal = Normal(0, self.std)

        if args.type == "train":
            self.replay = ReplayBuffer(args, md)

    def sample(self, args, mds):
        positions = torch.zeros(
            (args.num_samples, args.num_steps + 1, self.num_particles, 3),
            device=args.device,
        )
        velocities = torch.zeros(
            (args.num_samples, args.num_steps + 1, self.num_particles, 3),
            device=args.device,
        )
        actions = torch.zeros(
            (args.num_samples, args.num_steps, self.num_particles, 3),
            device=args.device,
        )
        potentials = torch.zeros(
            args.num_samples, args.num_steps + 1, device=args.device
        )

        position, velocity, _, potential = mds.report()

        positions[:, 0] = position
        velocities[:, 0] = velocity
        potentials[:, 0] = potential

        if args.type == "train":
            mds.set_temperature(args.train_temperature)
        for s in tqdm(range(args.num_steps), desc="Sampling"):
            bias = (
                args.bias_scale
                * self.policy(position.detach(), mds.target_position).squeeze().detach()
            )
            mds.step(bias)

            next_position, velocity, force, potential = mds.report()

            # extract noise
            noise = (next_position - position) / args.timestep - self.a * (
                velocity + force / self.m
            )

            positions[:, s + 1] = next_position
            potentials[:, s + 1] = potential - (bias * next_position).sum(
                (1, 2)
            )  # Subtract bias potential to get true potential

            position = next_position
            bias = 1e-6 * bias  # kJ/(mol*nm) -> (da*nm)/fs**2
            action = self.a * bias / self.m + noise

            actions[:, s] = action
        mds.reset()

        log_md_reward = -0.5 * torch.square(actions / self.std).mean((1, 2, 3))

        if args.reward == "kabsch":
            log_target_reward = torch.zeros(
                args.num_samples, args.num_steps, device=args.device
            )
            for i in range(args.num_samples):
                aligned_target_position, rmsd = kabsch(
                    mds.target_position, positions[i][1:]
                )
                target_velocity = (
                    aligned_target_position - positions[i][:-1]
                ) / args.timestep
                log_target_reward[i] = -0.5 * torch.square(
                    (target_velocity - velocities[i][1:]) / args.sigma
                ).mean((1, 2))
        elif args.reward == "dist":
            target_pd = pairwise_dist(mds.target_position)

            log_target_reward = torch.zeros(
                args.num_samples, args.num_steps + 1, device=args.device
            )
            for i in range(args.num_samples):
                pd = pairwise_dist(positions[i])
                log_target_reward[i] = -torch.square(
                    (pd - target_pd) / args.sigma
                ).mean((1, 2))
        elif args.reward == "s_dist":
            log_target_reward = torch.zeros(
                args.num_samples, args.num_steps + 1, device=args.device
            )
            for i in range(args.num_samples):
                log_target_reward[i] = (
                    compute_s_dist(positions[i], mds.target_position) / args.sigma
                )

        log_target_reward, last_idx = log_target_reward.max(1)

        log_reward = log_md_reward + log_target_reward

        if args.type == "train":
            self.replay.add((positions, actions, log_reward))

        log = {
            "actions": actions,
            "last_idx": last_idx + 1,
            "positions": positions,
            "potentials": potentials,
            "target_position": mds.target_position,
            "last_position": positions[torch.arange(args.num_samples), last_idx],
        }
        return log

    def train(self, args, mds):
        optimizer = torch.optim.Adam(
            [
                {"params": [self.policy.log_z], "lr": args.log_z_lr},
                {"params": self.policy.mlp.parameters(), "lr": args.policy_lr},
            ]
        )

        positions, actions, log_reward = self.replay.sample()

        biases = args.bias_scale * self.policy(positions[:, :-1], mds.target_position)
        biases = 1e-6 * biases  # kJ/(mol*nm) -> (da*nm)/fs**2
        biases = self.a * biases / self.m

        log_z = self.policy.log_z
        log_forward = -0.5 * torch.square((biases - actions) / self.std).mean((1, 2, 3))
        loss = (log_z + log_forward - log_reward).square().mean() * args.scale

        loss.backward()

        for group in optimizer.param_groups:
            torch.nn.utils.clip_grad_norm_(group["params"], args.max_grad_norm)

        optimizer.step()
        optimizer.zero_grad()
        return loss.item()


class ReplayBuffer:
    def __init__(self, args, md):
        self.positions = torch.zeros(
            (args.buffer_size, args.num_steps + 1, md.num_particles, 3),
            device=args.device,
        )
        self.actions = torch.zeros(
            (args.buffer_size, args.num_steps, md.num_particles, 3), device=args.device
        )
        self.log_reward = torch.zeros(args.buffer_size, device=args.device)

        self.idx = 0
        self.buffer_size = args.buffer_size
        self.num_samples = args.num_samples
        self.batch_size = args.batch_size

    def add(self, data):
        indices = torch.arange(self.idx, self.idx + self.num_samples) % self.buffer_size
        self.idx += self.num_samples

        self.positions[indices], self.actions[indices], self.log_reward[indices] = data

    def sample(self):
        indices = torch.randperm(min(self.idx, self.buffer_size))[: self.batch_size]
        return self.positions[indices], self.actions[indices], self.log_reward[indices]
