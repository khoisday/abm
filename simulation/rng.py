"""Separate reproducible random streams per process, agent and day.

Each stream is seeded from (seed, stream_code, *keys) so that a policy that
draws extra numbers in one stream cannot shift draws in another stream
(population, weather, initial room temperatures, ...).
"""
import numpy as np

STREAMS = {
    "population": 1,
    "weather": 2,
    "rooms_init": 3,
    "housekeeping": 4,
    "towels": 5,
    "group_day": 10,
    "decision": 11,
    "policy": 12,
    "prediction": 13,
    "service": 14,
    "population_extra": 20,
    "population_mainland": 21,
    "population_mainland_extra": 22,
    "population_day": 23,
    "population_day_extra": 24,
    "rooms_init_mainland": 25,
}


def stream(seed, name, *keys):
    return np.random.default_rng([int(seed), STREAMS[name], *[int(k) for k in keys]])
