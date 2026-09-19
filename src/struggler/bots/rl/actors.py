"""Persistent actor pool for PPO self-play.

A spawn `Pool` created once (not per training iteration) with a worker-side
policy cache keyed by checkpoint path, so a worker loads any given snapshot at
most once. This removes the per-iteration pool-spawn and per-game model-load
overhead that a naive loop pays.
"""

from __future__ import annotations

import multiprocessing
import random

import torch

from struggler.bots.rl.net import load_policy
from struggler.bots.rl.selfplay import collect_episode

_NETS: dict = {}


def init_worker() -> None:
    torch.set_num_threads(1)  # avoid oversubscription across workers
    _NETS.clear()


def get_net(path: str):
    net = _NETS.get(path)
    if net is None:
        if len(_NETS) >= 16:  # bounded: current + league snapshots, no leak
            _NETS.clear()
        net = load_policy(path, device="cpu")
        _NETS[path] = net
    return net


def collect_task(args: tuple) -> list:
    """(net_path, opponent_path | None, seed, anchor_fraction, events)."""
    net_path, opponent_path, seed, anchor_fraction, events = args
    net = get_net(net_path)
    opponent = get_net(opponent_path) if opponent_path else None
    return collect_episode(
        net, seed,
        events=events,
        anchor_fraction=anchor_fraction,
        opponent_net=opponent,
        opponent_prob=1.0 if opponent is not None else 0.0,
        rng=random.Random(seed),
        device="cpu",
    )


def make_pool(workers: int):
    ctx = multiprocessing.get_context("spawn")
    return ctx.Pool(workers, initializer=init_worker)
