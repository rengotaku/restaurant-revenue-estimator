# restaurant-revenue-estimator

飲食店 1 店舗を「独立した個人事業」として切り出し、公開情報・現地観察・統計・仮定から、売上と損益を根拠付きのシナリオとして算出する。結果は Web 画面と評価レポート（Markdown / JSON）で見る。

出力はシナリオであって実績でも予測でもない。実績データで誤差を測るまで、精度や信頼区間は表示しない。

## ドキュメント

- [ARCHITECTURE](docs/ARCHITECTURE.md) — 構成・データモデル・計算モデル・画面
- [ADR 一覧](docs/adr/README.md) — 設計判断の記録
- [元の spec](docs/spec/2026-10-30-original-spec.md) — 調査記録・要件・数理モデル・検証計画
- [spec の再検証レポート](docs/research/2026-10-30-spec-verification.md) — spec の数値と公開情報を一次情報で確かめた結果

## 開発

雛形は [my-boilerplate/python-web](https://github.com/rengotaku/my-boilerplate/tree/main/boilerplates/python-web)。操作は Makefile 経由で行う。

```bash
make help        # ターゲット一覧
make install     # Python (uv sync) と Node.js の依存を入れる
make run         # 127.0.0.1:8000 で開発サーバ (Tailwind watch つき)
make test        # pytest
make test-cov    # pytest + カバレッジ
make check       # lint + typecheck
make ci          # lint + typecheck + test-cov
make build       # Tailwind CSS を minify
make format      # ruff format + ruff check --fix
make migrate     # Alembic のマイグレーションを適用
```

CLI は `uv run rre --version` で動く。

CI は `.github/workflows/ci.yml`。
