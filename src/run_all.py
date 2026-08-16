"""
全レイヤー一括実行スクリプト

5つの分析レイヤー + 統合スクリーニングを順番に実行。

使い方:
  python src/run_all.py
"""

import subprocess
import sys
import os
from datetime import datetime

SCRIPTS = [
    ("📰 ニュース取得 & センチメント分析", "src/fetch_news.py"),
    ("📈 トレンド分析（第2層）", "src/trend_analysis.py"),
    ("🌍 マクロ環境スコア（第4層）", "src/macro_score.py"),
    ("🏭 業界超過成長率", "src/industry_score.py"),
    ("🔍 バリュエーションギャップ分析（第5層）", "src/valuation_gap.py"),
    ("🏆 統合スクリーニング v3", "src/screener_v3.py"),
]

def main():
    print("=" * 60)
    print("🚀 全レイヤー一括実行")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    project_dir = os.path.join(os.path.dirname(__file__), '..')
    failed = []

    for i, (label, script) in enumerate(SCRIPTS, 1):
        print(f"\n{'─' * 60}")
        print(f"[{i}/{len(SCRIPTS)}] {label}")
        print(f"{'─' * 60}")

        script_path = os.path.join(project_dir, script)
        try:
            result = subprocess.run(
                [sys.executable, script_path],
                cwd=project_dir,
                timeout=300,  # 5分タイムアウト
            )
            if result.returncode != 0:
                print(f"  ⚠️ 終了コード: {result.returncode}")
                failed.append(label)
        except subprocess.TimeoutExpired:
            print(f"  ⚠️ タイムアウト（5分）")
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
        print(f"✅ 全レイヤー実行完了！")
    print(f"{'=' * 60}")

    # ページ再生成を提案
    print(f"\n💡 スクリーニング結果をサイトに反映するには:")
    print(f"   python src/generate_pages.py")
    print(f"   git add -A && git commit -m 'v3 screening update' && git push")


if __name__ == '__main__':
    main()
