"""
全レイヤー一括実行スクリプト

使い方:
  python src/run_all.py morning   # 朝：フルスキャン
  python src/run_all.py evening   # 夕：軽量更新 + ポートフォリオ処理
  python src/run_all.py both      # 両方
  python src/run_all.py           # デフォルト: both
"""

import subprocess
import sys
import os
from datetime import datetime

# 朝：全レイヤー実行
MORNING_SCRIPTS = [
    ("📰 ニュース取得 & センチメント分析", "src/fetch_news.py"),
    ("📈 トレンド分析（第2層）", "src/trend_analysis.py"),
    ("🌍 マクロ環境スコア（第4層）", "src/macro_score.py"),
    ("🏭 業界超過成長率", "src/industry_score.py"),
    ("🔍 バリュエーションギャップ分析（第5層）", "src/valuation_gap.py"),
    ("🏆 統合スクリーニング v3", "src/screener_v3.py"),
]

# 夕：ニュース・マクロ・スクリーニング（軽量更新）
EVENING_SCRIPTS = [
    ("📰 ニュース取得 & センチメント分析", "src/fetch_news.py"),
    ("🌍 マクロ環境スコア（第4層）", "src/macro_score.py"),
    ("🏆 統合スクリーニング v3", "src/screener_v3.py"),
]

project_dir = os.path.join(os.path.dirname(__file__), '..')


def _run(script_path, *args, timeout=600):
    cmd = [sys.executable, os.path.join(project_dir, script_path)] + list(args)
    try:
        result = subprocess.run(cmd, cwd=project_dir, timeout=timeout)
        if result.returncode != 0:
            print(f"  ⚠️ 終了コード: {result.returncode}")
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"  ⚠️ タイムアウト")
        return False
    except Exception as e:
        print(f"  ❌ エラー: {e}")
        return False


def run_scripts(scripts, label_mode, send_notify=True):
    print("=" * 60)
    print(f"🚀 全レイヤー一括実行 [{label_mode}]")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    failed = []
    for i, (label, script) in enumerate(scripts, 1):
        print(f"\n{'─' * 60}")
        print(f"[{i}/{len(scripts)}] {label}")
        print(f"{'─' * 60}")
        if not _run(script):
            failed.append(label)

    print(f"\n{'=' * 60}")
    if failed:
        print(f"⚠️ 一部失敗あり:")
        for f in failed:
            print(f"   - {f}")
    else:
        print(f"✅ {label_mode}実行完了！")
    print(f"{'=' * 60}")

    if send_notify:
        print(f"\n📱 LINE通知送信中...")
        _run('src/notify_line.py', 'morning', timeout=30)


def run_morning():
    run_scripts(MORNING_SCRIPTS, '朝（フルスキャン）')


def run_evening():
    # 1. スクリーニング更新（LINE通知はあとで）
    run_scripts(EVENING_SCRIPTS, '夕方（軽量更新）', send_notify=False)

    # 2. 売りシグナル判定 → 各シグナルをLINEへ自動送信
    print(f"\n{'─' * 60}")
    print("🔍 売りシグナル判定")
    print(f"{'─' * 60}")
    _run('src/sell_checker.py', timeout=120)

    # 3. 当日の朝推奨注文を DB に記録
    print(f"\n{'─' * 60}")
    print("📝 朝の推奨注文を記録")
    print(f"{'─' * 60}")
    _run('src/portfolio_mgr.py', 'record_buys', timeout=30)

    # 4. 保有銘柄の現在株価を更新
    print(f"\n{'─' * 60}")
    print("💹 保有銘柄の株価更新")
    print(f"{'─' * 60}")
    _run('src/portfolio_mgr.py', 'update_prices', timeout=60)

    # 5. portfolio.json エクスポート
    print(f"\n{'─' * 60}")
    print("📊 portfolio.json エクスポート")
    print(f"{'─' * 60}")
    _run('src/portfolio_mgr.py', 'export', timeout=30)

    # 6. 日次スナップショット記録
    print(f"\n{'─' * 60}")
    print("📸 ポートフォリオスナップショット")
    print(f"{'─' * 60}")
    _run('src/portfolio_mgr.py', 'snapshot', timeout=30)

    # 7. 夕方の運用サマリーをLINEへ
    print(f"\n{'─' * 60}")
    print("📱 LINE 運用サマリー送信")
    print(f"{'─' * 60}")
    _run('src/notify_line.py', 'evening', timeout=30)

    print(f"\n{'=' * 60}")
    print("✅ 夕方パイプライン完了！")
    print(f"{'=' * 60}")


def main():
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else 'both'
    if mode == 'morning':
        run_morning()
    elif mode == 'evening':
        run_evening()
    elif mode in ('both', 'all'):
        run_morning()
        run_evening()
    else:
        print("使い方: python src/run_all.py morning|evening|both")


if __name__ == '__main__':
    main()
