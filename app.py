# coding=utf-8
from flask import Flask, render_template, request, redirect
import requests

import pickle, gzip
import re
import heapq

import pandas as pd
from bokeh.plotting import figure
from bokeh.embed import components

import os
import psycopg2
import urlparse

app = Flask(__name__)


def get_postgres_url():
    # Connect to postgres database --------------------
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


# # Load dictionary -------------------- # TODO: loading speed is too low to be practical
# pkl_file = open('static/trope_assn_dict.pkl', 'rb')
# trope_assn_dict = pickle.load(pkl_file)
# pkl_file.close()


# Constants --------------------------
# These top 20 tropes account for 5% of all trope counts.
# Tropes with > 18 counts are at the 0.999 quantile of all tropes.
STAPLE_TROPES = [u'ShoutOut',
                 u'ChekhovsGun',
                 u'DeadpanSnarker',
                 u'OhCrap',
                 u'Foreshadowing',
                 u'Jerkass',
                 u'BittersweetEnding',
                 u'LargeHam',
                 u'TitleDrop',
                 u'MeaningfulName',
                 u'BerserkButton',
                 u'RunningGag',
                 u'TheCameo',
                 u'BigBad',
                 u'KarmaHoudini',
                 u'GroinAttack',
                 u'WhatHappenedToTheMouse',
                 u'BrickJoke',
                 u'DownerEnding',
                 u'BookEnds']
GENRE_LIST = [u'Action',
              u'Adventure',
              u'Animation',
              u'Biography',
              u'Comedy',
              u'Crime',
              u'Documentary',
              u'Drama',
              u'Family',
              u'Fantasy',
              u'Film-Noir',
              u'History',
              u'Horror',
              u'Music',
              u'Musical',
              u'Mystery',
              u'Romance',
              u'Sci-Fi',
              u'Short',
              u'Sport',
              u'Thriller',
              u'War',
              u'Western']

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
    text = re.sub(r'-', ' ', text)
    text = re.sub(r'[^\w ]', '', text)
    return ''.join(w.title() for w in text.split())

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


# TropeRecommender class -------------
class TropeRecommender(object):

    def __init__(self, input_string, assn_dict, staple_tropes=STAPLE_TROPES):
        # Old: assn_dict=trope_assn_dict, TODO New: generate assn_dict from postgres db based on input_string

        # master dictionary of trope associations
        self.assn_dict = assn_dict
        self.staple_tropes = staple_tropes

        # user input should be a string of comma separated tropes.
        if not isinstance(input_string, basestring):
            raise TypeError('User input should be a string')
        # make user input an unicode string if it is not unicode
        if isinstance(input_string, str):
            input_string = input_string.encode('utf-8')

        self.usr_tropes = split_csv_str(input_string)
        self.format_tropes()
        self.validate_tropes()
        if len(self.usr_tropes) < 2:
            raise ValueError('Not enough tropes for analysis')

        self.sum_total_connetions()
        self.combine_neighbors()
        if len(self.common_count_dict) > 1:
            self.filter_staples()
        else:
            raise ValueError('No shared associations')

    def get_usr_tropes(self):
        return self.usr_tropes

    def get_common_count_dict(self):
        return self.common_count_dict

    def get_staple_count_dict(self):
        return self.staple_count_dict

    def get_sorted_staple_counts(self):
        return [(t, n) for (n, t) in heapsort_nlargest(self.staple_count_dict, len(self.staple_count_dict))]

    def get_non_staple_count_dict(self):
        return self.non_staple_count_dict

    def format_tropes(self):
        self.usr_tropes = [strip_startcase(t) for t in self.usr_tropes]

    def validate_tropes(self):
        # TODO: if switching to database queries, need to be changed to 'if key in list_of_legit_keys'
        self.usr_tropes = [t for t in self.usr_tropes if t in self.assn_dict]

    def sum_total_connetions(self):
        # counting all connections, whether shared by all tropes or not
        self.total_connections = 0
        for trope in self.usr_tropes:
            self.total_connections += sum(self.assn_dict[trope].values())

    def combine_neighbors(self):
        # calculate a count dict {trope: weight_sum} for neighbor tropes that are shared by all of self.usr_tropes
        dict_list = [self.assn_dict[t] for t in self.usr_tropes]
        self.common_count_dict = join_n_dicts(dict_list)

    def filter_staples(self, staple_list=None):

        if staple_list == None:
            staple_list = self.staple_tropes

        self.staple_count_dict = {}
        self.non_staple_count_dict = self.common_count_dict.copy()
        for key in staple_list:
            value = self.non_staple_count_dict.pop(key, None)
            if value:
                self.staple_count_dict[key] = value

    def find_top_n(self, n=5, filter_out_staples=True):
        if filter_out_staples:
            dict_for_sorting = self.non_staple_count_dict
        else:
            dict_for_sorting = self.common_count_dict

        if n > len(dict_for_sorting):
            return None
        else:
            return [(key, value) for (value, key) in heapsort_nlargest(dict_for_sorting, n)]


# Rendering pages --------------------
@app.route('/', methods=['GET'])
def load_index():
     return render_template('index.html')

@app.route('/insights', methods=['GET'])
def load_insights():
    return render_template('insights.html')

@app.route('/about', methods=['GET'])
def load_about():
    return render_template('about.html')

@app.route('/rec', methods=['GET'])
def trope_rec():
    # # testing rec__results
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT trope, freq, connections FROM tropes WHERE trope = 'FrothyMugsOfWater';")
    #cur.execute("SELECT 1 FROM tropes;")
    trope, freqs, connections = cur.fetchone()
    cur.close()
    #results = 'Here are some results'
    return render_template('rec.html', rec_results = freqs)

# Running the app -------------------

## This does not seem to wort at times
#if __name__ == '__main__':
  #app.run(port=33507)

# Binding PORT for Heroku deployment
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
