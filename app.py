# coding=utf-8
from flask import Flask, render_template, request, redirect, jsonify
import requests

import pickle, gzip
import re
import copy
import heapq
import os
import urlparse

import pandas as pd
import numpy as np
from bokeh.plotting import figure
from bokeh.charts import Bar
from bokeh.charts.attributes import CatAttr
from bokeh.embed import components


import psycopg2
import unidecode


app = Flask(__name__)
app.config['DEBUG'] = os.environ.get('DEBUG', False)

# Postgres database helper functions -----
def get_postgres_url():
    urlparse.uses_netloc.append("postgres")
    return urlparse.urlparse(os.environ["DATABASE_URL"])

def get_conn():
    url = get_postgres_url()

    return psycopg2.connect(
        database=url.path[1:],
        user=url.username,
        password=url.password,
        host=url.hostname,
        port=url.port
    )


# Constants --------------------------
# trope frequencies by genre, for plotting
freqs_each_genre_df = pd.read_csv('static/freqs_each_genre.csv', index_col=0)

# total number of movies that are used to populate the psql database containing 9387 tropes
TOTAL_MOVIES = 5923

# total number of trope apprearences from DBtropes data,
# used to calculate trope freq in the psql database
TOTAL_TROPE_APPEARENCES = 257349

# Helper functions -------------------
def add_space(text):
    '''Add spaces between captalized words, for better display of trope and movie names in the output.
       Spaces should be added just before the strings need to be outputted.'''
    return re.sub(r"\B([A-Z])", r" \1", text)

def strip_startcase(text):
    '''Strip spaces, capitalization, punctuation and accents from a string (movie titles or trope names).
       Weird unicode characters will be converted to ascii, eg. 'Nausicaä' to 'Nausicaa'.
       Then the text will be converted into a string of concatenated captalized words w/o spaces,
       eg.: 'BookEnds', 'YoureNotMyFather', 'RightForTheWrongReasons'. '''
    text = unidecode.unidecode(text)
    if ' ' in text:
        text = re.sub(r'-', ' ', text)
        text = re.sub(r'[^\w ]', '', text)
        return ''.join(w[0].upper() + w[1:] for w in text.split())
    else:
        return text

def split_csv_str(text):
    '''Take a string of comma separated values, return a list of values with leading/trailing spaces removed.'''
    return [s.strip() for s in text.split(',')]

def join_two_dicts(dict1, dict2):
    '''Take 2 dictionaries, return a new dictionary, keeping only shared keys.
       All input/output dictionaries have the same format: {str1: int1, str2: int2, ...}.
       Each key will have a new value equals to the sum of all its old values among input dictionaries.'''
    result_dict = {key : (dict1[key] + dict2[key]) for key in dict2.keys() if key in dict1}
    return result_dict

def join_n_dicts(dict_list):
    '''Take n (n>=2) dictionaries, return a new dictionary, keeping only shared keys.
       All input/output dictionaries have the same format: {str1: int1, str2: int2, ...}.
       Each key will have a new value equals to the sum of all its old values among input dictionaries.'''
    if len(dict_list) <= 1:
        return dict_list
    else:
        # reduce() will become functools.reduce() in Python 3
        return reduce(lambda x, y: join_two_dicts(x, y), dict_list)

def heapsort_nlargest(my_dict, n):
    '''Use heaps to efficiently find n largest items (sorted by value) from a dictionary.
       Return [(count_1st, item_1st), (count_2nd, item_2nd), ...]'''
    h = []
    for key, value in my_dict.items():
        heapq.heappush(h, (value, key))
    return heapq.nlargest(n, h)

def lift(count_ab, count_a, count_b, total_transactions):
    return (count_ab * total_transactions) / (count_a * count_b)

def common_keys(dict_list):
    '''Take n (n>=2) dictionaries, return a list of keys that are shared by all dictionaries.'''
    if len(dict_list) <= 1:
        return dict_list
    shortest_dict = min(dict_list, key=len)
    shared_keys = []
    for key in shortest_dict.keys():
        is_shared = True
        for each_dict in dict_list:
            if key not in each_dict:
                is_shared = False
        if is_shared:
            shared_keys.append(key)
    return shared_keys


