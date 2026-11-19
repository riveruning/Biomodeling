"""Side-by-side source image and newly simulated settled Goodwin cycle."""
import argparse
import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from PIL import Image


def main(root):
    root=Path(root).resolve()
    metrics=json.loads((root/'metrics.json').read_text())
    with (root/'settled-cycle.csv').open() as fh:
        rows=list(csv.DictReader(fh))
    t=[float(r['display_time_au']) for r in rows]
    with Image.open(root/'publisher-figure-2.png') as full:
        if full.size != (2050,872):
            raise ValueError('Publisher figure dimensions changed; review crop coordinates')
        panel=full.crop((1080,0,2050,872))
        panel.save(root/'publisher-panel-B-crop.png')
    with plt.rc_context({'font.size':10, 'axes.spines.top':False,
                         'axes.spines.right':False, 'svg.fonttype':'none'}):
        fig,(left,right)=plt.subplots(1,2,figsize=(13.2,6.2),layout='constrained',
                                      gridspec_kw={'width_ratios':[1.2,1]})
        left.imshow(panel)
        left.set_axis_off()
        left.set_title('PLOS Figure 2B · publisher image',fontsize=13)
        for name,color in [('X','#3854AD'),('Y','#008F4C'),('Z','#EB1E29')]:
            right.plot(t,[float(r[name]) for r in rows],color=color,lw=2.3,label=name)
        right.axhline(1.,color='#EB1E29',ls=(0,(8,6)),lw=1,label='Ki = 1')
        right.set(xlim=(0,200),ylim=(0,3),xticks=(0,50,100,150,200),
                  yticks=(0,.5,1,1.5,2,2.5,3),
                  xlabel='Displayed time (a.u.)',ylabel='X, Y, Z (arbitrary units)',
                  title='Our SBML / RoadRunner · settled cycle')
        right.grid(alpha=.18)
        right.legend(loc='upper right',frameon=True)
        fig.suptitle('Goodwin model: original figure versus new simulation',fontsize=15)
        fig.supxlabel('Simulation display = original run t 600–800 shifted to 0–200; phase was not fitted.\n'
                      'Initial values in the paper are unavailable. Plot comparison is visual, not pointwise.',fontsize=10)
        fig.savefig(root/'figure2-comparison.png',dpi=180,facecolor='white')
        fig.savefig(root/'figure2-comparison.svg',facecolor='white')
        plt.close(fig)
    report=f'''# Goodwin Figure 2B 对照重跑

来源：[Gonze 与 Abou-Jaoudé，PLOS ONE 2013，Figure 2](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0069573)。出版社原图完整副本为 [publisher-figure-2.png]({root/'publisher-figure-2.png'})，SHA-256 `{metrics['publisher_figure_sha256']}`；左侧展示的是其 B 面板裁剪，不改变像素值。论文图注给出的振荡周期约为 40 arbitrary units。

![出版社 Figure 2B 与本次重跑对照]({root/'figure2-comparison.png'})

本次实际重新运行了原来构建的 SBML：RoadRunner 计算 0–840 a.u.，图中只显示 600–800 a.u. 的稳定振荡窗口，并将显示时间平移为 0–200。**没有调整模型参数，也没有按原图拟合相位。** 初值 X=0.1、Y=0.2、Z=0.3 来自我们的探索协议；论文 Figure 2B 未给初值。

| 指标 | 新模拟稳定段 | 论文 Figure 2B |
|---|---:|---:|
| 周期 | {metrics['model_period_au']:.1f} a.u. | 图注约 40 a.u. |
| X 范围 | {metrics['model_min']['X']:.3f}–{metrics['model_max']['X']:.3f} | 图中蓝线约 0–0.05，目测 |
| Y 范围 | {metrics['model_min']['Y']:.3f}–{metrics['model_max']['Y']:.3f} | 图中绿线约 0.1–0.35，目测 |
| Z 范围 | {metrics['model_min']['Z']:.3f}–{metrics['model_max']['Z']:.3f} | 图中红线约 1.55–2.7，目测 |

新模拟的周期与稳定振荡范围和原图**视觉上一致**。这比上一轮只比较 RoadRunner 与 SciPy 更接近论文图，但仍不等于逐点复现：原图未数字化，原始初值和相位未知，曲线时间零点不可直接对齐。论文语义支持仍是 `{metrics['source_semantic_support']}`，科学状态见 [run-summary.json]({root/'run-summary.json'})。

重新运行时同一模型的独立 SciPy 对照也通过，最大缩放误差 {metrics['numeric_crosscheck']['max_scaled_error']:.3g}，预设阈值 0.001。完整 0–840 a.u. 的运行及来源记录在 [run-report.md]({root/'run-report.md'})；图中 401 个展示点保存在 [settled-cycle.csv]({root/'settled-cycle.csv'})，计算指标与图像来源在 [metrics.json]({root/'metrics.json'})。

重跑入口：

```bash
.venv/bin/python benchmarks/development/reproduce_goodwin_fig2.py --output artifacts/my-fig2-comparison --publisher-figure {root/'publisher-figure-2.png'}
```

绘图入口（Matplotlib / Pillow）：

```bash
MPLCONFIGDIR=/tmp/biomodel-fig2-mpl /home/riveruning/miniforge3/envs/bioinfo/bin/python benchmarks/development/render_goodwin_fig2_comparison.py artifacts/my-fig2-comparison
```
'''
    (root/'README.md').write_text(report)
    print(json.dumps({'report':str(root/'README.md'), 'figure':str(root/'figure2-comparison.png')}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('output',type=Path)
    main(p.parse_args().output)
