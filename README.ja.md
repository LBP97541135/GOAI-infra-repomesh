<div align="center">

<img src="docs/assets/logo.svg" alt="RepoMesh — 観測可能なデリバリー制御プレーン" width="820">

[English](README.md) · [简体中文](README.zh-CN.md) · **日本語**

![Apache 2.0](https://img.shields.io/badge/license-Apache_2.0-c99e52?style=flat-square&labelColor=16130d)
![Python 3.12+](https://img.shields.io/badge/python-3.12+-c99e52?style=flat-square&labelColor=16130d&logo=python&logoColor=e9dec2)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-c99e52?style=flat-square&labelColor=16130d&logo=fastapi&logoColor=e9dec2)
![PostgreSQL 17](https://img.shields.io/badge/PostgreSQL-17-c99e52?style=flat-square&labelColor=16130d&logo=postgresql&logoColor=e9dec2)
![React 19](https://img.shields.io/badge/React-19-c99e52?style=flat-square&labelColor=16130d&logo=react&logoColor=e9dec2)
![Vite 8](https://img.shields.io/badge/Vite-8-c99e52?style=flat-square&labelColor=16130d&logo=vite&logoColor=e9dec2)
![TypeScript 6](https://img.shields.io/badge/TypeScript-6-c99e52?style=flat-square&labelColor=16130d&logo=typescript&logoColor=e9dec2)
![Docker Compose](https://img.shields.io/badge/Docker-compose-c99e52?style=flat-square&labelColor=16130d&logo=docker&logoColor=e9dec2)

**複数リポジトリにまたがるコーディングエージェント・デリバリーのための、観測可能な制御プレーン。**
要求は計画になり、計画は実在のリポジトリで動くエージェントのチームになる。
その途上のゲートは、ひとつ残らず記録に残る。

|  |  |  |  |  |  |  |  |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **連携先** | Claude Code | OpenAI Codex | OpenCode | Cursor Agent | Copilot CLI | Aider | Goose |

*23 種のコーディングエージェント CLI が `src/repomesh/integrations/coding_agents/catalog.py` に
組み込まれている。ランタイムポートはベンダー中立なので、タスクを受け取れる CLI なら採用できる。*

</div>

---

RepoMesh は、複数リポジトリにまたがるコーディングエージェント・デリバリーのための観測可能な
制御プレーンです。プロジェクト、仕様、タスク、コンテキスト、検証、チェンジセット、リカバリ、
監査履歴を RepoMesh が所有します。AgentTeams は RepoMesh のファーストパーティ・ランタイム
制御プレーンで、チーム、ワーカー、スキル、メッセージ伝送を担当します。

![RepoMesh デリバリーコンソール：フラットな issue 一覧。各行はデリバリー段階を
バッジで表す —— 計画（マテリアライズ待ち）、実行中、リリース、一時停止、決裁待ち](docs/assets/console.svg)

残る 5 つの画面 —— レビューデスク、リポジトリ、チーム、エージェント、可観測性 —— は
[コンソール図解](docs/console-tour.md)にあります。

## コンソールを開く

以下のどちらでも、クローンしたてのリポジトリからブラウザ上のデリバリーコンソールまで、
手作業の設定なしで到達できます。**2 つの手順ではなく、2 つの選択肢**です。どちらもポート
8100 を使います。

**開発用ランチャー** —— ホットリロードあり。ホストに Docker、uv、Node 20+ が必要：

```powershell
.\scripts\dev-up.ps1                # -Seed でデモデータ、-NoBrowser で自動起動を抑止
```

```bash
./scripts/dev-up.sh                 # --seed, --no-browser
```

postgres を起動し、最新リビジョンまでマイグレートし、API を 8100、Vite を 5280 で提供して
`http://127.0.0.1:5280` を開きます。各段階はまず疎通を確認し、すでに動いているものは飛ばすので、
**このスクリプトを再実行するのが、動く状態に戻る通常の手段**です。しかも、自分が起動していない
ものを再起動・マイグレート・停止することは決してありません。`.\scripts\dev-down.ps1` /
`./scripts/dev-down.sh` はランチャーが起動した構成要素だけを、1 つずつ確認しながら落とします。

**フルスタック compose** —— ホストに必要なのは Docker だけ：

```bash
docker compose --profile console up -d --build
```

`http://127.0.0.1:8100` を開きます。nginx がビルド済みコンソールを配信し、`/api` を API
コンテナへリバースプロキシします。コンテナは起動時に自分専用のデータベースをマイグレートします
—— 同一オリジン、CORS なし、開発プロキシなし。起動後のデモデータ投入：

```bash
docker compose --profile console exec console-api python scripts/seed-console-demo.py
```

`REPOMESH_CONSOLE_PORT` でコンソールを 8100 以外へ、`REPOMESH_POSTGRES_PORT` で開発用
データベースを 5432 以外へ移せます。`docker compose --profile console down` で撤収し、
`-v` を付ければデータベースも削除します。

コンソールは**ログイン画面**から始まります。まっさらなデータベースにはアカウントが存在しない
ため、初回は「管理者を初期化」を通ります。資格情報は手元のマシンに留まります。2 つのプレーンは
**意図的に**認証方式が異なります。リードモデルは Bearer のアクショントークンを、人手による制御
（レビューデスク、チェックポイントの決裁）はセッションを受け取ります。エージェントのトークンでは
何も承認できないのは、このためです。

正直な現状：確認できているのは**再入経路**（構成要素がすべて稼働済みで、すべて飛ばされる経路）と
compose 構成です。空のマシンからのコールドスタートは、まだ端から端まで通していません。ある段階が
失敗し、そのメッセージが理由を説明できていなかったら、そう伝えてください —— そのメッセージも、
コマンドと同じくらい成果物です。

## 現在のマイルストーン

このリポジトリには、チーム、永続化、ランタイム統合、Context の 4 つの土台が入っています：

- モジュールを差し替え可能なアダプタへ結線するコンポジションルート。
- 機械可読なオーナーと境界を持つ 15 のビジネスモジュール。
- 動作する Repository Intelligence の垂直スライス。
- ベンダー中立な Agent Runtime ポート、23 の CLI アダプタ、7 シナリオのモックアダプタ。
- `components/agentteams` 配下に、バージョン固定の subtree として埋め込まれた AgentTeams v1.2.0 のソース。
- Runtime v1 の JSON 契約と、Python 版 RepoMesh Runner の実行基盤。
- バージョン付き Context オブジェクト、権限の積集合、不変バンドル、差分、アクセス監査。
- モジュール内蔵のビジネス API ルータと各プラットフォーム入口を、**振る舞いを持たない**
  トップレベルルータが集約。
- CODEOWNERS、プルリクエストのチェックリスト、アダプタ契約テスト、アーキテクチャテスト。
- PostgreSQL 永続化、Alembic マイグレーション、トランザクショナルイベント、監査、outbox、レディネス。

## ローカルで動かす

ここで起動するのはポート 8000 の v1 プラットフォーム API であって、デリバリーコンソール
**ではありません**。コンソールは 8100 で動く 2 つ目のインスタンスです。そちらは上の
「コンソールを開く」を参照してください。

```powershell
uv sync --extra dev
docker compose up -d postgres
uv run alembic upgrade head
uv run uvicorn repomesh.main:app --reload
```

`http://127.0.0.1:8000/docs` を開きます。フルのローカルプラットフォームが必要な場合は、
Docker と PowerShell 7+ を使い、リポジトリ同梱のインストーラから AgentTeams を導入して、
コンテナ化された RepoMesh API を起動します：

~~~powershell
.\scripts\start-platform.ps1 -InstallAgentTeams
~~~

Linux では：

~~~bash
./scripts/start-platform.sh --install-agentteams
~~~

フルプラットフォームは、RepoMesh の計画側と AgentTeams のエージェントの両方に、**同一の**
OpenAI 互換モデル接続を使います：

```dotenv
REPOMESH_MODEL_API_KEY=your-key
REPOMESH_MODEL_BASE_URL=https://api.deepseek.com/v1
REPOMESH_MODEL=deepseek-chat
```

高度な構成では、AgentTeams の `AGENTTEAMS_LLM_*` と RepoMesh 計画側の `REPOMESH_DEEPSEEK_*` を
個別に上書きできます。コーディングエージェント CLI の認証は、これとは別系統のままです。

起動スクリプトは、gitignore 済みの `.secrets/platform.env` に Runner・agent-action・MCP ゲート
ウェイの各トークンを生成し、AgentTeams コントローラのトークンを読み込み、Matrix のアクセス
トークンを取得します。初回起動時のレディネスは `GET /api/v1/setup/status`、インストール済み
CLI の認証状況は `GET /api/v1/setup/coding-agents` で確認します。リポジトリのスキャン後、
`POST /api/v1/repositories/{repository_id}/agent-team` がそのリポジトリの長期 Repository Leader、
既定 Worker、AgentTeams Team を作成します。デリバリーポリシーは `.env` を編集するのではなく、
`/api/v1/delivery` 配下の組織・リポジトリのポリシーエンドポイント経由で保存されます。

すべてのチェックを走らせるには：

```powershell
uv run ruff check .
uv run pytest
```

## ライセンス

RepoMesh は Apache License, Version 2.0 の下で提供されます。`LICENSE` を参照してください。

## ランチャーの中身と、手作業でのやり方

`scripts/dev-up.*` は、以下の 4 手順を順に実行し、それぞれの前に疎通確認を挟んだものです。
どれかの手順が失敗したとき、別の構成にしたいとき、あるいはランチャー自体を変更するときに、
この節を読んでください。

**なぜ 8100 なのか。**「ローカルで動かす」が起動するのは 8000 の v1 プラットフォーム API です。
`frontend/` 配下のデリバリーコンソールは、**それとは通信しません**。Vite 開発サーバは `/api` を
**同じアプリの 8100 上にある 2 つ目のインスタンス**へプロキシし、そのインスタンスがデリバリー
リードモデルとローカル ID エンドポイントを提供します。ポートは `frontend/vite.config.ts` に
書かれているため、ランチャーは 8100 と 5280 を固定値として扱います。

1. `docker compose up -d postgres` —— `REPOMESH_POSTGRES_PORT`（既定 5432）を公開します。
   既定の DSN `postgresql+asyncpg://repomesh:repomesh@localhost:5432/repomesh` と対応します。
2. `uv sync --extra dev` のあと `uv run alembic upgrade head` —— alembic は
   `REPOMESH_DATABASE_URL` を読むので、マイグレーションとサーバには**同じ値**を与える必要が
   あります。他人のものであるデータベースを、決してここに指定しないでください。
3. `frontend/.env.development` が期待するトークンでリードモデル用インスタンスを起動します。
   さもないとリードモデルへの呼び出しはすべて 401 になります：

   ```powershell
   $env:REPOMESH_AGENT_ACTION_TOKEN = "console-dev-token"
   uv run uvicorn repomesh.main:app --host 127.0.0.1 --port 8100
   ```

4. `cd frontend && npm install && npm run dev` のあと `http://127.0.0.1:5280` を開きます。

ここでのレディネス判定は `/docs`（またはルートからの任意の HTTP 応答）であって、
`/health/ready` では**ありません**。この最小構成ではレディネスが 503 を返すため、
健全なコンソールを壊れていると報告してしまいます。

`frontend/README.md`（「联调后端起法」）に、同じ手順と、デグレード時の注記、シードスクリプト、
データソース切り替えがまとまっています。

## チーム向けの入口

- ドキュメント目次：`docs/README.md`
- スキルのライフサイクル：`capabilities/skills/README.md`
- オープンソース化チェックリスト：`docs/open-source-readiness.md`
- サードパーティ表記：`THIRD_PARTY_NOTICES.md`
- 現フェーズの計画（全工程 GUI ループ、実装完了）：`docs/development/full-loop-plan-20260812.md`
- チーム引き継ぎ（アーキテクチャの節は有効。状況の節は更新済み）：`docs/development/team-handoff.md`
- 並行作業計画：`docs/development/parallel-work-plan.md`
- 公開契約：`docs/contracts/public-contracts-v0.1.md`
- デリバリーリードモデル契約：`docs/contracts/delivery-read-model-v0.1.md`
  （v0.2〜v0.4 は差分で、すべて有効）

- モジュールのオーナーと責務：`docs/architecture/module-map.md`
- 依存ルール：`docs/architecture/dependency-rules.md`
- Runtime planes：`docs/architecture/runtime-planes.md`
- データベース構築：`docs/database.md`
- データベースの所有権：`docs/architecture/database-ownership.md`
- チームのワークフロー：`docs/architecture/team-development.md`
- アーキテクチャ決定：`docs/adr/0001-independent-repomesh-core.md` と
  `docs/adr/0002-first-party-agentteams-runtime.md`

各モジュールは自分の schema と実装を所有します。利用側が import してよいのは、提供側の
`contracts` モジュールだけです。外部システムとファーストパーティのランタイムプロセスは、
`repomesh.integrations` 配下のアダプタを介してのみ境界を越えます。具体的な実装が選ばれるのは
`repomesh.bootstrap` の中だけです。
