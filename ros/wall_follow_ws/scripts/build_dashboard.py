#!/usr/bin/env python3
"""Build an offline interactive comparison, aggregating repetitions by configuration."""
import argparse
import json
from pathlib import Path
import sys
from datetime import datetime, timezone
from compare_experiments import discover
from run_experiments import WORKSPACE
sys.path.insert(0,str(WORKSPACE/'src/wall_follow_benchmark'))
from wall_follow_benchmark.comparison import load_comparison


def build_dashboard(runs, output, warmup=5., max_points=1500):
    groups,errors=load_comparison(runs,warmup,max_points)
    payload=dict(runs=groups,errors=errors,warmup=warmup,generated=datetime.now(timezone.utc).isoformat())
    encoded=json.dumps(payload,allow_nan=False,separators=(',',':')).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    assets=Path(__file__).parent/'web'
    html=(assets/'dashboard.html').read_text().replace('__STYLE__',(assets/'dashboard.css').read_text())
    html=html.replace('__SCRIPT__',(assets/'dashboard.js').read_text()).replace('__DATA__',encoded)
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True);output.write_text(html)
    for error in errors:
        print(f'Skipped malformed run: {error}',file=sys.stderr)
    print(f'Interactive comparison: {output.resolve()} ({len(groups)} configurations)')
    return payload


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('roots',nargs='+',type=Path)
    parser.add_argument('--output',type=Path,default=Path('dashboard.html'))
    parser.add_argument('--warmup',type=float,default=5.)
    parser.add_argument('--max-points',type=int,default=1500,help='Time-aligned chart grid limit; scalar metrics use all original samples')
    parser.add_argument('--include-incomplete',action='store_true')
    args=parser.parse_args()
    try:
        paths=discover([p.expanduser() for p in args.roots],args.include_incomplete)
        build_dashboard(paths,args.output.expanduser(),args.warmup,args.max_points)
    except (OSError,ValueError) as exc:
        parser.error(str(exc))


if __name__=='__main__':
    main()
