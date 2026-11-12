-- 기존 앵커 계약을 유지하면서 원장의 선택적 표시 크기 보정값을 보존한다.
ALTER TABLE content.building_properties ADD COLUMN display_adjustments jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK(jsonb_typeof(display_adjustments)='object');
