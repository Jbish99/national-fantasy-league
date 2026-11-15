-- =====================================================================
-- db/schema/011_provisional_games.sql
-- Phase 6: provisional games.
--
-- The official game record (core.game) comes from the All-In-One workbook.
-- During the season the workbook can lag behind, so etl/load_core.py also adds
-- a finished regular-season week straight from Sleeper, marked
-- is_provisional = true (Jaden, 2026-11-15). Only for seasons whose official
-- scores come from Sleeper, only for weeks Sleeper has marked scored, and only
-- for teams the workbook doesn't already have a game for that week.
--
-- When the workbook gets that week, its row has the same game_id
-- (season-week-team1-team2) and simply replaces the provisional one, adding the
-- workbook-only details: Game of the Week, division / conference game, IST and
-- other designations. Until then those stay false / empty.
--
-- Run after 010. Safe to re-run.
-- =====================================================================

alter table core.game add column if not exists is_provisional boolean not null default false;

comment on column core.game.is_provisional is
    'true = this game came from Sleeper because the workbook doesn''t have it yet; the workbook version replaces it.';
