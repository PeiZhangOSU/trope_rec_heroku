# coding=utf-8
from flask import Flask, render_template, request, redirect
import requests

import pickle, gzip
import re
import copy
import heapq
import os
import urlparse

import pandas as pd
from bokeh.plotting import figure
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
# TODO: deal with database errors?

class TropeRecPsql(object):
    def __init__(self, input_string, db_conn):
        self.cur = db_conn.cursor()
        # init dictionaries
        self.assn_dict = {} # will be the same format as trope_assn_dict that was used before database
        self.freq_dict = {}

        # user input should be a string of comma separated tropes.
        if not isinstance(input_string, basestring):
            raise TypeError('User input should be a string')
        # make user input an unicode string if it is not unicode
        if isinstance(input_string, str):
            input_string = input_string.encode('utf-8')

        self.usr_tropes = split_csv_str(input_string)
        self.format_tropes()
        self.make_dict_from_input() # will populate self.assn_dict and self.freq_dict with self.user_tropes as keys
        if len(self.usr_tropes) < 2:
            raise ValueError('Not enough tropes for analysis. Please try again with more tropes.')

        self.combine_neighbors()
        if len(self.common_count_dict) < 1:
            raise ValueError('No shared associations. Please try again with different tropes.')
        self.expand_freq_dict()

        # at the end of init, no need for db cursor anymore
        self.cur.close()

    def get_usr_tropes(self):
        return copy.deepcopy(self.usr_tropes)

    def get_common_count_dict(self):
        return copy.deepcopy(self.common_count_dict)

    def format_tropes(self):
        self.usr_tropes = [strip_startcase(t) for t in self.usr_tropes]

    def make_dict_from_input(self):
        validated_tropes = []
        for trope in self.usr_tropes:
            self.cur.execute("SELECT COUNT(*) FROM tropes WHERE trope = %s;", (trope,))
            if self.cur.fetchone()[0] > 0:
                validated_tropes.append(trope)
                self.cur.execute("SELECT trope, freq, connections FROM tropes WHERE trope = %s;", (trope,))
                row = self.cur.fetchone()
                self.assn_dict[row[0]] = row[2] # make sure the index match trope, connections
                self.freq_dict[row[0]] = row[1] # make sure the index match trope, freq
        self.usr_tropes = validated_tropes

    def combine_neighbors(self):
        # calculate a count dict {trope: weight_sum} for neighbor tropes that are shared by all of self.usr_tropes
        dict_list = [self.assn_dict[t] for t in self.usr_tropes]
        self.common_count_dict = join_n_dicts(dict_list)

    def expand_freq_dict(self):
        # make a dictionary of {trope: its overall frequncy (not number of associations)}
        for trope in self.common_count_dict.keys():
            self.cur.execute("SELECT trope, freq FROM tropes WHERE trope = %s;", (trope,))
            row = self.cur.fetchone()
            self.freq_dict[row[0]] = row[1] # make sure the index match trope, freq

    def find_top_n(self, n=5, penalize_frequents = True):
        if penalize_frequents:
            dict_for_sorting = {key: value * 1.0 / self.freq_dict[key]
                                for key, value in self.common_count_dict.iteritems()}
        else:
            dict_for_sorting = self.common_count_dict

        if len(dict_for_sorting) < 1:
            return None
        elif n > len(dict_for_sorting):
            return [(trope, self.freq_dict[trope]) for trope in dict_for_sorting.keys()]
        else:
            return [(trope, count) for (count, trope) in heapsort_nlargest(dict_for_sorting, n)]

    def get_recommendations(self, n=5, penalize_frequents=True, format_tropes=True):
        # returns only the names of tropes
        results = [trope for (trope, count) in self.find_top_n(n, penalize_frequents)]
        if format_tropes: # Output will be formatted like 'Shout Out'
            return [add_space(trope) for trope in results]
        else: # Output will be formatted like 'ShoutOut', with no spaces
            return results



# Rendering pages --------------------
@app.route('/', methods=['GET'])
def trope_rec():
    textarea_args = request.args.get('usrtropes')
    if textarea_args:
        conn = get_conn()
        try:
            rec_eng = TropeRecPsql(textarea_args, conn)
            rec_title = 'Here are the recommended tropes based on your list:'
            # rec_results: each tuple in the format of ('Shout Out', 'ShoutOut'), in order to display tvtropes link
            rec_results = [(add_space(trope), trope) for trope in rec_eng.get_recommendations(format_tropes=False)]
            conn.close()
        except ValueError as e:
            rec_title = getattr(e, 'message', repr(e))
            rec_results = []
    else:
        rec_title = ''
        rec_results = []
        # Keep trope suggestions if no textarea_args
        textarea_args = 'Haunted House, Ironic Nursery Tune'
    return render_template('recommendations.html', rec_title=rec_title, rec_results=rec_results, textarea_args=textarea_args)

@app.route('/howitworks', methods=['GET'])
def load_howitworks():
     return render_template('howitworks.html')

@app.route('/funfacts', methods=['GET'])
def load_insights():
    return render_template('funfacts.html')

@app.route('/about', methods=['GET'])
def load_about():
    return render_template('about.html')


# Running the app -------------------

## This does not seem to wort at times
#if __name__ == '__main__':
  #app.run(port=33507)

# Binding PORT for Heroku deployment
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
