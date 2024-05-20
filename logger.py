
# %%
import pandas as pd
from datetime import datetime
import pickle
import numpy as np
import importlib
import workout_tracker
importlib.reload(workout_tracker)

# %% ===============================
# Heavy day
# ==================================
date = datetime.today()
date = date.date()

tracker = workout_tracker.WorkoutTracker()
tracker.add_heavy_day(
    date=date,
    movement='Strict pull ups',
    reference_rep_scheme='5x5',
    weight='0-0-0-0-0',
    true_rep_scheme='5x5'
)

# %% ===============================
# Regular workout
# ==================================
date = datetime(2024, 5, 19)
date = date.date()

tracker = workout_tracker.WorkoutTracker()
tracker.add_workout(
    date=date,
    movements=['Air bike',
               'T2B',
               'Farmer walking lunges',
               'Farmer walk',
               'Double unders'],
    description='2 hour fobbit',
    time_domain='Long',
    hhr_movement=['Farmer walking lunges']
)

# %% ===============================
# Pure engine day
# ==================================
date = datetime(2024, 5, 19)
date = date.date()

tracker = workout_tracker.WorkoutTracker()
tracker.add_standard_engine_day(
    date=date,
    movement='Run',
    reference_rep_scheme='Long run',
    time_domain='Long',
    true_rep_scheme='6km'
)

# %% ===============================
# Modify movement dicts
# ==================================
tracker = workout_tracker.WorkoutTracker()
tracker.modify_movement_dicts(
    movement='Bench press',
    add_or_delete='add',
    gmp_list=['Pressing'],
    modality_list=['Weightlifting']
)

# Below parallel
# Pressing
# Pull from ground/hinge
# Midline
# Upper body pull
# Engine

# Weightlifting
# Gymnastic
# Monostructural

# %% ===============================
# Show data frames
# ==================================

# tracker.show_file('movement_log')
# tracker.show_file('movement_gmp_dict')
# tracker.show_file('movement_modality_dict')
tracker.show_file('main_log')
