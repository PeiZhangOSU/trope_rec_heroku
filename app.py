from flask import Flask, render_template, request, redirect
import requests
from bokeh.plotting import figure
from bokeh.embed import components
import pandas as pd

# from __future__ import print_function

app = Flask(__name__)

@app.route('/', methods=['GET'])
def load_index():
     return render_template('index.html')

@app.route('/rec', methods=['GET'])
def trope_rec():
    # all the heavy lifting of recommending tropes
    return render_template('rec.html')

#if __name__ == '__main__':
  #app.run(port=33507)

# Binding PORT for Heroku deployment
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
