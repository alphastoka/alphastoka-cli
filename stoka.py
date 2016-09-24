#!venv/bin/python3.5
#
import requests, re, click
from bs4 import BeautifulSoup
from Stokafile import Config
from sys import argv
import os

@click.group()
def cli():
    pass

@click.command(help='install new strand')
@click.argument('strand')
def install(strand):
    click.echo('Fetching DNA strand ' + str(strand))

@click.command(help='show all strands installed')
def strands():
    strands = os.listdir('./stoka_strands')
    click.echo('You have %d strand(s) available' % (len(strands)))
    for strand in strands:
        click.echo('- %s' % (strand,))
    click.echo('Run `stoka spawn --strand <strand_name>` to spawn new Stoka.')

@click.command(help='spawn new Stoka from strand')
@click.option('--name', default='Untitled Stoka', help='(optional) name of instance')
@click.option('--isolate', default=False, help='Isolate this Stoka instance memory.')
@click.argument('strand')
def spawn(name, strand, isolate):
    click.echo('Spawning a new stoka instance, %s using %s DNA' %(name, strand))

@click.command(help='list active stoka')
def ls():
    click.echo()
    
cli.add_command(install)
cli.add_command(spawn)
cli.add_command(strands)
cli.add_command(ls)

if __name__ == '__main__':
    # default command
    cli()