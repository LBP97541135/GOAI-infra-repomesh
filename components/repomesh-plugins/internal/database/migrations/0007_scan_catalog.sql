-- 0007_scan_catalog.sql
-- Go 版重建：repositories 仓库档案表（扫描域并入定稿）
--
-- 规格来源：《repositories 表合并改动说明》+ 数据库重构方案 ② 的 repositories 规划。
-- 风格与 skill 插件一致：幂等自举，可重复执行。
-- 说明：本文件面向 Go 版新库自举；末段 ALTER 兼顾旧结构补齐。
--
-- 列归属纪律（写入方必须遵守）：
--   扫描代码只写扫描列（fingerprint / metadata / languages / profiled_at）；
--   操作者只维护 test_commands / test_paths，扫描永不覆盖；
--   轮询代码只写 poll 三列（poll_last_at / poll_next_at / poll_failures）。
--   扫描与轮询互不触碰对方的列。
--
-- 明确不新增 scan_status：失败扫描整行不入库，同名无新卡不写库，
-- 列里只可能是 ok，属死列。失败/跳过只记在扫描作业的进度计数里。

CREATE SCHEMA IF NOT EXISTS repository_intelligence;

CREATE TABLE IF NOT EXISTS repository_intelligence.repositories (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- 方案 ② 基础档案
    organization_id UUID NOT NULL,
    name            TEXT NOT NULL,
    url             TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    topics          JSONB NOT NULL DEFAULT '[]'::jsonb,
    languages       JSONB NOT NULL DEFAULT '[]'::jsonb,

    -- 扫描域 4 列
    fingerprint     TEXT NOT NULL DEFAULT '',
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    test_commands   JSONB NOT NULL DEFAULT '[]'::jsonb,
    test_paths      JSONB NOT NULL DEFAULT '[]'::jsonb,

    profiled_at     TIMESTAMPTZ,

    -- 交付域轮询游标（原样保留）
    poll_last_at    TIMESTAMPTZ,
    poll_next_at    TIMESTAMPTZ,
    poll_failures   INTEGER NOT NULL DEFAULT 0,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_repositories_name UNIQUE (name),
    CONSTRAINT uq_repositories_url  UNIQUE (url)
);

-- 旧结构补齐（新库执行时全部 skip）。必须先补列再建索引。
ALTER TABLE repository_intelligence.repositories ADD COLUMN IF NOT EXISTS organization_id UUID;
ALTER TABLE repository_intelligence.repositories ADD COLUMN IF NOT EXISTS fingerprint TEXT NOT NULL DEFAULT '';
ALTER TABLE repository_intelligence.repositories ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE repository_intelligence.repositories ADD COLUMN IF NOT EXISTS test_commands JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE repository_intelligence.repositories ADD COLUMN IF NOT EXISTS test_paths JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE repository_intelligence.repositories ADD COLUMN IF NOT EXISTS poll_last_at TIMESTAMPTZ;
ALTER TABLE repository_intelligence.repositories ADD COLUMN IF NOT EXISTS poll_next_at TIMESTAMPTZ;
ALTER TABLE repository_intelligence.repositories ADD COLUMN IF NOT EXISTS poll_failures INTEGER NOT NULL DEFAULT 0;

-- 旧数据兜底：空值补默认，列加默认
UPDATE repository_intelligence.repositories SET metadata = '{}'::jsonb WHERE metadata IS NULL;
UPDATE repository_intelligence.repositories SET test_commands = '[]'::jsonb WHERE test_commands IS NULL;
UPDATE repository_intelligence.repositories SET test_paths = '[]'::jsonb WHERE test_paths IS NULL;
UPDATE repository_intelligence.repositories SET languages = '[]'::jsonb WHERE languages IS NULL;
UPDATE repository_intelligence.repositories SET topics = '[]'::jsonb WHERE topics IS NULL;
UPDATE repository_intelligence.repositories SET description = '' WHERE description IS NULL;
UPDATE repository_intelligence.repositories SET fingerprint = '' WHERE fingerprint IS NULL;
ALTER TABLE repository_intelligence.repositories ALTER COLUMN metadata SET DEFAULT '{}'::jsonb;
ALTER TABLE repository_intelligence.repositories ALTER COLUMN test_commands SET DEFAULT '[]'::jsonb;
ALTER TABLE repository_intelligence.repositories ALTER COLUMN test_paths SET DEFAULT '[]'::jsonb;
ALTER TABLE repository_intelligence.repositories ALTER COLUMN languages SET DEFAULT '[]'::jsonb;
ALTER TABLE repository_intelligence.repositories ALTER COLUMN topics SET DEFAULT '[]'::jsonb;
ALTER TABLE repository_intelligence.repositories ALTER COLUMN description SET DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_repositories_organization
    ON repository_intelligence.repositories (organization_id);
CREATE INDEX IF NOT EXISTS idx_repositories_name
    ON repository_intelligence.repositories (name);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_repositories_name'
          AND conrelid = 'repository_intelligence.repositories'::regclass
    ) THEN
        ALTER TABLE repository_intelligence.repositories
            ADD CONSTRAINT uq_repositories_name UNIQUE (name);
    END IF;
END $$;