# TropeRecommender class -------------
class TropeRecPsqlLift(object):
    # Use average lift(user_trope, candidate_trope) as recommendation criteria
    def __init__(self, input_string, db_conn):
        self.cur = db_conn.cursor()

        # init dictionaries
        self.freq_dict = {}
        self.assn_dict = {} # will be the same format as trope_assn_dict that was used before database
        self.lifts_dict = {} # {trope_a :[lift w/user_trope1, lift w/user_trope2, ...]}
        self.avg_lift_dict = {} # {trope_a : avg(lift of trope_a with each user trope)}

        # user input should be a string of comma separated tropes.
        if not isinstance(input_string, basestring):
            raise TypeError('User input should be a string')
        # make user input an unicode string if it is not unicode
        if isinstance(input_string, str):
            input_string = input_string.encode('utf-8')

        self.user_tropes = split_csv_str(input_string)
        self.format_tropes()
        self.make_dict_from_input() # will populate self.assn_dict and self.freq_dict with self.user_tropes as keys
        if len(self.user_tropes) < 2:
            raise ValueError('Not enough tropes for analysis. Please try again with more tropes.')

        self.combine_neighbors()
        if len(self.common_neighbors) < 1:
            raise ValueError('No shared associations. Please try again with different tropes.')
        self.expand_freq_dict()

        self.make_lifts_dict()
        self.make_avg_lift_dict()

        # at the end of init, no need for db cursor anymore
        self.cur.close()

    def get_user_tropes(self):
        return copy.deepcopy(self.user_tropes)

    def get_common_neighbors(self):
        return copy.deepcopy(self.common_neighbors)

    def get_lifts_dict(self):
        return copy.deepcopy(self.lifts_dict)

    def get_avg_lift_dict(self):
        return copy.deepcopy(self.avg_lift_dict)

    def format_tropes(self):
        self.user_tropes = [strip_startcase(t) for t in self.user_tropes]

    def make_dict_from_input(self):
        validated_tropes = []
        for trope in self.user_tropes:
            self.cur.execute("SELECT COUNT(*) FROM tropes WHERE trope = %s;", (trope,))
            if self.cur.fetchone()[0] > 0:
                validated_tropes.append(trope)
                self.cur.execute("SELECT trope, freq, connections FROM tropes WHERE trope = %s;", (trope,))
                row = self.cur.fetchone()
                current_user_trope = row[0]
                current_user_trope_freq = row[1]
                current_user_trope_connections = row[2]

                self.freq_dict[current_user_trope] = current_user_trope_freq
                self.assn_dict[current_user_trope] = current_user_trope_connections

        self.user_tropes = validated_tropes

    def combine_neighbors(self):
        # Return a list of neighbor tropes that are shared by all of self.user_tropes
        dict_list = [self.assn_dict[t] for t in self.user_tropes]
        self.common_neighbors = common_keys(dict_list)

    def expand_freq_dict(self):
        # make a dictionary of {trope: its overall frequncy (not number of associations)}
        for trope in self.common_neighbors:
            self.cur.execute("SELECT trope, freq FROM tropes WHERE trope = %s;", (trope,))
            row = self.cur.fetchone()
            self.freq_dict[row[0]] = row[1] # make sure the index match trope, freq

    def make_lifts_dict(self):
       # {trope_a :[lift w/user_trope1, lift w/user_trope2, ...],
       #  trope_b :[lift w/user_trope1, lift w/user_trope2, ...]}
        for candidate_trope in self.common_neighbors:
            current_list = []
            count_c = self.freq_dict[candidate_trope] * TOTAL_TROPE_APPEARENCES
            for user_trope in self.user_tropes:
                # u: user trope, c: candidate trope
                count_uc = self.assn_dict[user_trope][candidate_trope]
                count_u = self.freq_dict[user_trope] * TOTAL_TROPE_APPEARENCES
                current_list.append(lift(count_uc, count_u, count_c, TOTAL_MOVIES))
            self.lifts_dict[candidate_trope] = current_list

    def make_avg_lift_dict(self):
        self.avg_lift_dict = {key : np.mean(value_list) for key, value_list in self.lifts_dict.iteritems()}

    def find_top_n(self, n):
        if len(self.avg_lift_dict) < 1:
            return None
        elif n > len(self.avg_lift_dict):
            return [(trope, avg_lift) for trope, avg_lift in self.avg_lift_dict.iteritems()]
        else:
            return [(trope, avg_lift) for (avg_lift, trope) in heapsort_nlargest(self.avg_lift_dict, n)]

    def get_recommendations(self, n=5, spaces_in_tropes=False):
        # return (recommended_trope, avg_lift)
        results = self.find_top_n(n)
        if spaces_in_tropes: # Output will be formatted like 'Shout Out'
            return [(add_space(trope), avg_lift) for (trope, avg_lift) in results]
        else: # Output will be formatted like 'ShoutOut', with no spaces
            return results

