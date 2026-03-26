#!/usr/bin/env python3
"""README용 차트 생성 스크립트"""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np
import os

OUT = os.path.dirname(os.path.abspath(__file__))

# ── 공통 스타일 ──────────────────────────────────────────────
plt.rcParams.update({
    'font.family': 'sans-serif',
    'font.sans-serif': ['Apple SD Gothic Neo', 'Malgun Gothic', 'NanumGothic', 'DejaVu Sans'],
    'axes.spines.top': False,
    'axes.spines.right': False,
    'figure.dpi': 180,
    'savefig.bbox': 'tight',
    'savefig.pad_inches': 0.15,
})

# 색상 팔레트
C_PG = '#336791'       # PostgreSQL 블루
C_ES = '#FEC514'       # Elasticsearch 옐로
C_QD = '#DC382C'       # Qdrant 레드
C_VS = '#6C4DC4'       # Vespa 퍼플
C_BM25 = '#2D6A4F'     # BM25 그린
C_DENSE = '#E07A5F'    # Dense 코랄
C_RRF = '#457B9D'      # RRF 블루
C_BAYES = '#A8DADC'    # Bayesian 라이트블루

COLORS_SYS = [C_PG, C_ES, C_QD, C_VS]


def save(fig, name):
    fig.savefig(os.path.join(OUT, name), facecolor='white', transparent=False)
    plt.close(fig)
    print(f"  saved: {name}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 1: Hybrid NDCG@10 — 시스템 비교
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def chart_hybrid_ndcg():
    systems = ['PostgreSQL', 'Elasticsearch', 'Qdrant', 'Vespa']
    miracl =  [0.7683, 0.7501, 0.6924, 0.4463]
    ezis =    [0.8641, 0.8769, 0.8394, 0.8125]

    x = np.arange(len(systems))
    w = 0.35

    fig, ax = plt.subplots(figsize=(8, 4.5))
    b1 = ax.bar(x - w/2, miracl, w, label='MIRACL (Wikipedia)', color=[c + 'CC' for c in COLORS_SYS], edgecolor=COLORS_SYS, linewidth=1.5)
    b2 = ax.bar(x + w/2, ezis, w, label='EZIS (Technical Docs)', color=COLORS_SYS, edgecolor=COLORS_SYS, linewidth=1.5)

    for bars in [b1, b2]:
        for bar in bars:
            h = bar.get_height()
            ax.text(bar.get_x() + bar.get_width()/2, h + 0.008, f'{h:.2f}',
                    ha='center', va='bottom', fontsize=8.5, fontweight='bold')

    ax.set_ylabel('NDCG@10', fontsize=11)
    ax.set_xticks(x)
    ax.set_xticklabels(systems, fontsize=10.5)
    ax.set_ylim(0, 1.05)
    ax.legend(loc='upper right', fontsize=9.5, framealpha=0.9)
    ax.set_title('Hybrid Search Quality by System', fontsize=13, fontweight='bold', pad=12)
    ax.yaxis.set_major_formatter(ticker.FormatStrFormatter('%.1f'))
    ax.axhline(y=0.75, color='gray', linestyle=':', alpha=0.3)

    save(fig, 'hybrid_ndcg.png')


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 2: Hybrid Latency p50 — 시스템 비교
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def chart_hybrid_latency():
    systems = ['PostgreSQL', 'Elasticsearch', 'Qdrant', 'Vespa']
    miracl_p50 = [1.79, 5.18, 4.54, 4.14]
    ezis_p50 =   [0.92, 3.43, 3.69, 3.69]

    x = np.arange(len(systems))
    w = 0.35

    fig, ax = plt.subplots(figsize=(8, 4.5))
    b1 = ax.bar(x - w/2, miracl_p50, w, label='MIRACL', color=[c + 'CC' for c in COLORS_SYS], edgecolor=COLORS_SYS, linewidth=1.5)
    b2 = ax.bar(x + w/2, ezis_p50, w, label='EZIS', color=COLORS_SYS, edgecolor=COLORS_SYS, linewidth=1.5)

    for bars in [b1, b2]:
        for bar in bars:
            h = bar.get_height()
            ax.text(bar.get_x() + bar.get_width()/2, h + 0.08, f'{h:.1f}ms',
                    ha='center', va='bottom', fontsize=8.5, fontweight='bold')

    ax.set_ylabel('p50 Latency (ms)', fontsize=11)
    ax.set_xticks(x)
    ax.set_xticklabels(systems, fontsize=10.5)
    ax.set_ylim(0, 7)
    ax.legend(loc='upper right', fontsize=9.5, framealpha=0.9)
    ax.set_title('Hybrid Search Latency (p50, lower is better)', fontsize=13, fontweight='bold', pad=12)

    save(fig, 'hybrid_latency.png')


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 3: 토크나이저가 BM25 품질에 미치는 영향
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def chart_tokenizer_impact():
    labels = ['MeCab\n(PostgreSQL)', 'nori\n(Elasticsearch)', 'ICU\n(Vespa)', 'charabia\n(Qdrant)']
    miracl = [0.6385, 0.61, 0.4093, 0.3574]
    ezis =   [0.9162, 0.932, 0.8091, 0.7721]
    colors = [C_PG, C_ES, C_VS, C_QD]

    x = np.arange(len(labels))
    w = 0.35

    fig, ax = plt.subplots(figsize=(8, 4.5))
    b1 = ax.bar(x - w/2, miracl, w, label='MIRACL', color=[c + '99' for c in colors], edgecolor=colors, linewidth=1.5)
    b2 = ax.bar(x + w/2, ezis, w, label='EZIS', color=colors, edgecolor=colors, linewidth=1.5)

    for bars in [b1, b2]:
        for bar in bars:
            h = bar.get_height()
            ax.text(bar.get_x() + bar.get_width()/2, h + 0.012, f'{h:.2f}',
                    ha='center', va='bottom', fontsize=8.5, fontweight='bold')

    # 구분선: 형태소 vs 비형태소
    ax.axvline(x=1.5, color='gray', linestyle='--', alpha=0.5)
    ax.text(0.75, 0.97, 'Morphological', ha='center', va='top', fontsize=9, color='#2D6A4F',
            fontweight='bold', transform=ax.get_xaxis_transform())
    ax.text(2.5, 0.97, 'Non-morphological', ha='center', va='top', fontsize=9, color='#999',
            fontweight='bold', transform=ax.get_xaxis_transform())

    ax.set_ylabel('BM25 NDCG@10', fontsize=11)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=9.5)
    ax.set_ylim(0, 1.1)
    ax.legend(loc='upper left', fontsize=9.5, framealpha=0.9)
    ax.set_title('Tokenizer Impact on Korean BM25 Quality', fontsize=13, fontweight='bold', pad=12)

    save(fig, 'tokenizer_impact.png')


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 4: 도메인 역전 — BM25 vs Dense
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def chart_domain_reversal():
    datasets = ['MIRACL\n(Wikipedia)', 'EZIS\n(Technical Docs)']
    bm25   = [0.6385, 0.9162]
    dense  = [0.7904, 0.8041]
    hybrid = [0.7683, 0.8641]

    x = np.arange(len(datasets))
    w = 0.25

    fig, ax = plt.subplots(figsize=(6.5, 4.5))
    b1 = ax.bar(x - w, bm25, w, label='BM25',
                color=C_BM25+'99', edgecolor=C_BM25, linewidth=1.5)
    b2 = ax.bar(x, dense, w, label='Dense',
                color=C_DENSE+'99', edgecolor=C_DENSE, linewidth=1.5)
    b3 = ax.bar(x + w, hybrid, w, label='Hybrid (RRF)',
                color=C_RRF+'99', edgecolor=C_RRF, linewidth=1.5)

    for bars in [b1, b2, b3]:
        for bar in bars:
            h = bar.get_height()
            ax.text(bar.get_x() + bar.get_width()/2, h + 0.012, f'{h:.2f}',
                    ha='center', va='bottom', fontsize=9, fontweight='bold')

    ax.set_ylabel('NDCG@10', fontsize=11)
    ax.set_xticks(x)
    ax.set_xticklabels(datasets, fontsize=10.5)
    ax.set_ylim(0.5, 1.05)
    ax.legend(loc='lower center', fontsize=9.5, framealpha=0.9, ncol=3)
    ax.set_title('Domain Reversal: BM25 vs Dense vs Hybrid', fontsize=13, fontweight='bold', pad=12)

    save(fig, 'domain_reversal.png')


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 5: PG 내부 BM25 스케일링 (1K → 10K → 100K)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def chart_scaling():
    scales = ['1K', '10K', '100K']
    x = np.arange(len(scales))

    pg_textsearch = [0.40, 0.42, 0.62]
    vectorchord =   [1.10, 1.35, 3.58]
    plpgsql =       [2.31, 10.35, 85.58]

    fig, ax = plt.subplots(figsize=(7, 4.5))

    ax.semilogy(x, pg_textsearch, 'o-', color=C_PG, linewidth=2.5, markersize=8, label='pg_textsearch BM25', zorder=3)
    ax.semilogy(x, vectorchord, 's-', color=C_QD, linewidth=2, markersize=7, label='VectorChord-BM25', zorder=2)
    ax.semilogy(x, plpgsql, '^-', color='#999', linewidth=1.5, markersize=7, label='pl/pgsql BM25', zorder=1)

    for vals, offset in [(pg_textsearch, -0.18), (vectorchord, -0.18), (plpgsql, 0.15)]:
        for i, v in enumerate(vals):
            ax.text(i + 0.08, v * (1 + offset), f'{v}ms', fontsize=8.5, fontweight='bold', va='center')

    ax.set_xticks(x)
    ax.set_xticklabels(scales, fontsize=11)
    ax.set_xlabel('Corpus Size', fontsize=11)
    ax.set_ylabel('p50 Latency (ms, log scale)', fontsize=11)
    ax.set_ylim(0.2, 200)
    ax.legend(loc='upper left', fontsize=9.5, framealpha=0.9)
    ax.set_title('BM25 Latency Scaling: 1K → 100K Documents', fontsize=13, fontweight='bold', pad=12)
    ax.grid(True, alpha=0.2, which='both')

    save(fig, 'scaling.png')


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if __name__ == '__main__':
    print("Generating charts...")
    chart_hybrid_ndcg()
    chart_hybrid_latency()
    chart_tokenizer_impact()
    chart_domain_reversal()
    chart_scaling()
    print("Done!")
