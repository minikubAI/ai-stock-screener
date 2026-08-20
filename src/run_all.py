"""
全レイヤー一括実行スクリプト

使い方:
  python src/run_all.py morning   # 朝：フルスキャン
  python src/run_all.py evening   # 夕：軽量更新
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

# 夕：ニュース・マクロ・スクリーニングのみ（軽量更新）
EVENING_SCRIPTS = [
    ("📰 ニュース取得 & センチメント分析", "src/fetch_news.py"),
    ("🌍 マクロ環境スコア（第4層）", "src/macro_score.py"),
    ("🏆 統合スクリーニング v3", "src/screener_v3.py"),
]

project_dir = os.path.join(os.path.dirname(__file__), '..')


def run_scripts(scripts, label_mode):
    print("=" * 60)
    print(f"🚀 全レイヤー一括実行 [{label_mode}]")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    failed = []
    for i, (label, script) in enumerate(scripts, 1):
        print(f"\n{'─' * 60}")
        print(f"[{i}/{len(scripts)}] {label}")
        print(f"{'─' * 60}")

        script_path = os.path.join(project_dir, script)
        try:
            result = subprocess.run(
                [sys.executable, script_path],
                cwd=project_dir,
                timeout=600,
            )
            if result.returncode != 0:
                print(f"  ⚠️ 終了コード: {result.returncode}")
                failed.append(label)
        except subprocess.TimeoutExpired:
            print(f"  ⚠️ タイムアウト（10分）")
            failed.append(label)
        except Exception as e:
            print(f"  ❌ エラー: {e}")
            failed.append(label)

    print(f"\n{'=' * 60}")
    if failed:
        print(f"⚠️ 一部失敗あり:")
        for f in failed:
            print(f"   - {f}")
    else:
        print(f"✅ {label_mode}実行完了！")
    print(f"{'=' * 60}")

    # LINE通知
    print(f"\n📱 LINE通知送信中...")
    notify_script = os.path.join(project_dir, 'src', 'notify_line.py')
    try:
        subprocess.run([sys.executable, notify_script], cwd=project_dir, timeout=30)
    except Exception as e:
        print(f"  ⚠️ LINE通知失敗: {e}")


def run_morning():
    run_scripts(MORNING_SCRIPTS, '朝（フルスキャン）')


def run_evening():
    run_scripts(EVENING_SCRIPTS, '夕方（軽量更新）')


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
