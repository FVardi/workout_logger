

import pandas as pd
from datetime import datetime
import pickle
import numpy as np

class WorkoutTracker:

    def __init__(self, date):
        self.date = date
        self.movement_list = []
        self.hhr = False
        self.standard_engine_day = False


    def add_description(self, description, notes=''):

        self.description = description
        self.notes = notes
        self.workout_id = pd.read_pickle('main_log.pkl')['Workout id'].iloc[-1]+1


    def add_heavy_day(self, movement, reference_rep_scheme, weight, true_rep_scheme=None):

        self.workout_type = 'Heavy'

        self.movement_list.append(movement)
        self.rep_scheme = reference_rep_scheme
        self.weight = weight

        if true_rep_scheme is not None:
            self.true_rep_scheme = true_rep_scheme
        else:
            self.true_rep_scheme = reference_rep_scheme

        self.score = ''
        self.time_domain = ''

    def add_heavy_day_at_HHR(self, movement):

        self.hhr = True
        self.hhr_movement = movement

    def add_standard_engine_day(self, movement, reference_rep_scheme, time_domain, score, true_rep_scheme=None):

        self.standard_engine_day = True
        self.workout_type = 'Engine'
        self.movement_list.append(movement)
        self.rep_scheme = reference_rep_scheme

        if true_rep_scheme is not None:
            self.true_rep_scheme = true_rep_scheme
        else:
            self.true_rep_scheme = reference_rep_scheme
        
        self.score = score
        self.time_domain = time_domain

    def add_workout(self, movements, score, time_domain, heavy_day_at_hhr_movement=[]):

        self.movement_list = movements
        self.score = score
        self.time_domain = time_domain
        self.workout_type = 'Workout'

        if len(heavy_day_at_hhr_movement) != 0:
            

    def log_workout(self):

        # Save main log
        # Always needed
        main_df = pd.DataFrame({
            'Date': [self.date],
            'Workout id': [self.workout_id],
            'Description': [self.description],
            'Workout type': [self.workout_type],
            'Notes': [self.notes],
            'Score': [self.score],
            'Time domain': [self.time_domain],
            'Weightlifting': [0],
            'Monostructural': [0],
            'Gymnastic': [0],
            'Pull from ground': [0],
            'Pressing': [0],
            'Below parallel': [0],
            'Upper body pull': [0],
            'Midline': [0],
            'Engine': [0]})
        
        movement_gmp_dict = pickle.load(open('movement_gmp_dict.pkl', 'rb'))
        movement_modality_dict = pickle.load(open('movement_modality_dict.pkl', 'rb'))

        for movement in self.movement_list:
            for gmp in movement_gmp_dict[movement]:
                main_df[gmp] += 1
            for modality in movement_modality_dict[movement]:
                main_df[modality] += 1

        self._update_df('main_log.pkl', main_df)

        # Save heavy day log
        # Only if heavy day of HHR was logged
        if self.workout_type == 'Heavy':

            heavy_day_df = pd.DataFrame({
                'Date': [self.date],
                'Movement': [self.movement_list],
                'Type': [self.workout_type],
                'Rep scheme': [self.rep_scheme],
                'Weight': [self.weight],
                'True rep scheme': [self.true_rep_scheme],
                'Workout id': self.workout_id})
            
            self._update_df('heavy_day_log.pkl', heavy_day_df)

        if self.hhr == True:

            heavy_day_df = pd.DataFrame({
                'Date': [self.date],
                'Movement': [self.movement_list],
                'Type': ['HHR'],
                'Rep scheme': '',
                'Weight': '',
                'True rep scheme': '',
                'Workout id': self.workout_id})
            
            self._update_df('heavy_day_log.pkl', heavy_day_df)

        # Save engine day log
        # Only if pure engine or mixed engine day was logged
        if self.workout_type == 'Engine':
            engine_log = pd.DataFrame({
                'Date': [self.date],
                'Workout id': [self.workout_id],
                'Movement': self.movement_list,
                'Type': ['Single'],
                'Rep scheme': [self.rep_scheme],
                'True rep scheme': [self.true_rep_scheme],
                'Score': [self.score]})
            
            self._update_df('engine_log.pkl', engine_log)

        elif any(movement in ['Run', 'Row', 'Bike'] for movement in self.movement_list):

            engine_log = pd.DataFrame({
                'Date': [self.date],
                'Workout id': [self.workout_id],
                'Movement': self.movement_list,
                'Type': ['Mixed'],
                'Rep scheme': '',
                'True rep scheme': '',
                'Score': ''})
            self._update_df('engine_log.pkl', engine_log)
        
        # Save all movements
        movement_log = pd.read_pickle('movement_log.pkl')
        existing_movements = movement_log.columns
        
        new_entry = pd.DataFrame(
            np.zeros((1, len(existing_movements))),
            columns=existing_movements)

        for i in range(len(self.movement_list)):
            if movement not in existing_movements:
                new_entry[self.movement_list[i]] = 0
            new_entry[self.movement_list[i]] += 1
        
        new_entry['Date'] = self.date
        new_entry['Workout id'] = self.workout_id
        
        movement_log = pd.concat((
            movement_log,
            new_entry),
            axis=0)
        
        movement_log.to_pickle('movement_log.pkl')


    def modify_movement_dicts(self, movement, add_or_delete, gmp_list=None, modality_list=None):

        gmp_dict = pickle.load(open('movement_gmp_dict.pkl', 'rb'))
        modality_dict = pickle.load(open('movement_modality_dict.pkl', 'rb'))

        gmp_reference = [
            'Pull from ground',
            'Pressing',
            'Below parallel',
            'Upper body pull',
            'Midline',
            'Engine'
        ]

        modality_reference = [
            'Weightlifting',
            'Monostructural',
            'Gymnastic'
        ]

        if all(pattern in gmp_reference for pattern in gmp_list):
            if add_or_delete == 'add':
                gmp_dict[movement] = gmp_list
            if add_or_delete == 'delete':
                del gmp_dict[movement]

        if all(modality in modality_reference for modality in modality_list):
            if add_or_delete == 'add':
                modality_dict[movement] = modality_list
            if add_or_delete == 'delete':
                del modality_dict[movement]

        pickle.dump(gmp_dict, open('movement_gmp_dict.pkl', 'wb'))
        pickle.dump(modality_dict, open('movement_modality_dict.pkl', 'wb'))


    def show_file(self, name):

        df = pickle.load(open(name+'.pkl', 'rb'))
        print(df)


    def delete_workout(self, workout_id):

        engine_log = pickle.load(open('engine_log.pkl', 'rb'))
        heavy_day_log = pickle.load(open('heavy_day_log.pkl', 'rb'))
        main_log = pickle.load(open('main_log.pkl', 'rb'))
        movement_log = pickle.load(open('movement_log.pkl', 'rb'))

        engine_log = engine_log[engine_log['Workout id'] != workout_id]
        heavy_day_log = heavy_day_log[heavy_day_log['Workout id'] != workout_id]
        main_log = main_log[main_log['Workout id'] != workout_id]
        movement_log = movement_log[movement_log['Workout id'] != workout_id]

        pickle.dump(engine_log, open('engine_log.pkl', 'wb'))
        pickle.dump(heavy_day_log, open('heavy_day_log.pkl', 'wb'))
        pickle.dump(main_log, open('main_log.pkl', 'wb'))
        pickle.dump(movement_log, open('movement_log.pkl', 'wb'))

    def _update_df(self, log_df_name, new_df):

        old_df = pd.read_pickle(log_df_name)

        combined_df = pd.concat((old_df,
                                 new_df),
                                 axis=0)
        
        combined_df.to_pickle(log_df_name)
