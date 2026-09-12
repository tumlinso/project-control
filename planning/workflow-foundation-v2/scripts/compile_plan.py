#!/usr/bin/env python3
"""Pure projection of the authored catalog; never invokes generic v2 lowering."""
from pathlib import Path
import argparse,sys
sys.dont_write_bytecode=True
from common import load,canonical,demand
PACKAGE=Path(__file__).resolve().parents[1]

def assemble(master):
    return {**master['native_header'], 'tasks':[t['native'] for t in master['tasks']]}

def main():
    a=argparse.ArgumentParser(description=__doc__);a.add_argument('--check',action='store_true');a.add_argument('--output',type=Path)
    o=a.parse_args();m=load(PACKAGE/'machine/proposed_todos.json');data=canonical(assemble(m));dest=PACKAGE/'machine'/m['native_plan_file']
    if o.check or not o.output:
        demand(dest.read_bytes()==data,'native plan differs from authored projection');print('Native schema-3 projection matches.')
    if o.output:
        with o.output.open('xb') as f:f.write(data)
if __name__=='__main__':main()
