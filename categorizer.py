#!venv/bin/python3.5
#
import sys
import os
import json

def containKeyword(keyword, text):
    return text.count(keyword)

def containKeywords(keywords, text):
    i = 0
    t = text.lower()
    for x in keywords:
        i += containKeyword(x, t)
    return i

def categorize(input, dictionary):
    scorer = {}
    total = 0

    # iterate and score dictionary
    for k, v in dictionary.items():
        score = containKeywords(v, input)
        if score != 0:
            scorer[k] = score
            total += score

    # get confidence value
    for k, v in scorer.items():
        scorer[k] = scorer[k] / float(total)

    return scorer

def instagram(entry, dict):
    x = ''
    if isinstance(entry['biography'], str):
        x += entry['biography']
    for n in entry['media']['nodes']:
        x += n['caption']
    return categorize(x).keys()