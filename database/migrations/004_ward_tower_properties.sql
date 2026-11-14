-- 기존 256px 결계탑의 접지 앵커를 공용 속성으로 이관한다. 이미 편집된 값은 보존한다.
WITH inserted_tower AS (
    INSERT INTO content.building_properties(asset_id,anchor_x,anchor_y,display_adjustments)
    VALUES ('ward-tower-v1',0.5,0.8203125,'{}'::jsonb)
    ON CONFLICT (asset_id) DO NOTHING RETURNING asset_id
)
UPDATE content.generations SET current_generation=current_generation+1
WHERE singleton=true AND EXISTS(SELECT 1 FROM inserted_tower);
