
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
date = datetime(2024, 5, 25)
date = date.date()

tracker = workout_tracker.WorkoutTracker()
tracker.add_heavy_day(
    date=date,
    movement='Split jerk',
    reference_rep_scheme='1 rep x E1MOM10',
    total_reps=9,
    weight='50-50-50-60-60-60-70-70-70',
    true_rep_scheme='1 rep x E1,5MOM9'
)

# %% ===============================
# Regular workout
# ==================================
date = datetime(2024, 5, 2)
date = date.date()

tracker = workout_tracker.WorkoutTracker()
tracker.add_workout(
    date=date,
    movements=['Power C&J',
               'Double unders'],
    description='4x(90 sec. on/90 sec. off): 75 DUs, max C&J rest of time',
    time_domain='Short',
    hhr_movement=['Power C&J']
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
    movement='V-ups',
    add_or_delete='add',
    gmp_list=['Midline'],
    modality_list=['Gymnastic']
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

tracker = workout_tracker.WorkoutTracker()

pd.read_pickle('movement_log.pkl').columns
# tracker.show_file('movement_gmp_dict')
# tracker.show_file('movement_modality_dict')
# tracker.show_file('main_log')


# %% ===============================
# Delete workout
# ==================================

tracker.delete_workout(workout_id=6.0)
