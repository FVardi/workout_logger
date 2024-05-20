
import pandas as pd
from datetime import datetime
import pickle
import numpy as np


main_log_reset = pd.DataFrame(columns=[
    'Date',
    'Workout id',
    'Description',
    'Workout type',
    'Notes',
    'Score',
    'Time domain',
    'Weightlifting',
    'Monostructural',
    'Gymnastic',
    'Pull from ground',
    'Pressing',
    'Below parallel',
    'Upper body pull',
    'Midline',
    'Engine'],
    data = np.zeros((1, 16))
)


heavy_day_log_reset = pd.DataFrame({
    'Date': [],
    'Workout id': [],
    'Movement': [],
    'Type': [],
    'Rep scheme': [],
    'Weight': [],
    'True rep scheme': []
})

engine_log_reset = pd.DataFrame({
    'Date': [],
    'Workout id': [],
    'Movement': [],
    'Type': [],
    'Rep scheme': [],
    'True rep scheme': [],
    'Score': []
})

movement_log_reset = pd.DataFrame({
    'Date': []
})

main_log_reset.to_pickle('main_log.pkl')
heavy_day_log_reset.to_pickle('heavy_day_log.pkl')
engine_log_reset.to_pickle('engine_log.pkl')
movement_log_reset.to_pickle('movement_log.pkl')

reset_dict = {}
pickle.dump(reset_dict, open('movement_modality_dict.pkl', 'wb'))
pickle.dump(reset_dict, open('movement_gmp_dict.pkl', 'wb'))

rep_schemes = {
    'Heavy day': ['7x1', '7x2', '5x3', '5x4', '5x5', '4x6', '4x7', '4x8', '5-5-5-3-3-3-1-1-1', '15-10-5', '2-3-5-10-2-3-5-10']}

pickle.dump(rep_schemes, open('rep_schemes.pkl', 'wb'))
