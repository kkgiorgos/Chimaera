#!/usr/bin/env python3
"""Build an offline interactive comparison, aggregating repetitions by configuration."""
import json
from pathlib import Path
import sys
from datetime import datetime, timezone
from run_experiments import WORKSPACE
sys.path.insert(0,str(WORKSPACE/'benchmarking'))
from wall_follow_benchmark.comparison import load_comparison
from wall_follow_benchmark.metrics import TASK, ARCHITECTURE, SIMULATION
from wall_follow_benchmark.timing import SCOPE as TIMING_SCOPE


def build_dashboard(runs, output, warmup=0., max_points=1500):
    groups,errors=load_comparison(runs,warmup,max_points)
    payload=dict(runs=groups,errors=errors,warmup=warmup,
                 task_metrics=list(TASK.items()), architecture_metrics=list(ARCHITECTURE.items()),
                 simulation_metrics=list(SIMULATION.items()),
                 timing_scope=TIMING_SCOPE,generated=datetime.now(timezone.utc).isoformat())
    encoded=json.dumps(payload,allow_nan=False,separators=(',',':')).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    assets=Path(__file__).parent/'web'
    html=(assets/'dashboard.html').read_text().replace('__STYLE__',(assets/'dashboard.css').read_text())
    html=html.replace('__SCRIPT__',(assets/'dashboard.js').read_text()).replace('__DATA__',encoded)
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True);output.write_text(html)
    for error in errors:
        print(f'Comparison warning: {error}',file=sys.stderr)
    print(f'Interactive comparison: {output.resolve()} ({len(groups)} configurations)')
    return payload
