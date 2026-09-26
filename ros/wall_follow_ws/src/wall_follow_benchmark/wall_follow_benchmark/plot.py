"""Compare configurations using means and sample standard deviations over repetitions."""
import argparse
import csv
import json
import textwrap
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from .analysis import summarize, run_label  # Retain existing imports used by callers/tests.
from .comparison import load_comparison, write_summaries, configuration_rows


def signal(ax, group, key, scale=1.):
    t=np.asarray(group['series']['elapsed'],dtype=float)
    if not len(t):
        return
    y=np.asarray(group['series'][key],dtype=float)*scale
    sd=np.asarray(group['series_std'][key],dtype=float)*scale
    line,=ax.plot(t,y,label=f"{group['name']} (n={group['count']})")
    ax.fill_between(t,y-sd,y+sd,color=line.get_color(),alpha=.18)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runs',nargs='+',type=Path)
    parser.add_argument('--output',type=Path,default=Path('comparison'))
    parser.add_argument('--warmup',type=float,default=5.)
    args=parser.parse_args()
    try:
        groups,errors=load_comparison(args.runs,args.warmup)
    except ValueError as exc:
        parser.error(str(exc))
    args.output.mkdir(parents=True,exist_ok=True)
    for error in errors:
        print(f'Skipping: {error}')
    fig,axes=plt.subplots(2,2,figsize=(13,9))
    for group in groups:
        signal(axes[0,0],group,'gt_error')
        if group['series']['elapsed']:
            axes[0,1].plot(group['series']['x'],group['series']['y'],label=f"{group['name']} (n={group['count']})")
        signal(axes[1,0],group,'compute_ms')
        signal(axes[1,1],group,'scan_age',1000.)
    for width,height in sorted({(g['arena']['arena_width'],g['arena']['arena_height']) for g in groups}):
        hx,hy=width/2,height/2
        axes[0,1].plot([-hx,hx,hx,-hx,-hx],[-hy,-hy,hy,hy,-hy],'--',linewidth=1,label=f'Arena {width:g} × {height:g} m')
    axes[0,0].set(xlabel='Elapsed simulation time (s)',ylabel='Wall error mean ± SD (m)')
    axes[0,1].set(xlabel='World x (m)',ylabel='World y (m)',title='Mean trajectory, aligned by elapsed time',aspect='equal')
    axes[1,0].set(xlabel='Elapsed simulation time (s)',ylabel='Computation mean ± SD (ms)')
    axes[1,1].set(xlabel='Elapsed simulation time (s)',ylabel='Scan age mean ± SD (simulation ms)')
    for ax in axes.flat:
        ax.grid(True,alpha=.3);ax.legend(fontsize=6)
    fig.suptitle('Repetition means; bands = sample standard deviation (not confidence intervals)',fontsize=11)
    fig.tight_layout();fig.savefig(args.output/'comparison.png',dpi=160);plt.close(fig)
    fig,axes=plt.subplots(1,3,figsize=(13,5))
    for ax,key,title in zip(axes,['rmse_m','path_m','compute_p95_ms'],
                           ['Tracking RMSE (m)','Distance travelled (m)','Per-run computation p95 (ms)']):
        means=[g['metric_stats'][key]['mean'] for g in groups]
        ax.bar(range(len(groups)),[v if v is not None else np.nan for v in means],color='#197e89')
        for i,g in enumerate(groups):
            st=g['metric_stats'][key]
            if st['std'] is not None:
                ax.errorbar(i,st['mean'],yerr=st['std'],fmt='none',ecolor='#172b40',capsize=5)
        ax.set_xticks(range(len(groups)))
        ax.set_xticklabels([f"{g['name']}\nn={g['metric_stats'][key]['n']}" for g in groups],rotation=30,ha='right',fontsize=7)
        ax.set_title(title);ax.grid(axis='y',alpha=.3)
    fig.suptitle('Equal weight per run · mean ± sample SD · no SD estimate for n=1',fontsize=11)
    fig.tight_layout();fig.savefig(args.output/'metrics.png',dpi=160);plt.close(fig)
    write_summaries(groups,args.output)
    config_rows=configuration_rows(groups)
    (args.output/'configuration.json').write_text(json.dumps(config_rows,indent=2,allow_nan=False))
    with (args.output/'configuration.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['setting','differs']+[g['id'] for g in groups])
        writer.writeheader()
        writer.writerows({k:json.dumps(v,sort_keys=True) if isinstance(v,(dict,list)) else v for k,v in row.items()} for row in config_rows)
    differences=[row for row in config_rows if row['differs']]
    fig,ax=plt.subplots(figsize=(max(9,len(groups)*2.2+4),max(2.5,len(differences)*.65+1.5)))
    ax.axis('off')
    def display(value):
        text='Not recorded' if value is None else str(value)
        return textwrap.fill(text if len(text)<100 else text[:97]+'...',28)
    if differences:
        table=ax.table(cellText=[[row['setting']]+[display(row[g['id']]) for g in groups] for row in differences],
                       colLabels=['Differing setting']+[f"{g['id'][:6]} (n={g['count']})" for g in groups],
                       loc='center',cellLoc='left',bbox=[0,0,1,1])
        table.auto_set_font_size(False);table.set_fontsize(8)
        for (row,col),cell in table.get_celld().items():
            cell.set_facecolor('#eaf0f5' if row==0 else '#fff4d9')
            cell.set_edgecolor('#d8e1e9')
    else:
        ax.text(.5,.5,'No recorded configuration differences',ha='center',va='center')
    ax.set_title('Configuration differences (full values in configuration.csv)',pad=15)
    fig.tight_layout();fig.savefig(args.output/'configuration.png',dpi=160);plt.close(fig)
    print(f"Compared {sum(g['count'] for g in groups)} runs in {len(groups)} configuration groups → {args.output}")


if __name__=='__main__':
    main()
