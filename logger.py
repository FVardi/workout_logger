
# %%
import pandas as pd
from datetime import datetime
import pickle
import numpy as np
import workout_tracker as wt

# %%
# ==================================
# Always add date and description
# ==================================
date = datetime(2024, 4, 15)

date = date.date()

tracker = wt.WorkoutTracker(date)

# Always add description
tracker.add_description(
    description='''Strict pull ups''', 
    notes='4x7'
)

# %%
# ==================================
# Heavy day
# ==================================
tracker.add_heavy_day(
    movement='Push press',
    reference_rep_scheme='4x6',
    weight='60-60-60',
    true_rep_scheme='3x6'
)

# %%
# ==================================
# Regular workout
# ==================================
tracker.add_workout(
    movements=['Strict pull ups'],
    score='',
    time_domain=''
)

# %%
# ==================================
# Heavy day
# ==================================
tracker.add_heavy_day_at_HHR(
    movement=''
)

# %%
# ==================================
# Pure engine day
# ==================================
tracker.add_standard_engine_day(
    movement='Run',
    reference_rep_scheme='Long run',
    time_domain='Long',
    score='',
    true_rep_scheme='6km'
)

# %%
# ==================================
# Save workout
# ==================================
tracker.log_workout()

# %%
# ==================================
# Modify movement dicts
# ==================================
tracker.modify_movement_dicts(
    movement='Strict pull ups',
    add_or_delete='add',
    gmp_list=['Upper body pull'],
    modality_list=['Gymnastic']
)