# Plotting Functions ------------------

def horizontal_plot_freq_by_trope(my_trope, freqs_each_genre_df=freqs_each_genre_df):
    data = freqs_each_genre_df[freqs_each_genre_df['trope'] == my_trope].transpose()
    data = data.drop(data.index[0]).reset_index()
    data.columns = ['genres', my_trope]
    custom_y_range = [g for g in data['genres'].values][::-1]

    p = figure(title='Trope Frequencies of {}'.format(my_trope), y_range=custom_y_range,
              y_axis_label='Genre', x_axis_label='Frequencies (%)', toolbar_location=None)

    p.hbar(y=data['genres'], left=0, right=data[my_trope] * 100, height=0.7, color='#31a354', legend=False)

    return p

def horizontal_plot_freq_by_genre(my_genre, freqs_each_genre_df=freqs_each_genre_df):
    data = freqs_each_genre_df[['trope', my_genre]].nlargest(20, my_genre)
    custom_y_range = [g for g in data['trope'].values][::-1]
    p1 = figure(title='Trope Frequencies in {}'.format(my_genre), y_range=custom_y_range,
                y_axis_label='Trope', x_axis_label='Frequencies (%)', toolbar_location=None)
    p1.hbar(y=data['trope'], left=0, right=data[my_genre] * 100, height=0.7,
            color='#6baed6', legend=False)
    return p1

# Rendering pages --------------------
@app.route('/', methods=['GET'])
def trope_rec():
    textarea_args = request.args.get('user_tropes')
    if textarea_args:
        conn = get_conn()
        try:
            rec_eng = TropeRecPsqlLift(textarea_args, conn)
            rec_title = 'Here are the recommended tropes based on your list:'
            # rec_results: each tuple in the format of ('ShoutOut', 'Shout Out', avg_lift), in order to display tvtropes link
            rec_results = [(trope, add_space(trope), '{0:.1f}'.format(avg_lift)) for trope, avg_lift in rec_eng.get_recommendations()]
            conn.close()
        except ValueError as e:
            rec_title = getattr(e, 'message', repr(e))
            rec_results = []
    else:
        rec_title = ''
        rec_results = []
        # Keep trope suggestions if no textarea_args
        textarea_args = 'Mad Scientist, \nHeroic Sacrifice'
    return render_template('recommendations.html', rec_title=rec_title, rec_results=rec_results, textarea_args=textarea_args)

@app.route('/whataretropes', methods=['GET'])
def load_whataretropes():
     return render_template('whataretropes.html')

@app.route('/howitworks', methods=['GET'])
def load_howitworks():
     return render_template('howitworks.html')

@app.route('/funfacts', methods=['GET'])
def load_insights():
    # bokeh plot by trope, id="bokeh_by_trope"
    trope_to_plot = request.args.get('trope_search_plot')
    if trope_to_plot:
        trope_to_plot = ''.join(trope_to_plot.split())
    else:
        trope_to_plot = 'Zeerust'
    plot_by_trope = horizontal_plot_freq_by_trope(trope_to_plot)
    script_by_trope, div_by_trope = components(plot_by_trope)

    return render_template('funfacts.html',
                           script_by_trope=script_by_trope, div_by_trope=div_by_trope, trope_name=trope_to_plot)

@app.route('/about', methods=['GET'])
def load_about():
    return render_template('about.html')

@app.route('/api/1/by_name/<name>', methods=['GET'])
def fetch_tropes_by_name(name):
    with get_conn() as conn:
        with conn.cursor() as cur:
            name_like = "%{}%".format(name)
            cur.execute("SELECT trope FROM tropes WHERE trope ILIKE %s ORDER BY trope", (name_like,))
            names = [row[0] for row in cur.fetchall()]
            return jsonify({'tropes': names})

@app.route('/api/1/tropes', methods=['GET'])
def fetch_all_tropes():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT trope FROM tropes ORDER BY trope")
            names = [add_space(row[0]) for row in cur.fetchall()]
            return jsonify({'tropes': names})

# Running the app -------------------

## This does not seem to wort at times
#if __name__ == '__main__':
  #app.run(port=33507)

# Binding PORT for Heroku deployment
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
